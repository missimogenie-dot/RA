"""Goal lane — Yin-originated, semantically indexed, status-tracked."""

from __future__ import annotations

from typing import Tuple

from .jsonstore import save_entries, utc_now
from .paths import lane_path
from .store import SemanticJsonStore

GOAL_STATUSES = ("open", "active", "done", "dropped")


class GoalManager(SemanticJsonStore):
    metadata_keys = ("status",)

    def __init__(self, mirror=None) -> None:
        super().__init__(
            name="goals",
            path=lane_path("goals.json"),
            mirror=mirror,
            empty_hint=(
                "No goals stored yet. Goals emerge from Yin's own work; "
                "lessons and preferences may hold related material."
            ),
        )

    def add_goal(self, text: str) -> Tuple[bool, str]:
        return self.add(text, status="open")

    def update_goal(self, goal_id: str, status: str) -> Tuple[bool, str]:
        status = (status or "").strip().lower()
        if status not in GOAL_STATUSES:
            return False, (
                f"Unknown goal status '{status}'. "
                f"What works: {', '.join(GOAL_STATUSES)}."
            )
        entries = self.entries()
        for entry in entries:
            if entry["id"] == goal_id:
                entry["status"] = status
                entry["updated_at"] = utc_now()
                save_entries(self.path, entries)
                return True, f"Goal {goal_id} is now {status}: \"{entry['text'][:100]}\""
        existing = ", ".join(e["id"] for e in entries[:10]) or "none stored"
        return False, f"No goal with id {goal_id}. Existing goal ids: {existing}."

    def _format(self, entry) -> str:
        status = entry.get("status", "open")
        weight = int(entry.get("weight", 1))
        weight_note = f" (x{weight})" if weight > 1 else ""
        return f"- [{status}]{weight_note} ({entry['id']}) {entry['text']}"
