"""
memory.py — SQLite-backed persistent memory store.

Each session has a UUID. Every message records its role, content,
and an ISO-8601 timestamp. The agent uses this to reference past
conversations with accurate temporal context.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "memory.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL REFERENCES sessions(id),
                role        TEXT NOT NULL,          -- 'user' | 'assistant' | 'tool'
                content     TEXT NOT NULL,
                tool_name   TEXT,                   -- populated for role='tool'
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id);
        """)


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(title: Optional[str] = None) -> dict:
    sid = str(uuid.uuid4())
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (sid, title or "New chat", now, now),
        )
    return {"id": sid, "title": title or "New chat", "created_at": now, "updated_at": now}


def list_sessions() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def update_session_title(session_id: str, title: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )


def _touch_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now(), session_id),
        )


# ── Messages ──────────────────────────────────────────────────────────────────

def add_message(
    session_id: str,
    role: str,
    content: str,
    tool_name: Optional[str] = None,
) -> dict:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_name, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, tool_name, now),
        )
        msg_id = cur.lastrowid
    _touch_session(session_id)
    return {
        "id": msg_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "tool_name": tool_name,
        "created_at": now,
    }


def get_messages(session_id: str, limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_history_for_prompt(session_id: str, limit: int = 30) -> list[dict]:
    """
    Returns the last `limit` messages formatted for LLM context injection.
    Each entry has role + content (tool messages are folded into assistant).
    """
    messages = get_messages(session_id, limit=limit)
    history = []
    for m in messages:
        if m["role"] in ("user", "assistant"):
            history.append({
                "role": m["role"],
                "content": m["content"],
                "timestamp": m["created_at"],
            })
    return history


def search_across_sessions(query: str, limit: int = 5) -> list[dict]:
    """
    Simple keyword search across all messages — gives the agent cross-session recall.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT m.*, s.title as session_title
               FROM messages m JOIN sessions s ON m.session_id = s.id
               WHERE m.content LIKE ?
               ORDER BY m.created_at DESC LIMIT ?""",
            (f"%{query}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
