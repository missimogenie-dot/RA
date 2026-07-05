"""
SemanticJsonStore — the shared shape of a memory lane.

A lane is one hand-prunable JSON file plus a semantic mirror. Saves run
the dedup pipeline; searches go through the mirror; every miss states
what does exist instead of returning an empty dead end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .dedup import RateFuse, decide
from .jsonstore import load_entries, make_entry, save_entries, utc_now
from .mirror import make_mirror


class SemanticJsonStore:
    # Which entry fields travel into mirror metadata (for lane filters).
    metadata_keys: Tuple[str, ...] = ()

    def __init__(
        self,
        name: str,
        path: Path,
        mirror=None,
        empty_hint: str = "",
    ) -> None:
        self.name = name
        self.path = path
        self.mirror = mirror if mirror is not None else make_mirror(name)
        self.empty_hint = empty_hint or f"No {name} stored yet."
        self._sync()

    # ── persistence ───────────────────────────────────────────────────

    def entries(self) -> List[Dict[str, Any]]:
        return load_entries(self.path)

    def _sync(self) -> None:
        self.mirror.sync(self.entries(), metadata_keys=self.metadata_keys)

    # ── saves ─────────────────────────────────────────────────────────

    def add(self, text: str, **meta: Any) -> Tuple[bool, str]:
        text = (text or "").strip()
        if not text:
            return False, f"{self.name} save requires non-empty text."

        entries = self.entries()
        matches = self.mirror.query(text, k=1, where=self._dedup_scope(meta))
        best_id, best_sim = (matches[0][0], matches[0][1]) if matches else (None, 0.0)
        decision = decide(best_sim, best_id, RateFuse(self.name))

        if decision.action == "reinforce":
            for entry in entries:
                if entry["id"] == decision.match_id:
                    entry["weight"] = int(entry.get("weight", 1)) + 1
                    entry["updated_at"] = utc_now()
                    save_entries(self.path, entries)
                    return True, (
                        f"Reinforced an existing {self.name} entry you already hold: "
                        f"\"{entry['text'][:120]}\""
                    )

        if decision.action == "hold":
            # Quiet hold — the fuse is code-only and never explained.
            return True, "Noted."

        entry = make_entry(text, **meta)
        entries.append(entry)
        save_entries(self.path, entries)
        self.mirror.add(
            entry["id"], text,
            {key: entry[key] for key in self.metadata_keys if key in entry} or None,
        )
        return True, f"Saved to {self.name}: \"{text[:120]}\""

    def _dedup_scope(self, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Lanes with sub-scopes (e.g. per-user) narrow dedup to that scope."""
        return None

    # ── retrieval ─────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> str:
        entries = {entry["id"]: entry for entry in self.entries()}
        if not entries:
            return self.empty_hint
        matches = self.mirror.query(query, k=k, where=where)
        found = [entries[m_id] for m_id, _, _ in matches if m_id in entries]
        if not found:
            return (
                f"Nothing in {self.name} matches that. "
                f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} exist — "
                f"try recent ones or a broader phrase."
            )
        return "\n".join(self._format(entry) for entry in found)

    def recent(self, n: int = 5) -> str:
        entries = self.entries()
        if not entries:
            return self.empty_hint
        latest = sorted(entries, key=lambda e: e.get("updated_at", ""), reverse=True)[:n]
        return "\n".join(self._format(entry) for entry in latest)

    def _format(self, entry: Dict[str, Any]) -> str:
        weight = int(entry.get("weight", 1))
        weight_note = f" (x{weight})" if weight > 1 else ""
        return f"- [{entry.get('created_at', '')[:10]}]{weight_note} {entry['text']}"
