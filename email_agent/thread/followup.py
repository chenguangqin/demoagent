"""定时跟进器（设计文档 §5.4）。

周期扫描 Thread Store：
- 等客户回复且静默超阈值 → 自动催单（受 MAX_FOLLOWUP 限制）；
- SLA 到期且未解决 → 升级上报。

检索走索引（due_threads / idle_awaiting_customer），非全表扫描。
可注册为 KiroCrew cron / EventBridge / K8s CronJob。
"""
from __future__ import annotations

import time

from ..common.models import ThreadStatus
from ..thread.store import ThreadStore
from ..tools.adapters import Mailer, Escalator
from .. import config


def _render_followup(thread) -> str:
    return ("您好，\n\n关于您之前的咨询，我们尚未收到回复。请问问题是否已解决？"
            "如仍需协助，请直接回复本邮件。\n\n祝好\n客服团队")


def follow_up_sweep(store: ThreadStore, mailer: Mailer, escalator: Escalator,
                    now: float | None = None) -> dict:
    now = now or time.time()
    stats = {"followed_up": 0, "escalated_sla": 0}

    # 1) 等客户静默超阈值 → 催单
    idle_before = now - config.FOLLOWUP_IDLE_SECONDS
    for t in store.idle_awaiting_customer(idle_before):
        if t.followup_count >= config.MAX_FOLLOWUP:
            # 超过催单上限 → 转人工关单
            tid = escalator.open_ticket(t.thread_id, "多次催单无回应，请人工跟进", "", "normal")
            t.ticket_id = tid
            t.status = ThreadStatus.AWAITING_INTERNAL
            store.upsert(t)
            store.append_event(t.thread_id, "escalated", {"reason": "max_followup"})
            continue
        mid = mailer.reply(t.thread_id, f"Re: {t.subject_norm}", _render_followup(t))
        t.followup_count += 1
        t.last_outbound_at = now
        store.upsert(t)
        store.append_event(t.thread_id, "followup", {"message_id": mid, "count": t.followup_count})
        stats["followed_up"] += 1

    # 2) SLA 到期 → 升级
    for t in store.due_threads(now):
        tid = escalator.open_ticket(t.thread_id, "SLA 已到期，请优先处理", "", "high")
        t.ticket_id = tid
        t.sla_due_at = None
        t.status = ThreadStatus.AWAITING_INTERNAL
        store.upsert(t)
        store.append_event(t.thread_id, "escalated", {"reason": "sla_due"})
        stats["escalated_sla"] += 1

    return stats
