"""外部副作用适配器：发信、知识库检索、升级上报。

均以协议（Protocol）定义接口，附内存/Fake 实现，便于离线运行与测试。
生产替换为真实 SMTP/IMAP、向量库、工单/Slack。
"""
from __future__ import annotations

import time
from typing import Protocol


class Mailer(Protocol):
    def reply(self, thread_id: str, subject: str, body: str, to_addr: str = "") -> str: ...


class KnowledgeBase(Protocol):
    def search(self, query: str, top_k: int = 4) -> str: ...


class Escalator(Protocol):
    def open_ticket(self, thread_id: str, summary: str, draft: str, priority: str) -> str: ...


# ---------------- Fake 实现（离线/测试） ----------------

class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def reply(self, thread_id: str, subject: str, body: str, to_addr: str = "") -> str:
        msg_id = f"<out-{len(self.sent)+1}-{int(time.time())}@agent>"
        self.sent.append({"message_id": msg_id, "thread_id": thread_id,
                          "subject": subject, "body": body, "to": to_addr})
        return msg_id


class FakeKnowledgeBase:
    def __init__(self, faqs: dict[str, str] | None = None) -> None:
        # 关键词 → 答案
        self.faqs = faqs or {
            "退款": "退款将在 3-5 个工作日内原路退回。",
            "refund": "Refunds are processed within 3-5 business days.",
            "营业时间": "客服工作时间为周一至周五 9:00-18:00。",
            "hours": "Support hours are Mon-Fri 9:00-18:00.",
            "密码": "可在登录页点击“忘记密码”重置。",
            "password": "Use the 'Forgot password' link on the sign-in page to reset.",
        }

    def search(self, query: str, top_k: int = 4) -> str:
        q = query.lower()
        hits = [ans for kw, ans in self.faqs.items() if kw.lower() in q]
        return "\n".join(hits[:top_k]) if hits else ""


class FakeEscalator:
    def __init__(self) -> None:
        self.tickets: list[dict] = []

    def open_ticket(self, thread_id: str, summary: str, draft: str, priority: str) -> str:
        tid = f"TICKET-{len(self.tickets)+1}"
        self.tickets.append({"ticket_id": tid, "thread_id": thread_id,
                            "summary": summary, "draft": draft, "priority": priority})
        return tid
