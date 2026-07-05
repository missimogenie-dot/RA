"""
Deduplication, reinforcement, and the per-theme rate fuse.

DESIGN.md thresholds:
  similarity >= 0.92          -> reinforce the existing entry
  0.85 <= similarity < 0.92   -> rate fuse check; hold quietly if tripped
  similarity < 0.85           -> save as new

The fuse state lives in a code-only JSON file. The model never reads
the fuse logic and hold responses never mention it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .paths import lane_path

REINFORCE_THRESHOLD = 0.92
FUSE_THRESHOLD = 0.85
DAILY_CLUSTER_LIMIT = int(os.getenv("YIN_THEME_SAVES_PER_DAY", "3"))


@dataclass
class DedupDecision:
    action: str  # "new" | "reinforce" | "hold"
    match_id: Optional[str] = None


class RateFuse:
    """Counts near-duplicate saves per semantic cluster per UTC day."""

    def __init__(self, name: str, daily_limit: Optional[int] = None) -> None:
        self.path = lane_path("fuse", f"{name}.json")
        self._daily_limit = daily_limit

    @property
    def daily_limit(self) -> int:
        return DAILY_CLUSTER_LIMIT if self._daily_limit is None else self._daily_limit

    def _load(self) -> dict:
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        if state.get("date") != today:
            state = {"date": today, "counts": {}}
        return state

    def allows(self, cluster_id: str) -> bool:
        state = self._load()
        return int(state["counts"].get(cluster_id, 0)) < self.daily_limit

    def record(self, cluster_id: str) -> None:
        state = self._load()
        state["counts"][cluster_id] = int(state["counts"].get(cluster_id, 0)) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state), encoding="utf-8")


def decide(best_similarity: float, best_id: Optional[str], fuse: RateFuse) -> DedupDecision:
    if best_id is None or best_similarity < FUSE_THRESHOLD:
        return DedupDecision(action="new")
    if best_similarity >= REINFORCE_THRESHOLD:
        return DedupDecision(action="reinforce", match_id=best_id)
    if fuse.allows(best_id):
        fuse.record(best_id)
        return DedupDecision(action="new", match_id=best_id)
    return DedupDecision(action="hold", match_id=best_id)
