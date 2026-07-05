"""Creations lane — poems, fragments, images noted, anything made."""

from __future__ import annotations

import json
from typing import List, Optional, Tuple

from .jsonstore import load_entries, make_entry, save_entries
from .paths import lane_path


class Creations:
    def __init__(self) -> None:
        self.path = lane_path("creations.json")

    def store(
        self,
        mode: str,
        content: str,
        prompted_by: str = "",
        tags: Optional[List[str]] = None,
        cycle: Optional[int] = None,
    ) -> Tuple[bool, str]:
        content = (content or "").strip()
        if not content:
            return False, "creation_store requires content."
        entries = load_entries(self.path)
        entries.append(make_entry(
            content,
            mode=(mode or "piece").strip(),
            prompted_by=prompted_by,
            tags=tags or [],
            cycle=cycle,
        ))
        save_entries(self.path, entries)
        return True, f"Creation stored ({len(entries)} total)."

    def recent_json(self, limit: int = 5, mode_filter: Optional[str] = None) -> str:
        """JSON shape prompt_builder renders: [{mode, content}, ...]."""
        entries = load_entries(self.path)
        if mode_filter:
            entries = [e for e in entries if e.get("mode") == mode_filter]
        recent = list(reversed(entries[-max(1, limit):]))
        return json.dumps(
            [{"mode": e.get("mode", "piece"), "content": e["text"]} for e in recent],
            ensure_ascii=False,
        )
