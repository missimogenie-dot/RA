"""Lesson lane — Yin-originated, evidence-gated, rate-fused."""

from __future__ import annotations

from typing import Iterable, Tuple

from .evidence import check_evidence
from .paths import lane_path
from .store import SemanticJsonStore


class LessonManager(SemanticJsonStore):
    def __init__(self, mirror=None) -> None:
        super().__init__(
            name="lessons",
            path=lane_path("lessons.json"),
            mirror=mirror,
            empty_hint=(
                "No lessons stored yet. Lessons accumulate from reflection "
                "after real exchanges; goals and preferences may hold related material."
            ),
        )

    def add_lesson(
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
