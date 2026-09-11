"""安全与净化：提示注入防护、HTML 剥离、主题归一化、PII 脱敏。"""
from __future__ import annotations

import re

_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"[ \t]+")
_RE_SUBJECT_PREFIX = re.compile(r"^\s*((re|fwd|fw|答复|转发)\s*:\s*)+", re.IGNORECASE)
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def strip_html(html: str) -> str:
    """极简 HTML → 文本（生产可换 bleach/BeautifulSoup）。"""
    text = _RE_TAG.sub(" ", html or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = _RE_WS.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def normalize_subject(subject: str) -> str:
    """去掉 Re:/Fwd: 等前缀并归一化空白，用于兜底线程匹配。"""
    s = _RE_SUBJECT_PREFIX.sub("", subject or "")
    return _RE_WS.sub(" ", s).strip().lower()


def wrap_untrusted(body: str) -> str:
    """把邮件正文包成"不可信数据"，交给 LLM 时明确其边界，缓解提示注入。"""
    return f"<<<UNTRUSTED_EMAIL_BODY\n{body}\nUNTRUSTED_EMAIL_BODY>>>"


def redact_pii(text: str) -> str:
    """日志脱敏：邮箱打码。"""
    return _RE_EMAIL.sub(lambda m: m.group(0)[:2] + "***@***", text or "")
