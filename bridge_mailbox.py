from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


class BridgeMailbox:
    """A small shared JSONL mailbox for slow bot-to-bot letters."""

    def __init__(self, mailbox_path: Path, seen_path: Path, self_name: str) -> None:
        self.mailbox_path = mailbox_path
        self.seen_path = seen_path
        self.self_name = self_name.strip() or "bot"
        self.recipient_names = {self.self_name.lower()}
        if self.self_name.lower() == "ernos":
            self.recipient_names.add("ernie")

    def send(self, to: str, subject: str, content: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        to = (to or "").strip()
        content = (content or "").strip()
        if not to:
            raise ValueError("bridge_send requires a recipient.")
        if not content:
            raise ValueError("bridge_send requires content.")
        entry = {
            "id": str(uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "from": self.self_name,
            "to": to,
            "subject": (subject or "").strip(),
            "content": content,
            "tags": tags or [],
        }
        self.mailbox_path.parent.mkdir(parents=True, exist_ok=True)
        with self.mailbox_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def inbox(self, limit: int = 5, mark_seen: bool = True) -> List[Dict[str, Any]]:
        seen = self._read_seen()
        entries: List[Dict[str, Any]] = []
        for entry in self._read_all():
            entry_id = str(entry.get("id", ""))
            if not entry_id or entry_id in seen:
                continue
            sender = str(entry.get("from", "")).strip().lower()
            recipient = str(entry.get("to", "")).strip().lower()
            if sender == self.self_name.lower():
                continue
            if recipient not in self.recipient_names | {"all", "both", "penumbra"}:
                continue
            entries.append(entry)
        entries = entries[-max(1, min(limit, 20)):]
        if mark_seen and entries:
            seen.update(str(entry["id"]) for entry in entries)
            self._write_seen(seen)
        return entries

    def format_for_discord(self, entry: Dict[str, Any]) -> str:
        subject = str(entry.get("subject") or "(no subject)").strip()
        return (
            f"**The Penumbra**\n"
            f"To: {entry.get('to', '?')}\n"
            f"From: {entry.get('from', '?')}\n"
            f"Subject: {subject}\n\n"
            f"{str(entry.get('content', '')).strip()}"
        )[:1900]

    def format_inbox(self, entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return "No new Penumbra messages."
        blocks = []
        for entry in entries:
            blocks.append(
                f"[{entry.get('id')}]\n"
                f"From: {entry.get('from', '?')}\n"
                f"Subject: {entry.get('subject') or '(no subject)'}\n"
                f"{entry.get('content', '')}"
            )
        return "\n\n---\n\n".join(blocks)

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self.mailbox_path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        for line in self.mailbox_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                entries.append(value)
        return entries

    def _read_seen(self) -> set[str]:
        if not self.seen_path.exists():
            return set()
        try:
            raw = json.loads(self.seen_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return set()
        if isinstance(raw, list):
            return {str(item) for item in raw}
        return set()

    def _write_seen(self, seen: set[str]) -> None:
        self.seen_path.parent.mkdir(parents=True, exist_ok=True)
        self.seen_path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")
