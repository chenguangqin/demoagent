"""线程持久化 —— Event Sourcing + 物化视图（SQLite 起步）。

设计要点（对应设计文档 §5 与"存储权衡"讨论）：
- `events` 表：**append-only**，只 INSERT，是审计/可重放的事实来源。
- `threads` 表：**物化状态**，UPSERT 原地更新，供高频随机检索。
- sweep 的检索走 `(status, sla_due_at)` 复合索引，范围扫描而非全表 O(n)。
- WAL 模式：多读单写、并发友好。

生产可把该类替换为 DynamoDB / PostgreSQL 后端，接口保持不变。
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Iterable

from ..common.models import ThreadState, ThreadStatus, ACTIVE_STATUSES

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,   -- 全局单调序，天然 append 顺序
    thread_id  TEXT NOT NULL,
    message_id TEXT,
    kind       TEXT NOT NULL,                        -- inbound / outbound / classified / escalated / followup
    ts         REAL NOT NULL,
    payload    TEXT NOT NULL                         -- JSON
);
CREATE INDEX IF NOT EXISTS idx_events_thread ON events(thread_id, seq);

CREATE TABLE IF NOT EXISTS threads (
    thread_id      TEXT PRIMARY KEY,
    subject_norm   TEXT,
    participants   TEXT,        -- JSON list
    messages       TEXT,        -- JSON list（精简往来）
    status         TEXT NOT NULL,
    category       TEXT,
    last_inbound_at  REAL,
    last_outbound_at REAL,
    sla_due_at     REAL,
    followup_count INTEGER NOT NULL DEFAULT 0,
    ticket_id      TEXT,
    open_todos     TEXT          -- JSON list
);

-- sweep 的关键索引：只覆盖活跃线程，按到期时间范围扫描（partial index）
CREATE INDEX IF NOT EXISTS idx_threads_due
    ON threads(status, sla_due_at)
    WHERE status IN ('open','await_cust','await_int');

-- 兜底线程归一化用
CREATE INDEX IF NOT EXISTS idx_threads_subject ON threads(subject_norm);
"""


class ThreadStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- append-only 事件日志 ----------
    def append_event(self, thread_id: str, kind: str, payload: dict,
                     message_id: str | None = None, ts: float | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO events(thread_id, message_id, kind, ts, payload) VALUES (?,?,?,?,?)",
            (thread_id, message_id, kind, ts or time.time(), json.dumps(payload, ensure_ascii=False)),
        )
        self._conn.commit()
        return cur.lastrowid

    def replay(self, thread_id: str) -> list[dict]:
        """按序回放某线程全部事件（审计/重建物化状态用）。"""
        rows = self._conn.execute(
            "SELECT seq, message_id, kind, ts, payload FROM events WHERE thread_id=? ORDER BY seq",
            (thread_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    def message_seen(self, message_id: str) -> bool:
        """幂等：该 Message-ID 是否已入过事件日志。"""
        row = self._conn.execute(
            "SELECT 1 FROM events WHERE message_id=? LIMIT 1", (message_id,)
        ).fetchone()
        return row is not None

    # ---------- 物化状态（检索层） ----------
    def load(self, thread_id: str) -> ThreadState | None:
        row = self._conn.execute(
            "SELECT * FROM threads WHERE thread_id=?", (thread_id,)
        ).fetchone()
        return _row_to_state(row) if row else None

    def load_or_new(self, thread_id: str, subject_norm: str = "") -> ThreadState:
        return self.load(thread_id) or ThreadState(thread_id=thread_id, subject_norm=subject_norm)

    def upsert(self, st: ThreadState) -> None:
        self._conn.execute(
            """INSERT INTO threads(thread_id, subject_norm, participants, messages, status,
                    category, last_inbound_at, last_outbound_at, sla_due_at, followup_count,
                    ticket_id, open_todos)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(thread_id) DO UPDATE SET
                    subject_norm=excluded.subject_norm, participants=excluded.participants,
                    messages=excluded.messages, status=excluded.status, category=excluded.category,
                    last_inbound_at=excluded.last_inbound_at, last_outbound_at=excluded.last_outbound_at,
                    sla_due_at=excluded.sla_due_at, followup_count=excluded.followup_count,
                    ticket_id=excluded.ticket_id, open_todos=excluded.open_todos""",
            (
                st.thread_id, st.subject_norm, json.dumps(sorted(st.participants)),
                json.dumps(st.messages, ensure_ascii=False), st.status.value, st.category,
                st.last_inbound_at, st.last_outbound_at, st.sla_due_at, st.followup_count,
                st.ticket_id, json.dumps(st.open_todos, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    # ---------- sweep 检索（高效范围扫描，非全表） ----------
    def due_threads(self, now: float | None = None) -> list[ThreadState]:
        """所有 SLA 已到期且未解决的活跃线程 —— 命中 idx_threads_due。"""
        now = now or time.time()
        rows = self._conn.execute(
            """SELECT * FROM threads
               WHERE status IN ('open','await_cust','await_int')
                 AND sla_due_at IS NOT NULL AND sla_due_at <= ?
               ORDER BY sla_due_at""",
            (now,),
        ).fetchall()
        return [_row_to_state(r) for r in rows]

    def idle_awaiting_customer(self, idle_before: float) -> list[ThreadState]:
        """等客户回复但已静默超过阈值的线程（用于自动催单）。"""
        rows = self._conn.execute(
            """SELECT * FROM threads
               WHERE status='await_cust' AND last_outbound_at <= ?
               ORDER BY last_outbound_at""",
            (idle_before,),
        ).fetchall()
        return [_row_to_state(r) for r in rows]

    def match_recent_by_subject(self, subject_norm: str, participants: Iterable[str],
                                window_hours: float = 72.0, now: float | None = None) -> str | None:
        """兜底线程归一化：主题+参与者+时间窗匹配一个近期线程。"""
        now = now or time.time()
        cutoff = now - window_hours * 3600
        rows = self._conn.execute(
            "SELECT thread_id, participants, last_inbound_at, last_outbound_at "
            "FROM threads WHERE subject_norm=?",
            (subject_norm,),
        ).fetchall()
        pset = set(participants)
        for r in rows:
            recent = max(r["last_inbound_at"] or 0, r["last_outbound_at"] or 0)
            if recent >= cutoff and pset & set(json.loads(r["participants"] or "[]")):
                return r["thread_id"]
        return None

    def close(self) -> None:
        self._conn.close()


def _row_to_state(row: sqlite3.Row) -> ThreadState:
    return ThreadState(
        thread_id=row["thread_id"],
        subject_norm=row["subject_norm"] or "",
        participants=set(json.loads(row["participants"] or "[]")),
        messages=json.loads(row["messages"] or "[]"),
        status=ThreadStatus(row["status"]),
        category=row["category"] or "",
        last_inbound_at=row["last_inbound_at"] or 0.0,
        last_outbound_at=row["last_outbound_at"] or 0.0,
        sla_due_at=row["sla_due_at"],
        followup_count=row["followup_count"] or 0,
        ticket_id=row["ticket_id"],
        open_todos=json.loads(row["open_todos"] or "[]"),
    )
