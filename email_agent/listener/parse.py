"""原始邮件 → EmailEvent：解析头/正文/附件，净化 HTML。"""
from __future__ import annotations

import email
import time
from email.message import Message
from email.utils import getaddresses, parseaddr

from ..common.models import EmailEvent
from ..common.security import strip_html


def _extract_body(msg: Message) -> str:
    if msg.is_multipart():
        # 优先 text/plain，其次净化后的 text/html
        plain, html = None, None
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            try:
                payload = part.get_payload(decode=True)
                text = payload.decode(part.get_content_charset() or "utf-8", "replace") if payload else ""
            except (LookupError, UnicodeDecodeError):
                text = ""
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
        if plain:
            return plain.strip()
        return strip_html(html or "")
    payload = msg.get_payload(decode=True)
    raw = payload.decode(msg.get_content_charset() or "utf-8", "replace") if payload else msg.get_payload()
    return strip_html(raw) if msg.get_content_type() == "text/html" else (raw or "").strip()


def parse_to_event(raw_bytes: bytes | str) -> EmailEvent:
    msg = email.message_from_bytes(raw_bytes) if isinstance(raw_bytes, bytes) \
        else email.message_from_string(raw_bytes)

    refs = (msg.get("References") or "").split()
    to_addrs = [a for _, a in getaddresses([msg.get("To", "")]) if a]
    return EmailEvent(
        message_id=(msg.get("Message-ID") or "").strip(),
        thread_id="",                          # 由 resolve_thread_id 归一化
        from_addr=parseaddr(msg.get("From", ""))[1],
        to_addrs=to_addrs,
        subject=msg.get("Subject", ""),
        body_text=_extract_body(msg),
        in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
        references=refs,
        received_at=time.time(),
    )
