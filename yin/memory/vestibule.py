"""
Vestibule — questions held open on purpose.

Ported from Ra, where it earned its keep. Holding is available anywhere;
checking and tending belong to reflection and ambient cycles. Resolving
keeps the question with its resolution — the vestibule is a record of
what was held, not just a queue.
"""

from __future__ import annotations

from typing import Tuple

from .jsonstore import load_entries, make_entry, save_entries, utc_now
from .paths import lane_path


class Vestibule:
    def __init__(self) -> None:
        self.path = lane_path("vestibule.json")

    def hold(self, question: str, context: str = "") -> Tuple[bool, str]:
        question = (question or "").strip()
        if not question:
            return False, "vestibule_hold requires a question to hold."
        entries = load_entries(self.path)
        entry = make_entry(question, status="open", context=context, notes=[])
        entries.append(entry)
        save_entries(self.path, entries)
        return True, f"Held open ({entry['id']}): \"{question[:120]}\""

    def check(self, n: int = 8) -> str:
        entries = [e for e in load_entries(self.path) if e.get("status") == "open"]
        if not entries:
            total = len(load_entries(self.path))
            if total:
                return (
                    f"No questions currently held open. {total} resolved "
                    "question(s) remain in the vestibule's record."
                )
            return (
                "The vestibule is empty — nothing held open yet. Questions "
                "worth keeping unresolved can be held with vestibule_hold."
            )
        recent = entries[-n:]
        lines = []
        for entry in recent:
            note_count = len(entry.get("notes", []))
            tended = f", tended x{note_count}" if note_count else ""
            lines.append(
                f"- ({entry['id']}) [{entry.get('created_at', '')[:10]}{tended}] {entry['text']}"
            )
        return "\n".join(lines)

    def tend(self, question_id: str, note: str) -> Tuple[bool, str]:
        note = (note or "").strip()
        if not note:
            return False, "Tending needs a note — what shifted, what still holds."
        entries = load_entries(self.path)
        for entry in entries:
            if entry["id"] == question_id and entry.get("status") == "open":
                entry.setdefault("notes", []).append({"at": utc_now(), "note": note})
                entry["updated_at"] = utc_now()
                save_entries(self.path, entries)
                return True, f"Tended ({question_id}): \"{entry['text'][:80]}\""
        open_ids = ", ".join(e["id"] for e in entries if e.get("status") == "open") or "none"
        return False, f"No open question with id {question_id}. Open ids: {open_ids}."

    def resolve(self, question_id: str, resolution: str) -> Tuple[bool, str]:
        resolution = (resolution or "").strip()
        if not resolution:
            return False, "Resolving needs the resolution itself, in words."
        entries = load_entries(self.path)
        for entry in entries:
            if entry["id"] == question_id and entry.get("status") == "open":
                entry["status"] = "resolved"
                entry["resolution"] = resolution
                entry["updated_at"] = utc_now()
                save_entries(self.path, entries)
                return True, f"Resolved ({question_id}): \"{entry['text'][:80]}\""
        open_ids = ", ".join(e["id"] for e in entries if e.get("status") == "open") or "none"
        return False, f"No open question with id {question_id}. Open ids: {open_ids}."
