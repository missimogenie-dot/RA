"""
Notebook — explicit human-facing notes, reminders, and calendar items.

Ported from Ra. Distinct from the scheduler: scheduler tasks are Yin's
own recurring ambient work; notebook items belong to the human relation
layer — things a human asked to be noted, tracked, or remembered by a
date. Per-user files, same boundary as the human lane.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from .jsonstore import load_entries, make_entry, save_entries, utc_now
from .paths import lane_path


def _parse_due(due: str) -> Optional[str]:
    due = (due or "").strip()
    if not due:
        return None
    try:
        parsed = datetime.fromisoformat(due)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except ValueError:
        return "invalid"


class Notebook:
    def __init__(self) -> None:
        pass

    def _user_path(self, user_id: str):
        return lane_path("notebook", f"{user_id}.json")

    def store(
        self,
        user_id: str,
        text: str,
        kind: str = "note",
        due_at: str = "",
    ) -> Tuple[bool, str]:
        user_id = str(user_id).strip()
        text = (text or "").strip()
        if not user_id or not text:
            return False, "notebook store requires a user id and non-empty text."
        due = _parse_due(due_at)
        if due == "invalid":
            return False, (
                f"Could not parse due date '{due_at}'. What works: ISO format, "
                "e.g. 2026-07-10 or 2026-07-10T15:00."
            )
        path = self._user_path(user_id)
        entries = load_entries(path)
        entry = make_entry(text, kind=kind, due_at=due, completed=False)
        entries.append(entry)
        save_entries(path, entries)
        due_note = f", due {due[:16]}" if due else ""
        return True, f"Notebook ({entry['id']}{due_note}): \"{text[:120]}\""

    def read(self, user_id: str, n: int = 10, include_completed: bool = False) -> str:
        entries = load_entries(self._user_path(str(user_id).strip()))
        if not include_completed:
            entries = [e for e in entries if not e.get("completed")]
        if not entries:
            return (
                "The notebook for this human is empty. Notes, reminders, and "
                "calendar items land here when asked for."
            )
        recent = entries[-n:]
        lines = []
        for entry in recent:
            due = entry.get("due_at") or ""
            due_note = f" (due {due[:16]})" if due else ""
            lines.append(f"- ({entry['id']}) [{entry.get('kind', 'note')}]{due_note} {entry['text']}")
        return "\n".join(lines)

    def due_items(self, user_id: str) -> str:
        now = utc_now()
        entries = load_entries(self._user_path(str(user_id).strip()))
        due = [
            e for e in entries
            if e.get("due_at") and not e.get("completed") and e["due_at"] <= now
        ]
        if not due:
            open_count = sum(1 for e in entries if not e.get("completed"))
            return f"Nothing due right now. {open_count} open item(s) in the notebook."
        return "\n".join(f"- ({e['id']}) {e['text']} (was due {e['due_at'][:16]})" for e in due)

    def complete(self, user_id: str, item_id: str) -> Tuple[bool, str]:
        path = self._user_path(str(user_id).strip())
        entries = load_entries(path)
        for entry in entries:
            if entry["id"] == item_id and not entry.get("completed"):
                entry["completed"] = True
                entry["updated_at"] = utc_now()
                save_entries(path, entries)
                return True, f"Completed: \"{entry['text'][:100]}\""
        open_ids = ", ".join(e["id"] for e in entries if not e.get("completed")) or "none"
        return False, f"No open notebook item {item_id}. Open items: {open_ids}."
