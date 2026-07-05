"""Preference lane — Yin-originated, evidence-gated, semantically indexed."""

from __future__ import annotations

from typing import Iterable, Tuple

from .evidence import check_evidence
from .paths import lane_path
from .store import SemanticJsonStore


class PreferenceManager(SemanticJsonStore):
    def __init__(self, mirror=None) -> None:
        super().__init__(
            name="preferences",
            path=lane_path("preferences.json"),
            mirror=mirror,
            empty_hint=(
                "No preferences stored yet. Preferences form from reflection on "
                "real exchanges; lessons and goals may hold related material."
            ),
        )

    def add_preference(
        self,
        text: str,
        evidence: str,
        live_conversation: str,
        recalled_texts: Iterable[str] = (),
        phase: str = "chat",
    ) -> Tuple[bool, str]:
        passes, reason = check_evidence(evidence, live_conversation, recalled_texts, phase)
        if not passes:
            return False, reason
        return self.add(text, evidence=evidence.strip())
