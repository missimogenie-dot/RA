"""
KG consolidator — extracts facts into the graph every N conversation turns.

Queued from observe (turn counter % 5), runs as a separate async task,
never blocks a reply. Takes the recent turns and:

1. Extracts factual claims as subject-predicate-object triples
2. MERGEs into Neo4j (existing nodes/edges reinforce, never duplicate)
3. Mirrors each fact into the world lane (JSON + semantic index)
4. Skips claims about Yin's identity or internal state, and anything
   naming a person — world knowledge only. Person facts belong to the
   human lane, and that filter is code, not prompt.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from scheduler import names_person

log = logging.getLogger("yin.kg")

EXTRACT_SYSTEM_PROMPT = (
    "Extract factual claims about the world from the conversation excerpt. "
    "Return ONLY a JSON array of triples: "
    '[{"subject": "...", "predicate": "...", "object": "..."}]. '
    "World knowledge only — durable facts about things, places, works, "
    "concepts. No claims about the participants, their states, or the "
    "conversation itself. Empty array if nothing qualifies."
)

# Subjects/objects that make a triple self-referential rather than worldly.
_SELF_WORDS = {"i", "me", "you", "we", "yin", "the bot", "assistant", "this conversation"}


def _is_worldly(triple: Dict[str, str], instance_name: str) -> bool:
    subject = str(triple.get("subject", "")).strip()
    obj = str(triple.get("object", "")).strip()
    predicate = str(triple.get("predicate", "")).strip()
    if not (subject and predicate and obj):
        return False
    for part in (subject, obj):
        lowered = part.lower()
        if lowered in _SELF_WORDS or lowered == instance_name.lower():
            return False
        if names_person(part):  # person facts go to the human lane
            return False
    return True


def parse_triples(raw: str) -> List[Dict[str, str]]:
    """Tolerant parse of the model's JSON (fenced or bare)."""
    match = re.search(r"\[.*\]", raw or "", re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [t for t in data if isinstance(t, dict)]


class KGConsolidator:
    def __init__(self, adapter, model: str, graph, world, logs, instance_name: str = "Yin") -> None:
        self.adapter = adapter
        self.model = model
        self.graph = graph
        self.world = world
        self.logs = logs
        self.instance_name = instance_name

    async def run(self, turns_text: str) -> str:
        if not (turns_text or "").strip():
            return "nothing to consolidate"
        try:
            response = await self.adapter.complete(
                model=self.model,
                system=EXTRACT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": turns_text[:6000]}],
                tools=[],
                max_tokens=800,
            )
            raw = self.adapter.extract_text(response)
        except Exception as exc:
            log.warning("kg extraction call failed: %s", exc)
            return "extraction call failed"

        triples = [t for t in parse_triples(raw) if _is_worldly(t, self.instance_name)]
        if not triples:
            self.logs.log_event("system", "kg consolidator: no worldly triples this window")
            return "no worldly triples"

        stored = 0
        for triple in triples[:10]:
            subject, predicate, obj = triple["subject"], triple["predicate"], triple["object"]
            ok, _ = await self.graph.add_fact(subject, predicate, obj)
            self.world.add(f"{subject} {predicate} {obj}")
            if ok:
                stored += 1
        self.logs.log_event(
            "system", f"kg consolidator: {stored}/{len(triples)} triples into graph + world lane"
        )
        return f"{stored} facts consolidated"
