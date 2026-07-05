"""
Evidence gate for lesson and preference saves.

A save must quote the current live conversation verbatim. The gate
rejects: no evidence, evidence not present in the live text, evidence
that matches recalled memory instead of live text, and any save from a
phase without a live conversation (ambient/dream/scheduled). This is a
code-level gate, not a prompt instruction.
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

LIVE_PHASES = {"chat", "reflection"}


def _normalise(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def check_evidence(
    evidence: str,
    live_conversation: str,
    recalled_texts: Iterable[str] = (),
    phase: str = "chat",
) -> Tuple[bool, Optional[str]]:
    """Returns (passes, rejection_reason). Reasons name what would work."""
    if phase not in LIVE_PHASES:
        return False, (
            "This save path needs a live conversation, which this cycle does not have. "
            "What works here: autobiography_append, creation_store, or working notes."
        )

    ev = _normalise(evidence)
    if not ev or len(ev) < 8:
        return False, (
            "A save needs verbatim evidence — quote the exact words from the "
            "current conversation that support it."
        )

    if ev not in _normalise(live_conversation):
        return False, (
            "The evidence quote was not found in the current conversation. "
            "Quote the exact words as they appeared, not a paraphrase."
        )

    for recalled in recalled_texts:
        if ev and ev in _normalise(recalled):
            return False, (
                "That evidence comes from recalled memory, not the live conversation. "
                "Only words spoken in the current exchange can support a new save."
            )

    return True, None
