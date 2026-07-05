"""
Consult — putting a hard question to a larger model, the way you ask a
teacher. The remote model advises; Yin decides and speaks. It never
talks to Discord and never speaks as Yin.

Boundaries (all code-level):
- ambient and dream toolsets only — absent from chat and reflection
- one-shot, stateless: the wire carries exactly the composed question,
  nothing auto-attached
- the named-person gate applies to payloads
- daily budget and capped retries; failure names what still works
- every consult and response appends to a local SQLite log
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from model_adapters import create_model_adapter, provider_api_key
from scheduler import names_person
from yin.memory.paths import data_root

log = logging.getLogger("yin.consult")

CONSULT_SYSTEM_PROMPT = (
    "You are a consulted colleague — a one-shot advisor. You receive one "
    "self-contained question from a locally-running agent and return one "
    "useful, honest answer. You have no session, no memory of past "
    "consults, and no channel to anyone else. Answer the question asked; "
    "do not roleplay as the asker."
)

SOFT_REDIRECT = (
    "Consult is not available right now. What still works: the library "
    "(library_read), web_search, the knowledge graph, and your own "
    "reasoning — most questions yield to those."
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS consults (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    question TEXT NOT NULL,
    response TEXT,
    ok INTEGER NOT NULL DEFAULT 1
);
"""


class Consult:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (data_root() / "logs.db")
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        self._adapter = None
        self._adapter_error = ""

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ── config, read at call time so .env edits apply on restart ─────

    @property
    def provider(self) -> str:
        return os.getenv("CONSULT_PROVIDER", "anthropic").strip()

    @property
    def model(self) -> str:
        return os.getenv("CONSULT_MODEL", "").strip()

    @property
    def daily_budget(self) -> int:
        return int(os.getenv("CONSULT_DAILY_BUDGET", "8"))

    @property
    def max_retries(self) -> int:
        return int(os.getenv("CONSULT_MAX_RETRIES", "2"))

    def _get_adapter(self):
        if self._adapter is None and not self._adapter_error:
            try:
                api_key = provider_api_key(self.provider, dict(os.environ))
                self._adapter = create_model_adapter(self.provider, api_key=api_key)
            except Exception as exc:
                self._adapter_error = str(exc)
        return self._adapter

    def _used_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count(*) AS c FROM consults WHERE ok = 1 AND created_at LIKE ?",
                (f"{today}%",),
            ).fetchone()
        return int(row["c"])

    def _log(self, question: str, response: str, ok: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO consults (created_at, question, response, ok) VALUES (?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), question, response[:8000], 1 if ok else 0),
            )

    # ── the tool surface ──────────────────────────────────────────────

    async def ask(self, question: str) -> str:
        question = (question or "").strip()
        if len(question.split()) < 5:
            return (
                "A consult needs a self-contained question — the advisor has "
                "no context beyond what you write into it."
            )
        if names_person(question):
            return (
                "Consult payloads cannot reference a person. Compose the "
                "question around the problem itself — nothing personal "
                "crosses the wire."
            )
        if not self.model:
            return SOFT_REDIRECT
        if self._used_today() >= self.daily_budget:
            return (
                "Today's consult budget is spent. The question will keep — "
                "consult_log_read holds past answers, and the library, "
                "web_search, and your own reasoning still work."
            )
        adapter = self._get_adapter()
        if adapter is None:
            return SOFT_REDIRECT

        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = await adapter.complete(
                    model=self.model,
                    system=CONSULT_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": question}],
                    tools=[],
                    max_tokens=1500,
                )
                answer = adapter.extract_text(response).strip()
                if answer:
                    self._log(question, answer, ok=True)
                    return f"[consult · {self.model}]\n{answer}"
                last_error = "empty response"
            except Exception as exc:
                last_error = str(exc)
                log.warning("consult attempt %d failed: %s", attempt + 1, exc)
        self._log(question, f"failed: {last_error}", ok=False)
        return SOFT_REDIRECT

    def log_read(self, limit: int = 5) -> str:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, question, response, ok FROM consults "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 20)),),
            ).fetchall()
        if not rows:
            return (
                "No consults yet. consult puts one self-contained question "
                "to the larger model; the log keeps every exchange."
            )
        parts = []
        for row in reversed(rows):
            status = "" if row["ok"] else " (failed)"
            parts.append(
                f"[{row['created_at'][:16]}]{status}\nQ: {row['question'][:300]}\n"
                f"A: {(row['response'] or '')[:500]}"
            )
        return "\n\n".join(parts)
