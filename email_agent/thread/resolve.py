"""线程归一化：把一封邮件可靠地归到既有线程或开一条新线程。

优先级（设计文档 §5.2）：
  1. References 头链 → 取根 Message-ID
  2. In-Reply-To → 回溯根
  3. 供应商 threadId（若监听器已填 thread_id 则直接用）
  4. 兜底：归一化主题 + 参与者 + 时间窗
"""
from __future__ import annotations

from ..common.models import EmailEvent
from ..common.security import normalize_subject
from .store import ThreadStore


def resolve_thread_id(evt: EmailEvent, store: ThreadStore) -> str:
    # 0) 监听器（如 Gmail API）已给出可靠 threadId
    if evt.thread_id:
        return evt.thread_id

    # 1) References 头链：第一个通常是线程根
    if evt.references:
        return evt.references[0]

    # 2) In-Reply-To
    if evt.in_reply_to:
        return evt.in_reply_to

    # 3) 兜底：主题 + 参与者 + 时间窗
    subj = normalize_subject(evt.subject)
    parts = [evt.from_addr, *evt.to_addrs]
    match = store.match_recent_by_subject(subj, parts)
    if match:
        return match

    # 4) 全新线程：以本邮件 Message-ID 作根
    return evt.message_id
