"""顶层编排器：thread_ctx → triage → 条件分支 → persist（含线程状态机）。

这是"确定性 Workflow"部分；auto_reply 分支内部嵌 ReAct Agent。
"""
from __future__ import annotations

import time

from ..common.models import EmailEvent, ThreadState, ThreadStatus, Category
from ..common.security import normalize_subject, redact_pii
from ..thread.store import ThreadStore
from ..thread.resolve import resolve_thread_id
from ..tools.adapters import Mailer, KnowledgeBase, Escalator
from .. import config
from . import triage as triage_mod
from .reply_agent import auto_reply
from .escalate import escalate


def _render_history(thread: ThreadState, limit: int = 6) -> str:
    lines = []
    for m in thread.messages[-limit:]:
        who = "客户" if m.get("dir") == "in" else "我方"
        lines.append(f"[{who}] {m.get('summary', '')}")
    return "\n".join(lines)


class EmailWorkflow:
    def __init__(self, store: ThreadStore, mailer: Mailer, kb: KnowledgeBase, escalator: Escalator):
        self.store = store
        self.mailer = mailer
        self.kb = kb
        self.escalator = escalator

    def run(self, evt: EmailEvent) -> dict:
        # 幂等：同一 Message-ID 只处理一次
        if evt.message_id and self.store.message_seen(evt.message_id):
            return {"action": "skipped_duplicate", "message_id": evt.message_id}

        # 1) thread_ctx：归一化 + 载入线程
        evt.thread_id = resolve_thread_id(evt, self.store)
        subj_norm = normalize_subject(evt.subject)
        thread = self.store.load_or_new(evt.thread_id, subj_norm)
        thread.participants.update([evt.from_addr, *evt.to_addrs])
        history_ctx = _render_history(thread)

        # 记录入站事件（append-only）+ 更新入站时间/往来
        self.store.append_event(evt.thread_id, "inbound", evt.to_dict(), message_id=evt.message_id)
        thread.last_inbound_at = evt.received_at or time.time()
        thread.messages.append({"dir": "in", "from": evt.from_addr,
                                "ts": thread.last_inbound_at,
                                "summary": redact_pii(evt.subject)})

        # 2) 规则前置过滤
        if triage_mod.rule_is_spam(evt):
            return self._finish(evt, thread, {"category": Category.SPAM.value,
                                              "confidence": 1.0, "reason": "rule"},
                                {"action": "archived_spam"}, ThreadStatus.SPAM)

        # 3) LLM/桩分类
        verdict = triage_mod.classify(evt, history_ctx)
        self.store.append_event(evt.thread_id, "classified", verdict, message_id=evt.message_id)
        thread.category = verdict["category"]

        if verdict["confidence"] < config.MIN_CONFIDENCE:
            res = escalate(evt, verdict, self.escalator, priority="normal")
            return self._finish(evt, thread, verdict, res, ThreadStatus.AWAITING_INTERNAL)

        # 4) 条件分支
        cat = verdict["category"]
        if cat == Category.SPAM.value:
            return self._finish(evt, thread, verdict, {"action": "archived_spam"}, ThreadStatus.SPAM)

        if cat == Category.SIMPLE.value:
            res = auto_reply(evt, thread, history_ctx, self.mailer, self.kb, self.escalator)
            new_status = self._status_after_reply(res)
            return self._finish(evt, thread, verdict, res, new_status)

        # complex / urgent
        priority = "high" if cat == Category.URGENT.value else "normal"
        res = escalate(evt, verdict, self.escalator, priority=priority)
        return self._finish(evt, thread, verdict, res, ThreadStatus.AWAITING_INTERNAL)

    # ---------- 状态机 & persist ----------
    def _status_after_reply(self, res: dict) -> ThreadStatus:
        if res.get("action") == "replied":
            return ThreadStatus.AWAITING_CUSTOMER   # 已回，等客户
        if res.get("action") == "escalated":
            return ThreadStatus.AWAITING_INTERNAL
        return ThreadStatus.OPEN                     # 草稿待审 → 仍未闭环

    def _finish(self, evt, thread: ThreadState, verdict: dict, res: dict,
                status: ThreadStatus) -> dict:
        now = time.time()
        thread.status = status

        if res.get("action") == "replied":
            thread.last_outbound_at = now
            thread.messages.append({"dir": "out", "from": "agent", "ts": now,
                                    "summary": "自动回复"})
            self.store.append_event(evt.thread_id, "outbound", res, message_id=res.get("detail"))
            thread.sla_due_at = None                 # 已首次响应，清 SLA
        elif res.get("action") == "escalated":
            thread.ticket_id = res.get("detail")
            thread.sla_due_at = now + config.SLA_SECONDS
            self.store.append_event(evt.thread_id, "escalated", res)
        elif res.get("action") in ("archived_spam",):
            thread.sla_due_at = None
        else:  # draft / 其他未闭环 → 设 SLA 计时，等人审
            thread.sla_due_at = now + config.SLA_SECONDS

        self.store.upsert(thread)
        return {"thread_id": evt.thread_id, "category": verdict.get("category"),
                "confidence": verdict.get("confidence"), "status": status.value, **res}
