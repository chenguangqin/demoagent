"""核心数据模型：EmailEvent（监听器 → 工作流的接口契约）与 ThreadState（线程物化状态）。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Category(str, Enum):
    SPAM = "spam"
    SIMPLE = "simple"
    COMPLEX = "complex"
    URGENT = "urgent"


class ThreadStatus(str, Enum):
    OPEN = "open"                    # 新开 / 客户来信待处理
    AWAITING_CUSTOMER = "await_cust"  # 我方已回，等客户
    AWAITING_INTERNAL = "await_int"   # 已升级，等内部处理
    RESOLVED = "resolved"
    SPAM = "spam"


# sweep（定时跟进器）只关心这些活跃状态；partial index 也据此收窄
ACTIVE_STATUSES = (
    ThreadStatus.OPEN,
    ThreadStatus.AWAITING_CUSTOMER,
    ThreadStatus.AWAITING_INTERNAL,
)


@dataclass
class EmailEvent:
    """监听器把原始邮件规整成的结构化事件，是工作流的唯一输入。"""

    message_id: str                       # RFC Message-ID —— 幂等键
    thread_id: str                        # 归一化后的线程标识（见 thread/resolve.py）
    from_addr: str
    to_addrs: list[str] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""                   # 纯文本正文（HTML 已净化）
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    received_at: float = field(default_factory=time.time)
    auth_results: dict = field(default_factory=dict)  # SPF/DKIM/DMARC

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ThreadState:
    """线程当前状态 —— 物化视图，高频原地更新 + 随机检索。"""

    thread_id: str
    subject_norm: str = ""
    participants: set[str] = field(default_factory=set)
    messages: list[dict] = field(default_factory=list)  # 精简往来 [{dir,from,ts,summary}]
    status: ThreadStatus = ThreadStatus.OPEN
    category: str = ""
    last_inbound_at: float = 0.0
    last_outbound_at: float = 0.0
    sla_due_at: float | None = None
    followup_count: int = 0
    ticket_id: str | None = None
    open_todos: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["participants"] = sorted(self.participants)
        d["status"] = self.status.value
        return d
