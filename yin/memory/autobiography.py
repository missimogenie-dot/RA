"""
Autobiography — append-only narrative log.

There is no edit or delete API on purpose. Entries are dated fragments
in Yin's own words; the dream cycle appends, nothing overwrites.
Private lane: chat responses never read it (see recall.py).
"""

from __future__ import annotations

from typing import Tuple

from .jsonstore import load_entries, make_entry, save_entries
from .paths import lane_path


class Autobiography:
    def __init__(self) -> None:
        self.path = lane_path("autobiography.json")

    def append(self, text: str, source: str = "ambient") -> Tuple[bool, str]:
        text = (text or "").strip()
        if not text:
            return False, "autobiography_append requires non-empty text."
        entries = load_entries(self.path)
        entries.append(make_entry(text, source=source))
        save_entries(self.path, entries)
        return True, f"Appended to autobiography ({len(entries)} entries)."

    def read_recent(self, n: int = 5) -> str:
        entries = load_entries(self.path)
        if not entries:
            return (
                "The autobiography is empty so far. It grows from ambient "
                "fragments and the dream cycle's daily paragraph."
            )
        recent = entries[-n:]
        return "\n\n".join(
            f"[{entry.get('created_at', '')[:10]}] {entry['text']}" for entry in recent
        )

    def count(self) -> int:
        return len(load_entries(self.path))
