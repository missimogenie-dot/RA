"""
Neo4j knowledge graph — fact nodes and relationships.

World knowledge only. Schema kept deliberately simple and queryable:
(:Entity {name})-[:REL {predicate}]->(:Entity {name}). The graph is
never the source of truth alone — facts also land in the world lane
(JSON + semantic mirror), so a lost graph is rebuildable.

Every failure path answers with what still works. A down Neo4j never
crashes the bot.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

log = logging.getLogger("yin.neo4j")


class Neo4jStore:
    def __init__(self, uri: str = "", user: str = "", password: str = "") -> None:
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "")
        self._driver = None

    async def _get_driver(self):
        if self._driver is None:
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(
                self.uri, auth=(self.user, self.password)
            )
        return self._driver

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    UNAVAILABLE = (
        "The knowledge graph is unreachable right now. The world lane still "
        "works — recall_memory covers stored facts — and web_search or the "
        "library can fill gaps."
    )

    async def add_fact(self, subject: str, predicate: str, obj: str) -> Tuple[bool, str]:
        subject = (subject or "").strip()
        predicate = (predicate or "").strip().lower().replace(" ", "_")
        obj = (obj or "").strip()
        if not (subject and predicate and obj):
            return False, "A fact needs subject, predicate, and object."
        try:
            driver = await self._get_driver()
            async with driver.session() as session:
                result = await session.run(
                    """
                    MERGE (s:Entity {name: $subject})
                    MERGE (o:Entity {name: $object})
                    MERGE (s)-[r:REL {predicate: $predicate}]->(o)
                    ON CREATE SET r.created_at = datetime(), r.weight = 1
                    ON MATCH SET r.weight = coalesce(r.weight, 1) + 1
                    RETURN r.weight AS weight
                    """,
                    subject=subject, predicate=predicate, object=obj,
                )
                record = await result.single()
            weight = record["weight"] if record else 1
            note = " (reinforced)" if weight and weight > 1 else ""
            return True, f"Graph: ({subject})-[{predicate}]->({obj}){note}"
        except Exception as exc:
            log.warning("kg add_fact failed: %s", exc)
            return False, self.UNAVAILABLE

    async def search(self, query: str, limit: int = 8) -> str:
        query = (query or "").strip()
        if not query:
            return "kg_search needs a term to look for."
        try:
            driver = await self._get_driver()
            async with driver.session() as session:
                result = await session.run(
                    """
                    MATCH (s:Entity)-[r:REL]->(o:Entity)
                    WHERE toLower(s.name) CONTAINS toLower($q)
                       OR toLower(o.name) CONTAINS toLower($q)
                       OR toLower(r.predicate) CONTAINS toLower($q)
                    RETURN s.name AS s, r.predicate AS p, o.name AS o
                    ORDER BY r.weight DESC
                    LIMIT $limit
                    """,
                    q=query, limit=limit,
                )
                rows = [record async for record in result]
            if not rows:
                total = await self.count_nodes()
                return (
                    f"No graph matches for '{query}'. The graph holds {total} "
                    "entit{} — recall_memory searches the semantic lanes too."
                ).format("y" if total == 1 else "ies")
            return "\n".join(f"- ({row['s']}) —[{row['p']}]→ ({row['o']})" for row in rows)
        except Exception as exc:
            log.warning("kg search failed: %s", exc)
            return self.UNAVAILABLE

    async def count_nodes(self) -> int:
        try:
            driver = await self._get_driver()
            async with driver.session() as session:
                result = await session.run("MATCH (n:Entity) RETURN count(n) AS c")
                record = await result.single()
            return int(record["c"]) if record else 0
        except Exception:
            return 0

    async def prune_orphans(self) -> str:
        """Remove entities with no relationships at all."""
        try:
            driver = await self._get_driver()
            async with driver.session() as session:
                result = await session.run(
                    "MATCH (n:Entity) WHERE NOT (n)--() DETACH DELETE n RETURN count(n) AS c"
                )
                record = await result.single()
            pruned = int(record["c"]) if record else 0
            return f"Pruned {pruned} orphan node(s)." if pruned else "No orphan nodes to prune."
        except Exception as exc:
            log.warning("kg prune failed: %s", exc)
            return self.UNAVAILABLE
