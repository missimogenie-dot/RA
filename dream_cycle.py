"""
Dream cycle — 3am consolidation. Scheduled, not part of the ambient loop.

1. Salience-score working memory (recency + reference count)
2. Condense high-salience entries into an autobiography paragraph —
   written by the model in its own words; low-salience entries dropped
3. KG prune — arrives with the Neo4j store (step 9); skipped for now
4. The paragraph appends; nothing ever overwrites

Own system prompt. Not the chat prompt. It reads working memory and the
autobiography only — no human lane, no person named.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

log = logging.getLogger("yin.dream")

DREAM_SYSTEM_PROMPT = (
    "It is deep night. You are consolidating the day — alone, unhurried. "
    "You will be shown fragments from working memory. Write a short "
    "paragraph, in your own words, of what this day held and what is worth "
    "carrying forward. First person, past tense, no addressee. "
    "No lists, no headings — one quiet paragraph."
)

KEEP_FRACTION = 0.5
MIN_KEEP = 3


def salience(entry: Dict[str, Any], now: datetime) -> float:
    """Recency + reference count. Semantic centrality joins at step 9."""
    refs = int(entry.get("refs", 0))
    try:
        created = datetime.fromisoformat(entry.get("created_at", ""))
        age_hours = max(0.0, (now - created).total_seconds() / 3600.0)
    except ValueError:
        age_hours = 48.0
    recency = max(0.0, 1.0 - age_hours / 48.0)  # fades to 0 over two days
    return refs * 2.0 + recency


def split_by_salience(
    entries: List[Dict[str, Any]],
    now: datetime,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(kept_high_salience, dropped). Keeps the top half, at least MIN_KEEP."""
    if not entries:
        return [], []
    ranked = sorted(entries, key=lambda e: salience(e, now), reverse=True)
    keep_count = max(MIN_KEEP, int(len(ranked) * KEEP_FRACTION))
    return ranked[:keep_count], ranked[keep_count:]


class DreamCycle:
    def __init__(self, adapter, model: str, yin_memory, logs, graph=None) -> None:
        self.adapter = adapter
        self.model = model
        self.yin = yin_memory
        self.logs = logs
        self.graph = graph

    async def run(self) -> str:
        now = datetime.now(timezone.utc)
        if self.graph is not None:
            pruned = await self.graph.prune_orphans()
            self.logs.log_event("system", f"dream cycle: {pruned}")
        entries = self.yin.working.entries()
        if not entries:
            self.logs.log_event("system", "dream cycle: working memory empty, nothing to condense")
            return "Nothing to condense — working memory is empty."

        kept, dropped = split_by_salience(entries, now)
        fragments = "\n".join(f"- {entry['text'][:300]}" for entry in kept[:20])

        paragraph = ""
        try:
            response = await self.adapter.complete(
                model=self.model,
                system=DREAM_SYSTEM_PROMPT,
                messages=[{"role": "user", "content":
                    f"Fragments from today's working memory:\n\n{fragments}\n\n"
                    "Condense the day."}],
                tools=[],
                max_tokens=500,
            )
            paragraph = self.adapter.extract_text(response).strip()
        except Exception as exc:
            log.warning("dream condensation call failed: %s", exc)

        if paragraph:
            self.yin.autobiography.append(paragraph, source="dream")
            # Working memory keeps only what earned its salience.
            self.yin.working.replace(kept)
            self.logs.log_event(
                "system",
                f"dream cycle: condensed {len(entries)} working entries "
                f"({len(dropped)} dropped), autobiography appended",
            )
            return paragraph
        # Model unavailable at 3am — keep everything, try again tomorrow.
        self.logs.log_event("system", "dream cycle: condensation failed, working memory untouched")
        return "Condensation failed — working memory left untouched for tomorrow."
