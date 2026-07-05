"""
Scheduler — Yin-created recurring tasks.

Rules enforced here in code, not in any prompt:
- task instructions must be self-contained
- instructions cannot reference a named person (rejected at storage)
- tasks run against the ambient system prompt (bot.py wires this)
- the lesson/preference save path is closed anyway: the evidence gate
  rejects any phase without a live conversation

Storage is SQLite alongside the logs — append-heavy, structured, not
hand-prunable.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from yin.memory.paths import data_root

MIN_INTERVAL_MINUTES = 60
MIN_INSTRUCTION_WORDS = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    instruction TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL,
    next_run TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    last_result TEXT
);
"""


def _blocked_names() -> List[str]:
    raw = os.getenv("SCHEDULER_BLOCKED_NAMES", "immie")
    return [name.strip().lower() for name in raw.split(",") if name.strip()]


def names_person(instruction: str) -> Optional[str]:
    """The named-person gate. Word-boundary match against the configured
    name list — code-level, the model never reads this."""
    lowered = (instruction or "").lower()
    for name in _blocked_names():
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            return name
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Scheduler:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (data_root() / "logs.db")
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def add_task(self, instruction: str, interval_minutes: int) -> Tuple[bool, str]:
        instruction = (instruction or "").strip()
        if len(instruction.split()) < MIN_INSTRUCTION_WORDS:
            return False, (
                "A scheduled task needs a self-contained instruction — a full "
                "sentence that makes sense with no other context."
            )
        name = names_person(instruction)
        if name:
            return False, (
                "Scheduled tasks cannot reference a person. Rephrase the task "
                "around the work itself — the library, the knowledge graph, "
                "the goals — not around anyone."
            )
        try:
            interval = int(interval_minutes)
        except (TypeError, ValueError):
            interval = 0
        if interval < MIN_INTERVAL_MINUTES:
            return False, (
                f"Interval too short. Minimum is {MIN_INTERVAL_MINUTES} minutes; "
                "slower rhythms (daily, every few hours) work well."
            )
        next_run = (_utc_now() + timedelta(minutes=interval)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO scheduled_tasks (created_at, instruction, interval_minutes, next_run) "
                "VALUES (?, ?, ?, ?)",
                (_utc_now().isoformat(), instruction, interval, next_run),
            )
        return True, f"Task {cursor.lastrowid} scheduled every {interval} min: \"{instruction[:120]}\""

    def list_tasks(self) -> str:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, instruction, interval_minutes, next_run, last_run FROM scheduled_tasks "
                "WHERE active = 1 ORDER BY id"
            ).fetchall()
        if not rows:
            return (
                "No scheduled tasks. schedule_task creates one — a "
                "self-contained instruction and an interval in minutes."
            )
        return "\n".join(
            f"- [{row['id']}] every {row['interval_minutes']}m, next {row['next_run'][:16]}: "
            f"{row['instruction'][:140]}"
            for row in rows
        )

    def cancel_task(self, task_id: int) -> Tuple[bool, str]:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE scheduled_tasks SET active = 0 WHERE id = ? AND active = 1", (task_id,)
            )
            if cursor.rowcount:
                return True, f"Task {task_id} cancelled."
        return False, f"No active task {task_id}. Current tasks:\n{self.list_tasks()}"

    def due_tasks(self) -> List[Dict[str, Any]]:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, instruction, interval_minutes FROM scheduled_tasks "
                "WHERE active = 1 AND next_run <= ? ORDER BY next_run LIMIT 3",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_ran(self, task_id: int, result_preview: str = "") -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT interval_minutes FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not row:
                return
            next_run = (_utc_now() + timedelta(minutes=row["interval_minutes"])).isoformat()
            conn.execute(
                "UPDATE scheduled_tasks SET last_run = ?, last_result = ?, next_run = ? WHERE id = ?",
                (_utc_now().isoformat(), result_preview[:400], next_run, task_id),
            )
