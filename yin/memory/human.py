"""
Human lane — per-user facts, keyed by Discord user id.

Each user gets their own hand-prunable JSON file under human/. One
shared semantic collection (human_memory) carries a user_id filter so
chat recall can only ever see the current user's lane — the boundary
is enforced here in code, not by prompt.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .jsonstore import load_entries, make_entry, save_entries, utc_now
from .dedup import RateFuse, decide
from .mirror import make_mirror
from .paths import lane_path


class HumanMemory:
    def __init__(self, mirror=None) -> None:
        self.mirror = mirror if mirror is not None else make_mirror("human_memory")
        self._sync()

    def _user_path(self, user_id: str):
        return lane_path("human", f"{user_id}.json")

    def _all_entries(self):
        root = lane_path("human", "placeholder").parent
        entries = []
        for path in sorted(root.glob("*.json")):
            entries.extend(load_entries(path))
        return entries

    def _sync(self) -> None:
        self.mirror.sync(self._all_entries(), metadata_keys=("user_id",))

    def store(self, user_id: str, text: str) -> Tuple[bool, str]:
        user_id = str(user_id).strip()
        text = (text or "").strip()
        if not user_id or not text:
            return False, "human memory save requires a user id and non-empty text."

        matches = self.mirror.query(text, k=1, where={"user_id": user_id})
        best_id, best_sim = (matches[0][0], matches[0][1]) if matches else (None, 0.0)
        decision = decide(best_sim, best_id, RateFuse("human_memory"))

        path = self._user_path(user_id)
        entries = load_entries(path)

        if decision.action == "reinforce":
            for entry in entries:
                if entry["id"] == decision.match_id:
                    entry["weight"] = int(entry.get("weight", 1)) + 1
                    entry["updated_at"] = utc_now()
                    save_entries(path, entries)
                    return True, f"Reinforced what you already hold: \"{entry['text'][:120]}\""

        if decision.action == "hold":
            return True, "Noted."

        entry = make_entry(text, user_id=user_id)
        entries.append(entry)
        save_entries(path, entries)
        self.mirror.add(entry["id"], text, {"user_id": user_id})
        return True, f"Stored for this human: \"{text[:120]}\""

    def recall(self, user_id: str, query: str, k: int = 5) -> str:
        """Only this user's lane. Other users' facts are unreachable from here."""
        user_id = str(user_id).strip()
        entries = {entry["id"]: entry for entry in load_entries(self._user_path(user_id))}
        if not entries:
            return (
                "Nothing stored about this human yet. Facts accumulate as "
                "conversations happen; save_human_memory adds them."
            )
        matches = self.mirror.query(query, k=k, where={"user_id": user_id})
        found = [entries[m_id] for m_id, _, _ in matches if m_id in entries]
        if not found:
            return (
                f"Nothing about this human matches that. {len(entries)} "
                "fact(s) exist — try a broader phrase or recent ones."
            )
        return "\n".join(
            f"- [{entry.get('created_at', '')[:10]}] {entry['text']}" for entry in found
        )
