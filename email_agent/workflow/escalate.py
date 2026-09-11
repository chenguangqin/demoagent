"""升级节点：生成摘要 + 草稿，开工单/通知负责人。"""
from __future__ import annotations

from ..common.models import EmailEvent
from ..tools.adapters import Escalator


def escalate(evt: EmailEvent, verdict: dict, escalator: Escalator,
             priority: str = "normal") -> dict:
    summary = (f"[{verdict.get('category')}] {evt.subject} — 来自 {evt.from_addr}. "
               f"分类理由: {verdict.get('reason', '')}")
    draft = evt.body_text[:2000]
    tid = escalator.open_ticket(evt.thread_id, summary, draft, priority)
    return {"action": "escalated", "detail": tid, "priority": priority}
