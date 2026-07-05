"""
Working memory — current session context, salience-scored.

Entries carry a reference count and timestamps; the dream cycle scores
salience from recency, references, and semantic centrality, then
condenses high-salience entries into the autobiography and drops the
rest. A soft cap keeps the file prunable by hand.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .jsonstore import load_entries, make_entry, save_entries, utc_now
from .paths import lane_path

SOFT_CAP = 200


class WorkingMemory:
    def __init__(self) -> None:
        self.path = lane_path("working.json")

    def add(self, text: str, source: str = "chat") -> Tuple[bool, str]:
        text = (text or "").strip()
        if not text:
            return False, "working memory add requires non-empty text."
        entries = load_entries(self.path)
        entries.append(make_entry(text, source=source, refs=0))
        if len(entries) > SOFT_CAP:
            # Drop the oldest never-referenced entries first.
            entries.sort(key=lambda e: (int(e.get("refs", 0)), e.get("created_at", "")))
            entries = entries[len(entries) - SOFT_CAP:]
            entries.sort(key=lambda e: e.get("created_at", ""))
        save_entries(self.path, entries)
        return True, "Added to working memory."

    def touch(self, entry_id: str) -> None:
        entries = load_entries(self.path)
        for entry in entries:
            if entry["id"] == entry_id:
                entry["refs"] = int(entry.get("refs", 0)) + 1
                entry["updated_at"] = utc_now()
        save_entries(self.path, entries)

    def read(self, n: int = 20) -> str:
        entries = load_entries(self.path)
        if not entries:
            return (
                "Working memory is empty — a fresh session. The timeline "
                "and recall tools hold longer-lived material."
            )
        recent = entries[-n:]
        return "\n".join(
            f"- ({entry['id']}) [{entry.get('created_at', '')[11:16]}] {entry['text']}"
            for entry in recent
        )

    def entries(self) -> List[Dict[str, Any]]:
        return load_entries(self.path)

    def replace(self, entries: List[Dict[str, Any]]) -> None:
        """Used by the dream cycle after condensation."""
        save_entries(self.path, entries)
