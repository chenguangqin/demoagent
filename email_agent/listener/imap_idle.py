"""IMAP IDLE 监听器（设计文档 §3.2）。

常驻进程：连接邮箱、IDLE 等待新邮件、解析为 EmailEvent、入队交给 Worker。
仅依赖标准库 imaplib；生产建议加断线重连、TLS、错误退避。

注意：这是常驻监听进程，**不是**工作流的一部分——它只负责"感知并入队"。
"""
from __future__ import annotations

import imaplib
import time
from typing import Callable

from ..common.models import EmailEvent
from .parse import parse_to_event


class ImapIdleListener:
    def __init__(self, host: str, user: str, password: str,
                 mailbox: str = "INBOX", poll_interval: float = 30.0):
        self.host = host
        self.user = user
        self.password = password
        self.mailbox = mailbox
        self.poll_interval = poll_interval
        self._seen_uids: set[bytes] = set()

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self.host)
        conn.login(self.user, self.password)
        conn.select(self.mailbox)
        return conn

    def _fetch_new(self, conn: imaplib.IMAP4_SSL, on_event: Callable[[EmailEvent], None]) -> None:
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK":
            return
        for uid in data[0].split():
            if uid in self._seen_uids:
                continue
            typ, msg_data = conn.fetch(uid, "(RFC822)")
            if typ == "OK" and msg_data and msg_data[0]:
                evt = parse_to_event(msg_data[0][1])
                on_event(evt)
            self._seen_uids.add(uid)

    def run(self, on_event: Callable[[EmailEvent], None]) -> None:
        """阻塞式监听循环。on_event 通常是 queue.put。"""
        while True:
            try:
                conn = self._connect()
                while True:
                    self._fetch_new(conn, on_event)
                    # 简化版：轮询代替真正的 IDLE（imaplib 无原生 IDLE）。
                    # 生产可用 imaplib2 / aioimaplib 的 idle() 获得秒级推送。
                    time.sleep(self.poll_interval)
                    conn.noop()
            except (imaplib.IMAP4.error, OSError) as e:  # 断线 → 退避重连
                print(f"[listener] 连接异常，5s 后重连: {e}")
                time.sleep(5)
