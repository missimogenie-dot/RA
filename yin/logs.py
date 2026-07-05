"""
SQLite logs — replaces Ra's Postgres events and tool_calls tables.

Append-heavy, structured, not hand-prunable: exactly what SQLite is for
in this build. Everything hand-prunable stays JSON. The consult log and
scheduler tables will live in this same database when they arrive.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from .memory.jsonstore import utc_now
from .memory.paths import data_root

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    phase TEXT NOT NULL,
    args TEXT,
    result_preview TEXT,
    success INTEGER NOT NULL DEFAULT 1
);
"""


class YinLogs:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (data_root() / "logs.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ── events ────────────────────────────────────────────────────────

    def log_event(
        self,
        source_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO events (created_at, source_type, content, metadata) VALUES (?, ?, ?, ?)",
                (utc_now(), source_type, content, json.dumps(metadata) if metadata else None),
            )
            return int(cursor.lastrowid)

    def recent_events(self, limit: int = 20) -> str:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, source_type, content FROM events ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        if not rows:
            return "No events logged yet. Events accumulate as the bot runs."
        return "\n".join(
            f"- [{row['created_at'][:16]}] ({row['source_type']}) {row['content'][:160]}"
            for row in rows
        )

    # ── tool calls ────────────────────────────────────────────────────

    def log_tool_call(
        self,
        tool_name: str,
        phase: str,
        args: Optional[Dict[str, Any]] = None,
        result_preview: str = "",
        success: bool = True,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO tool_calls (created_at, tool_name, phase, args, result_preview, success) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    utc_now(),
                    tool_name,
                    phase,
                    json.dumps(args) if args else None,
                    result_preview[:400],
                    1 if success else 0,
                ),
            )
            return int(cursor.lastrowid)

    def recent_tool_calls(self, limit: int = 12) -> str:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, tool_name, phase, result_preview, success FROM tool_calls "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        if not rows:
            return "No tool calls logged yet."
        return "\n".join(
            f"- [{row['created_at'][11:16]}] {row['tool_name']} ({row['phase']})"
            f"{'' if row['success'] else ' FAILED'} → {row['result_preview'][:120]}"
            for row in rows
        )
