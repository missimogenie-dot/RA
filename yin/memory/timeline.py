"""Timeline — significant events, ordered, hand-prunable."""

from __future__ import annotations

from typing import Tuple

from .jsonstore import load_entries, make_entry, save_entries
from .paths import lane_path


class Timeline:
    def __init__(self) -> None:
        self.path = lane_path("timeline.json")

    def add(self, text: str, kind: str = "event") -> Tuple[bool, str]:
        text = (text or "").strip()
        if not text:
            return False, "timeline event requires non-empty text."
        entries = load_entries(self.path)
        entries.append(make_entry(text, kind=kind))
        save_entries(self.path, entries)
        return True, "Timeline event recorded."

    def read_recent(self, n: int = 10) -> str:
        entries = load_entries(self.path)
        if not entries:
            return (
                "The timeline is empty so far. Significant events land here "
                "as they happen; working memory holds the current session."
            )
        recent = entries[-n:]
        return "\n".join(
            f"- [{entry.get('created_at', '')[:16]}] ({entry.get('kind', 'event')}) {entry['text']}"
            for entry in recent
        )
