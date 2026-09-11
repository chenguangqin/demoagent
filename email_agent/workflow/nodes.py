"""Strands Graph 节点封装。

- FunctionNode：把确定性 Python 函数包成 MultiAgentBase 节点（不调用 LLM）。
- 各节点闭包共享一个 RunContext（本封邮件的 evt / thread / 适配器 / 结果）。

Graph 的 node 输出会作为下游 node 的输入，但邮件流水线的真正状态在 RunContext 里流转；
node 的文本返回值只用于可读的执行轨迹。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..common.models import EmailEvent, ThreadState, ThreadStatus, Category
from ..common.security import normalize_subject, redact_pii
from ..thread.store import ThreadStore
from ..thread.resolve import resolve_thread_id
from ..tools.adapters import Mailer, KnowledgeBase, Escalator
from .. import config
from . import triage as triage_mod
from .reply_agent import auto_reply
from .escalate import escalate as escalate_fn


@dataclass
class RunContext:
    """一次邮件处理运行的共享状态，被各确定性节点闭包读写。"""
    evt: EmailEvent
    store: ThreadStore
    mailer: Mailer
    kb: KnowledgeBase
    escalator: Escalator
    thread: ThreadState | None = None
    history_ctx: str = ""
    verdict: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    final_status: ThreadStatus = ThreadStatus.OPEN
    duplicate: bool = False


class FunctionNode:
    """把确定性函数作为 Strands Graph 节点（继承 MultiAgentBase）。

    延迟导入 strands，使本模块在无 SDK 环境也能被 import（供测试/回退）。
    """

    def __init__(self, func, name: str):
        self.func = func
        self.name = name
        self._impl = None

    def _build_impl(self):
        from strands.multiagent.base import MultiAgentBase, NodeResult, Status, MultiAgentResult
        from strands.agent.agent_result import AgentResult
        from strands.types.content import ContentBlock, Message

        func, name = self.func, self.name

        class _Node(MultiAgentBase):
            def __init__(self):
                super().__init__()

            async def invoke_async(self, task, invocation_state, **kwargs):
                out = func(task)
                agent_result = AgentResult(
                    stop_reason="end_turn",
                    message=Message(role="assistant", content=[ContentBlock(text=str(out))]),
                )
                return MultiAgentResult(
                    status=Status.COMPLETED,
                    results={name: NodeResult(result=agent_result)},
                )

        return _Node()

    def as_executor(self):
        if self._impl is None:
            self._impl = self._build_impl()
        return self._impl


# ---------------- 确定性节点函数（闭包绑定 RunContext） ----------------

def make_nodes(ctx: RunContext) -> dict:
    """返回 {node_name: callable(task)->str}，供 FunctionNode 包裹。"""

    def thread_ctx(task):
        evt = ctx.evt
        if evt.message_id and ctx.store.message_seen(evt.message_id):
            ctx.duplicate = True
            ctx.result = {"action": "skipped_duplicate", "message_id": evt.message_id}
            return "duplicate"
        evt.thread_id = resolve_thread_id(evt, ctx.store)
        subj_norm = normalize_subject(evt.subject)
        thread = ctx.store.load_or_new(evt.thread_id, subj_norm)
        thread.participants.update([evt.from_addr, *evt.to_addrs])
        ctx.history_ctx = "\n".join(
            f"[{'客户' if m.get('dir') == 'in' else '我方'}] {m.get('summary', '')}"
            for m in thread.messages[-6:]
        )
        ctx.store.append_event(evt.thread_id, "inbound", evt.to_dict(), message_id=evt.message_id)
        thread.last_inbound_at = evt.received_at or time.time()
        thread.messages.append({"dir": "in", "from": evt.from_addr,
                                "ts": thread.last_inbound_at, "summary": redact_pii(evt.subject)})
        ctx.thread = thread
        return "loaded"

    def triage(task):
        evt = ctx.evt
        if triage_mod.rule_is_spam(evt):
            ctx.verdict = {"category": Category.SPAM.value, "confidence": 1.0, "reason": "rule"}
            return Category.SPAM.value
        v = triage_mod.classify(evt, ctx.history_ctx)
        ctx.store.append_event(evt.thread_id, "classified", v, message_id=evt.message_id)
        ctx.thread.category = v["category"]
        if v["confidence"] < config.MIN_CONFIDENCE:
            v = {**v, "category": Category.COMPLEX.value, "reason": v["reason"] + " (低置信度)"}
        ctx.verdict = v
        return v["category"]

    def archive_spam(task):
        ctx.result = {"action": "archived_spam"}
        ctx.final_status = ThreadStatus.SPAM
        return "archived"

    def do_reply(task):
        res = auto_reply(ctx.evt, ctx.thread, ctx.history_ctx, ctx.mailer, ctx.kb, ctx.escalator)
        ctx.result = res
        ctx.final_status = (ThreadStatus.AWAITING_CUSTOMER if res.get("action") == "replied"
                            else ThreadStatus.AWAITING_INTERNAL if res.get("action") == "escalated"
                            else ThreadStatus.OPEN)
        return res.get("action", "unknown")

    def do_escalate(task):
        cat = ctx.verdict.get("category")
        priority = "high" if cat == Category.URGENT.value else "normal"
        ctx.result = escalate_fn(ctx.evt, ctx.verdict, ctx.escalator, priority=priority)
        ctx.final_status = ThreadStatus.AWAITING_INTERNAL
        return "escalated"

    def persist(task):
        if ctx.duplicate:
            return "skipped"
        now = time.time()
        t, res = ctx.thread, ctx.result
        t.status = ctx.final_status
        if res.get("action") == "replied":
            t.last_outbound_at = now
            t.messages.append({"dir": "out", "from": "agent", "ts": now, "summary": "自动回复"})
            ctx.store.append_event(ctx.evt.thread_id, "outbound", res, message_id=res.get("detail"))
            t.sla_due_at = None
        elif res.get("action") == "escalated":
            t.ticket_id = res.get("detail")
            t.sla_due_at = now + config.SLA_SECONDS
            ctx.store.append_event(ctx.evt.thread_id, "escalated", res)
        elif res.get("action") == "archived_spam":
            t.sla_due_at = None
        else:
            t.sla_due_at = now + config.SLA_SECONDS
        ctx.store.upsert(t)
        return "persisted"

    return {"thread_ctx": thread_ctx, "triage": triage, "archive_spam": archive_spam,
            "auto_reply": do_reply, "escalate": do_escalate, "persist": persist}
