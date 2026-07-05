"""World knowledge lane — extracted facts as text, semantically indexed.

The JSON here is the rebuildable source of truth for the Neo4j graph:
each entry is one "subject predicate object" fact. Hand-prunable like
every lane; the graph and mirror follow.
"""

from __future__ import annotations

from .paths import lane_path
from .store import SemanticJsonStore


class WorldKnowledge(SemanticJsonStore):
    def __init__(self, mirror=None) -> None:
        super().__init__(
            name="world_knowledge",
            path=lane_path("world.json"),
            mirror=mirror,
            empty_hint=(
                "No world knowledge extracted yet. The consolidator fills "
                "this as conversations happen; the library and web_search "
                "hold the wider world meanwhile."
            ),
        )
