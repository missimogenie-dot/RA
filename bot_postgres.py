from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from influence_router import is_acknowledgement_only, is_no_reply_marker

log = logging.getLogger(__name__)

try:
    import asyncpg
    _ASYNCPG = True
except ImportError:
    _ASYNCPG = False

try:
    from openai import AsyncOpenAI
    _OPENAI = True
except ImportError:
    _OPENAI = False


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_INTERPRETATION_TYPES = {
    "observation",
    "self_inference",
    "hypothesis",
    "question",
    "external_claim",
    "local_alternative",
}

VALID_INTERPRETATION_STATUSES = {
    "provisional",
    "held_open",
    "insufficient_basis",
    "not_integrating_yet",
    "contested",
    "stable",
    "archived",
    "discarded",
}

VALID_LINK_TYPES = {
    "revises",
    "supports",
    "conflicts_with",
    "extends",
    "came_from",
    "echoes",
}

VALID_ADMISSION_CATEGORIES = {
    "useful_continuity",
    "explicit_tracking",
    "sensitive_or_emotional",
    "one_off_event",
}

VALID_HABITAT_AREAS = {
    "observatory",
    "garden",
    "studio",
    "library",
    "atlas",
    "threshold",
    "game",
}

VALID_HABITAT_ENTRY_TYPES = {
    "seed",
    "shelf_item",
    "path",
    "weather",
    "fragment",
    "marker",
    "object",
}

VALID_HABITAT_ENTRY_STATUSES = {
    "active",
    "resting",
    "resolved",
    "decayed",
    "archived",
}


def _uuid_text(value: str) -> Optional[str]:
    try:
        return str(UUID(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return None


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _context_key(value: str) -> str:
    key = "".join(ch if ch.isalnum() else "-" for ch in (value or "general").lower())
    key = "-".join(part for part in key.split("-") if part)
    return key[:60] or "general"


def _review_allowed(candidate: Dict[str, Any], decision: str) -> tuple[bool, str]:
    valid = {
        "promote_to_provisional", "promote_to_stable",
        "hold", "reject", "archive", "decay", "demote", "reinforce",
    }
    if decision not in valid:
        return False, f"invalid decision {decision!r}"
    if candidate.get("human_authored"):
        return False, "human-authored candidates cannot be promoted in bot-self memory"
    if decision == "promote_to_stable":
        if candidate.get("promotion_status") != "provisional":
            return False, "stable promotion requires existing provisional status"
        if candidate.get("identity_relevant"):
            if candidate.get("source_kind") != "bot":
                return False, "identity-relevant stable promotion requires bot source_kind"
            if int(candidate.get("recurrence_count") or 0) < 2:
                return False, "identity-relevant stable promotion requires recurrence_count >= 2"
    return True, ""


def _status_for_decision(current_status: str, decision: str) -> Optional[str]:
    mapping = {
        "promote_to_provisional": "provisional",
        "promote_to_stable": "stable",
        "hold": "held_open",
        "reject": "rejected",
        "archive": "archived",
        "decay": "discarded",
        "demote": "candidate" if current_status == "provisional" else "held_open",
        "reinforce": current_status,
    }
    return mapping.get(decision)


class BotPostgres:
    """Postgres persistence layer. All methods degrade gracefully when unavailable."""

    def __init__(
        self,
        dsn: str,
        openai_api_key: str = "",
        schema: str = "public",
        expected_database: str = "",
    ) -> None:
        self._dsn = dsn
        self._schema = schema
        self._expected_database = expected_database
        self._pool: Any = None
        self._openai: Any = AsyncOpenAI(api_key=openai_api_key) if (_OPENAI and openai_api_key) else None

    async def connect(self) -> bool:
        if not _ASYNCPG or not self._dsn:
            return False
        try:
            from pgvector.asyncpg import register_vector

            async def _init(conn):
                await register_vector(conn)

            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=5,
                server_settings={"search_path": f"{self._schema},public"},
                init=_init,
            )
            if self._expected_database:
                async with self._pool.acquire() as conn:
                    database = await conn.fetchval("select current_database()")
                if database != self._expected_database:
                    await self._pool.close()
                    self._pool = None
                    raise RuntimeError(
                        f"Connected to database {database!r}; expected {self._expected_database!r}."
                    )
            log.info("Postgres connected (database=%s, schema=%s)", self._expected_database or "?", self._schema)
            return True
        except Exception as exc:
            log.warning("Postgres unavailable: %s", exc)
            self._pool = None
            return False

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    @property
    def available(self) -> bool:
        return self._pool is not None

    # ── embeddings ────────────────────────────────────────────────────

    async def _embed(self, text: str) -> Optional[List[float]]:
        if not self._openai:
            return None
        try:
            resp = await self._openai.embeddings.create(
                model="text-embedding-3-small",
                input=text[:8000],
            )
            return resp.data[0].embedding
        except Exception as exc:
            log.warning("Embedding failed: %s", exc)
            return None

    # ── memory_interpretations ────────────────────────────────────────

    async def store_interpretation(
        self,
        type: str,
        content: str,
        confidence: float = 0.5,
        status: str = "provisional",
        tags: Optional[List[str]] = None,
        source_actor: str = "bot",
    ) -> str:
        type = str(type or "").strip()
        status = str(status or "").strip()
        if type not in VALID_INTERPRETATION_TYPES:
            return f"error: invalid interpretation type {type!r}; use memory_interpret with one of {sorted(VALID_INTERPRETATION_TYPES)}"
        if status not in VALID_INTERPRETATION_STATUSES:
            return f"error: invalid interpretation status {status!r}; use one of {sorted(VALID_INTERPRETATION_STATUSES)}"
        # Structural downweighting: human claims start at lower confidence
        if source_actor == "human" and type == "external_claim":
            confidence = min(confidence, 0.3)
        if not self.available:
            return f"[no-postgres] {content[:80]}"
        embedding = await self._embed(content)
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO memory_interpretations
                        (type, content, embedding, confidence, status, tags, source_actor)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    type, content, embedding, confidence, status,
                    tags or [], source_actor,
                )
                return str(row["id"])
        except Exception as exc:
            log.error("store_interpretation: %s", exc)
            return f"error: {exc}"

    async def update_interpretation_status(self, id: str, new_status: str, note: str = "") -> str:
        if not self.available:
            return "[no-postgres]"
        id = _uuid_text(id)
        if not id:
            return "error: invalid interpretation id; use a full UUID returned by memory_interpret, memory_recent, or memory_search"
        if new_status not in VALID_INTERPRETATION_STATUSES:
            return f"error: invalid interpretation status {new_status!r}; use one of {sorted(VALID_INTERPRETATION_STATUSES)}"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE memory_interpretations
                    SET status = $1, status_updated_at = now()
                    WHERE id = $2
                    RETURNING id
                    """,
                    new_status, id,
                )
                if not row:
                    return f"error: interpretation_id not found: {id}; use memory_recent/search or store it first with memory_interpret"
                if note:
                    await conn.execute(
                        """
                        INSERT INTO memory_interpretations
                            (type, content, confidence, status, tags, source_actor)
                        VALUES ('observation', $1, 0.5, 'provisional', '{}', 'bot')
                        """,
                        f"Status update note for {id}: {note}",
                    )
            return f"updated {id} → {new_status}"
        except Exception as exc:
            log.error("update_interpretation_status: %s", exc)
            return f"error: {exc}"

    async def link_interpretations(self, from_id: str, to_id: str, link_type: str, note: str = "") -> str:
        if not self.available:
            return "[no-postgres]"
        from_id = _uuid_text(from_id)
        to_id = _uuid_text(to_id)
        link_type = str(link_type or "").strip()
        if not from_id or not to_id:
            return "error: invalid link id; from_id and to_id must be full UUIDs from memory_interpret, memory_recent, or memory_search"
        if link_type not in VALID_LINK_TYPES:
            return f"error: invalid link_type {link_type!r}; use one of {sorted(VALID_LINK_TYPES)}"
        try:
            async with self._pool.acquire() as conn:
                missing = await conn.fetch(
                    """
                    SELECT x.id
                    FROM unnest($1::uuid[]) AS x(id)
                    LEFT JOIN memory_interpretations m ON m.id = x.id
                    WHERE m.id IS NULL
                    """,
                    [from_id, to_id],
                )
                if missing:
                    ids = ", ".join(str(r["id"]) for r in missing)
                    return f"error: interpretation_id not found: {ids}; use memory_recent/search or store missing rows first with memory_interpret"
                row = await conn.fetchrow(
                    """
                    INSERT INTO interpretation_links (from_id, to_id, link_type, note)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (from_id, to_id, link_type) DO NOTHING
                    RETURNING id
                    """,
                    from_id, to_id, link_type, note or None,
                )
            return str(row["id"]) if row else "already linked"
        except Exception as exc:
            log.error("link_interpretations: %s", exc)
            return f"error: {exc}"

    async def search_interpretations(
        self,
        query: str,
        limit: int = 10,
        status_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
    ) -> str:
        if not self.available:
            return "[]"
        embedding = await self._embed(query)
        try:
            async with self._pool.acquire() as conn:
                if embedding:
                    conditions = ["status NOT IN ('archived','discarded','insufficient_basis')"]
                    params: list = [embedding, limit]
                    if status_filter:
                        conditions.append(f"status = ${len(params)+1}")
                        params.append(status_filter)
                    if type_filter:
                        conditions.append(f"type = ${len(params)+1}")
                        params.append(type_filter)
                    where = " AND ".join(conditions)
                    rows = await conn.fetch(
                        f"""
                        SELECT id, type, content, confidence, status, tags, source_actor, created_at
                        FROM memory_interpretations
                        WHERE {where}
                        ORDER BY embedding <=> $1
                        LIMIT $2
                        """,
                        *params,
                    )
                else:
                    needle = f"%{query.lower()}%"
                    rows = await conn.fetch(
                        """
                        SELECT id, type, content, confidence, status, tags, source_actor, created_at
                        FROM memory_interpretations
                        WHERE lower(content) LIKE $1
                          AND status NOT IN ('archived','discarded','insufficient_basis')
                        ORDER BY created_at DESC LIMIT $2
                        """,
                        needle, limit,
                    )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("search_interpretations: %s", exc)
            return f"error: {exc}"

    async def recent_interpretations(
        self,
        limit: int = 10,
        type_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        tag_filter: Optional[str] = None,
    ) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                conditions = ["status NOT IN ('archived','discarded','insufficient_basis')"]
                params: list = []
                if type_filter:
                    params.append(type_filter)
                    conditions.append(f"type = ${len(params)}")
                if status_filter:
                    params.append(status_filter)
                    conditions.append(f"status = ${len(params)}")
                if tag_filter:
                    params.append(tag_filter)
                    conditions.append(f"$%d = ANY(tags)" % len(params))
                params.append(limit)
                where = " AND ".join(conditions)
                rows = await conn.fetch(
                    f"""
                    SELECT id, type, content, confidence, status, tags, source_actor, created_at
                    FROM memory_interpretations
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT ${len(params)}
                    """,
                    *params,
                )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("recent_interpretations: %s", exc)
            return f"error: {exc}"

    async def identity_threads(self, limit: int = 10) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, type, content, confidence, status, tags, created_at FROM identity_threads LIMIT $1",
                    limit,
                )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("identity_threads: %s", exc)
            return "[]"

    # ── creations ─────────────────────────────────────────────────────

    async def store_creation(
        self,
        mode: str,
        content: str,
        prompted_by: Optional[str] = None,
        tags: Optional[List[str]] = None,
        cycle: Optional[int] = None,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        embedding = await self._embed(content)
        try:
            async with self._pool.acquire() as conn:
                prompted_by_id = _uuid_text(prompted_by) if prompted_by else None
                if prompted_by and not prompted_by_id:
                    return "error: prompted_by_id must be a full UUID from memory_interpret, memory_recent, or memory_search"
                if prompted_by_id:
                    exists = await conn.fetchval(
                        "SELECT EXISTS (SELECT 1 FROM memory_interpretations WHERE id=$1)",
                        prompted_by_id,
                    )
                    if not exists:
                        return f"error: prompted_by_id not found: {prompted_by_id}; store the referenced interpretation first or omit prompted_by_id"
                row = await conn.fetchrow(
                    """
                    INSERT INTO creations (mode, content, embedding, prompted_by, tags, cycle)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    mode, content, embedding,
                    prompted_by_id, tags or [], cycle,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("store_creation: %s", exc)
            return f"error: {exc}"

    async def recent_creations(self, limit: int = 5, mode_filter: Optional[str] = None) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                if mode_filter:
                    rows = await conn.fetch(
                        "SELECT id, mode, content, tags, created_at FROM creations WHERE mode=$1 ORDER BY created_at DESC LIMIT $2",
                        mode_filter, limit,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT id, mode, content, tags, created_at FROM creations ORDER BY created_at DESC LIMIT $1",
                        limit,
                    )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("recent_creations: %s", exc)
            return "[]"

    # ── posture_state ─────────────────────────────────────────────────

    async def ensure_habitat_area(self, bot_id: str, area: str, state: Optional[Dict[str, Any]] = None) -> str:
        if not self.available:
            return "[no-postgres]"
        area = str(area or "").strip().lower()
        if area not in VALID_HABITAT_AREAS:
            return f"error: invalid habitat area {area!r}; use one of {sorted(VALID_HABITAT_AREAS)}"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO habitat_state (bot_id, area, state)
                    VALUES ($1, $2, $3::jsonb)
                    ON CONFLICT (bot_id, area) DO UPDATE
                        SET updated_at = habitat_state.updated_at
                    RETURNING id
                    """,
                    bot_id, area, json.dumps(state or {}),
                )
            return str(row["id"])
        except Exception as exc:
            log.error("ensure_habitat_area: %s", exc)
            return f"error: {exc}"

    async def habitat_snapshot(self, bot_id: str, area: str = "", event_limit: int = 8) -> str:
        if not self.available:
            return "[]"
        area = str(area or "").strip().lower()
        if area and area not in VALID_HABITAT_AREAS:
            return f"error: invalid habitat area {area!r}; use one of {sorted(VALID_HABITAT_AREAS)}"
        try:
            async with self._pool.acquire() as conn:
                if area:
                    await self.ensure_habitat_area(bot_id, area)
                    states = await conn.fetch(
                        """
                        SELECT area, state, updated_at
                        FROM habitat_state
                        WHERE bot_id=$1 AND area=$2
                        ORDER BY area
                        """,
                        bot_id, area,
                    )
                    events = await conn.fetch(
                        """
                        SELECT id, area, action, content, metadata, created_at
                        FROM habitat_events
                        WHERE bot_id=$1 AND area=$2
                          AND COALESCE(metadata->>'source', '') <> 'tool_echo'
                        ORDER BY created_at DESC
                        LIMIT $3
                        """,
                        bot_id, area, max(1, min(event_limit, 30)),
                    )
                    entries = await conn.fetch(
                        """
                        SELECT id, area, entry_type, title, content, status,
                               suggested_actions, weight, confidence, reason,
                               source_type, source_ref, metadata, created_at, last_touched_at
                        FROM habitat_entries
                        WHERE bot_id=$1 AND area=$2
                          AND status NOT IN ('archived', 'decayed', 'resolved')
                        ORDER BY weight DESC, last_touched_at DESC
                        LIMIT $3
                        """,
                        bot_id, area, max(1, min(event_limit, 30)),
                    )
                else:
                    for habitat_area in sorted(VALID_HABITAT_AREAS):
                        await self.ensure_habitat_area(bot_id, habitat_area)
                    states = await conn.fetch(
                        """
                        SELECT area, state, updated_at
                        FROM habitat_state
                        WHERE bot_id=$1
                        ORDER BY area
                        """,
                        bot_id,
                    )
                    events = await conn.fetch(
                        """
                        SELECT id, area, action, content, metadata, created_at
                        FROM habitat_events
                        WHERE bot_id=$1
                          AND COALESCE(metadata->>'source', '') <> 'tool_echo'
                        ORDER BY created_at DESC
                        LIMIT $2
                        """,
                        bot_id, max(1, min(event_limit, 30)),
                    )
                    entries = await conn.fetch(
                        """
                        SELECT id, area, entry_type, title, content, status,
                               suggested_actions, weight, confidence, reason,
                               source_type, source_ref, metadata, created_at, last_touched_at
                        FROM habitat_entries
                        WHERE bot_id=$1
                          AND status NOT IN ('archived', 'decayed', 'resolved')
                        ORDER BY weight DESC, last_touched_at DESC
                        LIMIT $2
                        """,
                        bot_id, max(1, min(event_limit, 30)),
                    )
            return json.dumps({
                "areas": [dict(r) for r in states],
                "entries": [dict(r) for r in entries],
                "recent_events": [dict(r) for r in events],
            }, ensure_ascii=False, default=str)
        except Exception as exc:
            log.error("habitat_snapshot: %s", exc)
            return f"error: {exc}"

    async def place_habitat_entry(
        self,
        *,
        bot_id: str,
        area: str,
        entry_type: str,
        title: str,
        content: str = "",
        source_type: str = "autonomous",
        source_ref: str = "",
        status: str = "active",
        suggested_actions: Optional[List[str]] = None,
        weight: float = 0.5,
        confidence: float = 0.7,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        area = str(area or "").strip().lower()
        entry_type = str(entry_type or "").strip().lower()
        status = str(status or "").strip().lower()
        source_type = str(source_type or "autonomous").strip().lower()
        if area not in VALID_HABITAT_AREAS:
            return f"error: invalid habitat area {area!r}; use one of {sorted(VALID_HABITAT_AREAS)}"
        if entry_type not in VALID_HABITAT_ENTRY_TYPES:
            return f"error: invalid habitat entry_type {entry_type!r}; use one of {sorted(VALID_HABITAT_ENTRY_TYPES)}"
        if status not in VALID_HABITAT_ENTRY_STATUSES:
            return f"error: invalid habitat status {status!r}; use one of {sorted(VALID_HABITAT_ENTRY_STATUSES)}"
        if source_type not in {"tool", "human", "autonomous", "memory", "creative", "system"}:
            return "error: invalid habitat source_type"
        if not str(title or "").strip():
            return "error: habitat title is required"
        if not str(reason or "").strip():
            return "error: habitat residue reason is required"
        weight = max(0.0, min(float(weight), 1.0))
        confidence = max(0.0, min(float(confidence), 1.0))
        try:
            await self.ensure_habitat_area(bot_id, area)
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO habitat_entries
                        (bot_id, area, entry_type, title, content, source_type, source_ref,
                         status, suggested_actions, weight, confidence, reason, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb)
                    RETURNING id
                    """,
                    bot_id,
                    area,
                    entry_type,
                    str(title).strip(),
                    content or None,
                    source_type,
                    source_ref or None,
                    status,
                    suggested_actions or [],
                    weight,
                    confidence,
                    reason,
                    json.dumps(metadata or {}),
                )
            return str(row["id"])
        except Exception as exc:
            log.error("place_habitat_entry: %s", exc)
            return f"error: {exc}"

    async def log_habitat_residue_decision(
        self,
        *,
        bot_id: str,
        tool_call_id: str,
        tool_name: str,
        phase: str,
        has_residue: bool,
        reason: str,
        area: str = "",
        entry_type: str = "",
        entry_id: str = "",
        confidence: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        tool_uuid = _uuid_text(tool_call_id) if tool_call_id else None
        entry_uuid = _uuid_text(entry_id) if entry_id else None
        if confidence is not None:
            confidence = max(0.0, min(float(confidence), 1.0))
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO habitat_residue_decisions
                        (bot_id, tool_call_id, tool_name, phase, has_residue,
                         area, entry_type, entry_id, reason, confidence, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                    RETURNING id
                    """,
                    bot_id,
                    tool_uuid,
                    tool_name,
                    phase or None,
                    bool(has_residue),
                    area or None,
                    entry_type or None,
                    entry_uuid,
                    reason,
                    confidence,
                    json.dumps(metadata or {}),
                )
            return str(row["id"])
        except Exception as exc:
            log.error("log_habitat_residue_decision: %s", exc)
            return f"error: {exc}"

    async def recent_habitat_residue_decisions(self, bot_id: str, limit: int = 10) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, tool_name, phase, has_residue, area, entry_type,
                           entry_id, reason, confidence, metadata, created_at
                    FROM habitat_residue_decisions
                    WHERE bot_id=$1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    bot_id, max(1, min(limit, 30)),
                )
            return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
        except Exception as exc:
            log.error("recent_habitat_residue_decisions: %s", exc)
            return f"error: {exc}"

    async def update_habitat_state(
        self,
        *,
        bot_id: str,
        area: str,
        state_patch: Optional[Dict[str, Any]] = None,
        note: str = "",
        trace_id: str = "",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        area = str(area or "").strip().lower()
        if area not in VALID_HABITAT_AREAS:
            return f"error: invalid habitat area {area!r}; use one of {sorted(VALID_HABITAT_AREAS)}"
        if not isinstance(state_patch, dict):
            return "error: state_patch must be an object"
        trace_uuid = _uuid_text(trace_id) if trace_id else None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO habitat_state (bot_id, area, state)
                    VALUES ($1, $2, $3::jsonb)
                    ON CONFLICT (bot_id, area) DO UPDATE
                        SET state = habitat_state.state || EXCLUDED.state,
                            updated_at = now()
                    RETURNING id
                    """,
                    bot_id, area, json.dumps(state_patch or {}),
                )
                if note:
                    await conn.execute(
                        """
                        INSERT INTO habitat_events (bot_id, area, action, content, metadata, trace_id)
                        VALUES ($1, $2, 'state_update', $3, $4::jsonb, $5)
                        """,
                        bot_id, area, note, json.dumps({"state_patch": state_patch or {}}), trace_uuid,
                    )
            return str(row["id"])
        except Exception as exc:
            log.error("update_habitat_state: %s", exc)
            return f"error: {exc}"

    async def log_habitat_event(
        self,
        *,
        bot_id: str,
        area: str,
        action: str,
        content: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: str = "",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        area = str(area or "").strip().lower()
        action = str(action or "").strip().lower()
        if area not in VALID_HABITAT_AREAS:
            return f"error: invalid habitat area {area!r}; use one of {sorted(VALID_HABITAT_AREAS)}"
        if not action:
            return "error: habitat action is required"
        trace_uuid = _uuid_text(trace_id) if trace_id else None
        try:
            await self.ensure_habitat_area(bot_id, area)
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO habitat_events (bot_id, area, action, content, metadata, trace_id)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                    RETURNING id
                    """,
                    bot_id, area, action, content or None, json.dumps(metadata or {}), trace_uuid,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("log_habitat_event: %s", exc)
            return f"error: {exc}"

    async def log_habitat_tool_echo(
        self,
        *,
        bot_id: str,
        tool_call_id: str,
        tool_name: str,
        area: str,
        action: str,
        content: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        area = str(area or "").strip().lower()
        action = str(action or "").strip().lower()
        if area not in VALID_HABITAT_AREAS:
            return f"error: invalid habitat area {area!r}; use one of {sorted(VALID_HABITAT_AREAS)}"
        if not action:
            return "error: habitat action is required"
        meta = dict(metadata or {})
        meta.update({
            "source": "tool_echo",
            "tool_call_id": str(tool_call_id),
            "tool_name": str(tool_name),
        })
        try:
            await self.ensure_habitat_area(bot_id, area)
            async with self._pool.acquire() as conn:
                existing = await conn.fetchval(
                    """
                    SELECT id
                    FROM habitat_events
                    WHERE bot_id=$1
                      AND metadata->>'source'='tool_echo'
                      AND metadata->>'tool_call_id'=$2
                    LIMIT 1
                    """,
                    bot_id, str(tool_call_id),
                )
                if existing:
                    return str(existing)
                row = await conn.fetchrow(
                    """
                    INSERT INTO habitat_events (bot_id, area, action, content, metadata)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    RETURNING id
                    """,
                    bot_id, area, action, content or None, json.dumps(meta),
                )
            return str(row["id"])
        except Exception as exc:
            log.error("log_habitat_tool_echo: %s", exc)
            return f"error: {exc}"

    async def read_posture(self) -> Dict[str, Any]:
        if not self.available:
            return {}
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("SELECT key, value FROM posture_state")
            return {r["key"]: r["value"] for r in rows}
        except Exception as exc:
            log.error("read_posture: %s", exc)
            return {}

    async def update_posture(self, key: str, value: Any) -> str:
        if not self.available:
            return "[no-postgres]"
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO posture_state (key, value, updated_at)
                    VALUES ($1, $2::jsonb, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                    """,
                    key, json.dumps(value),
                )
            return f"posture.{key} updated"
        except Exception as exc:
            log.error("update_posture: %s", exc)
            return f"error: {exc}"

    # ── vestibule ─────────────────────────────────────────────────────

    # -- RA memory layers -------------------------------------------------

    async def store_human_memory(
        self,
        *,
        bot_id: str,
        human_id: str,
        memory_type: str,
        content: str,
        confidence: float = 0.5,
        consent_status: str = "inferred_low_risk",
        status: str = "active",
        tags: Optional[List[str]] = None,
        trace_id: str = "",
        admission_category: str = "useful_continuity",
        admission_reason: str = "",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        if admission_category not in VALID_ADMISSION_CATEGORIES:
            return f"error: invalid admission_category {admission_category!r}; use one of {sorted(VALID_ADMISSION_CATEGORIES)}"
        if not admission_reason.strip():
            return "error: admission_reason is required for human memory writes"
        embedding = await self._embed(content)
        trace_uuid = _uuid_text(trace_id) if trace_id else None
        admission_tags = list(tags or [])
        for tag in (f"admission:{admission_category}", f"reason:{_context_key(admission_reason)[:40]}"):
            if tag not in admission_tags:
                admission_tags.append(tag)
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO human_memory
                        (bot_id, human_id, memory_type, content, embedding, confidence,
                         consent_status, status, trace_id, tags)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    bot_id, human_id, memory_type, content, embedding,
                    confidence, consent_status, status, trace_uuid, admission_tags,
                )
                await conn.execute(
                    """
                    INSERT INTO memory_admissions
                        (bot_id, human_id, target_table, target_id,
                         admission_category, admission_reason, source)
                    VALUES ($1, $2, 'human_memory', $3, $4, $5, 'bot')
                    """,
                    bot_id, human_id, row["id"], admission_category, admission_reason,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("store_human_memory: %s", exc)
            return f"error: {exc}"

    async def store_human_notebook(
        self,
        *,
        bot_id: str,
        human_id: str,
        entry_type: str,
        content: str,
        title: str = "",
        due_at: Optional[str] = None,
        recurrence: str = "",
        consent_status: str = "explicit",
        status: str = "active",
        tags: Optional[List[str]] = None,
        trace_id: str = "",
        admission_category: str = "explicit_tracking",
        admission_reason: str = "",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        if admission_category not in VALID_ADMISSION_CATEGORIES:
            return f"error: invalid admission_category {admission_category!r}; use one of {sorted(VALID_ADMISSION_CATEGORIES)}"
        if not admission_reason.strip():
            return "error: admission_reason is required for human notebook writes"
        embedding = await self._embed(content)
        trace_uuid = _uuid_text(trace_id) if trace_id else None
        admission_tags = list(tags or [])
        for tag in (f"admission:{admission_category}", f"reason:{_context_key(admission_reason)[:40]}"):
            if tag not in admission_tags:
                admission_tags.append(tag)
        parsed_due = None
        if due_at:
            try:
                from dateutil.parser import parse as _parse
                parsed_due = _parse(due_at)
            except Exception:
                parsed_due = None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO human_notebook
                        (bot_id, human_id, entry_type, title, content, due_at,
                         recurrence, consent_status, status, trace_id, embedding, tags)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    RETURNING id
                    """,
                    bot_id, human_id, entry_type, title or None, content,
                    parsed_due, recurrence or None, consent_status, status,
                    trace_uuid, embedding, admission_tags,
                )
                await conn.execute(
                    """
                    INSERT INTO memory_admissions
                        (bot_id, human_id, target_table, target_id,
                         admission_category, admission_reason, source)
                    VALUES ($1, $2, 'human_notebook', $3, $4, $5, 'bot')
                    """,
                    bot_id, human_id, row["id"], admission_category, admission_reason,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("store_human_notebook: %s", exc)
            return f"error: {exc}"

    async def recent_memory_admissions(self, bot_id: str, human_id: str = "", limit: int = 10) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, human_id, target_table, target_id,
                           admission_category, admission_reason, source, created_at
                    FROM memory_admissions
                    WHERE bot_id=$1 AND ($2='' OR human_id=$2)
                    ORDER BY created_at DESC
                    LIMIT $3
                    """,
                    bot_id, human_id, max(1, min(limit, 30)),
                )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("recent_memory_admissions: %s", exc)
            return f"error: {exc}"

    async def due_notebook_items(self, bot_id: str, limit: int = 10) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, bot_id, human_id, entry_type, title, content, due_at,
                           recurrence, status, consent_status, tags, created_at, updated_at
                    FROM human_notebook n
                    WHERE bot_id=$1
                      AND status='active'
                      AND due_at IS NOT NULL
                      AND due_at <= now()
                      AND consent_status <> 'denied'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM initiation_attempts i
                          WHERE i.bot_id=n.bot_id
                            AND i.target_table='human_notebook'
                            AND i.target_id=n.id
                            AND i.status='sent'
                      )
                    ORDER BY due_at ASC
                    LIMIT $2
                    """,
                    bot_id, max(1, min(limit, 30)),
                )
            return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
        except Exception as exc:
            log.error("due_notebook_items: %s", exc)
            return f"error: {exc}"

    async def complete_notebook_item(self, bot_id: str, notebook_id: str, reason: str = "") -> str:
        if not self.available:
            return "[no-postgres]"
        notebook_uuid = _uuid_text(notebook_id)
        if not notebook_uuid:
            return "error: invalid notebook_id"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE human_notebook
                    SET status='completed',
                        updated_at=now(),
                        tags = CASE
                            WHEN $3='' THEN tags
                            ELSE array_append(tags, $3)
                        END
                    WHERE id=$1 AND bot_id=$2
                    RETURNING id
                    """,
                    notebook_uuid, bot_id, f"completed:{_context_key(reason)[:40]}" if reason else "",
                )
            return str(row["id"]) if row else "error: notebook item not found"
        except Exception as exc:
            log.error("complete_notebook_item: %s", exc)
            return f"error: {exc}"

    async def recent_initiation_attempts(self, bot_id: str, human_id: str = "", limit: int = 10) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, human_id, target_table, target_id, initiation_type,
                           channel_type, status, reason, message_preview, error,
                           metadata, created_at
                    FROM initiation_attempts
                    WHERE bot_id=$1 AND ($2='' OR human_id=$2)
                    ORDER BY created_at DESC
                    LIMIT $3
                    """,
                    bot_id, human_id, max(1, min(limit, 30)),
                )
            return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
        except Exception as exc:
            log.error("recent_initiation_attempts: %s", exc)
            return f"error: {exc}"

    async def log_initiation_attempt(
        self,
        *,
        bot_id: str,
        human_id: str = "",
        target_table: str = "",
        target_id: str = "",
        initiation_type: str,
        channel_type: str = "dm",
        status: str,
        reason: str,
        message_preview: str = "",
        error: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        target_uuid = _uuid_text(target_id) if target_id else None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO initiation_attempts
                        (bot_id, human_id, target_table, target_id, initiation_type,
                         channel_type, status, reason, message_preview, error, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                    RETURNING id
                    """,
                    bot_id,
                    human_id or None,
                    target_table or None,
                    target_uuid,
                    initiation_type,
                    channel_type,
                    status,
                    reason,
                    message_preview[:500] if message_preview else None,
                    error[:500] if error else None,
                    json.dumps(metadata or {}),
                )
            return str(row["id"])
        except Exception as exc:
            log.error("log_initiation_attempt: %s", exc)
            return f"error: {exc}"

    async def resolve_memory_id(self, target_table: str, bot_id: str, id_prefix: str) -> str:
        if not self.available:
            return "[no-postgres]"
        table = {
            "human_memory": "human_memory",
            "human_notebook": "human_notebook",
            "bot_self_memory": "bot_self_memory",
        }.get(target_table)
        if not table:
            return "error: invalid target_table"
        prefix = str(id_prefix or "").strip().lower()
        full_uuid = _uuid_text(prefix)
        if full_uuid:
            prefix = full_uuid
        if len(prefix) < 8:
            return "error: memory id prefix must be at least 8 characters"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT id
                    FROM {table}
                    WHERE bot_id=$1 AND lower(id::text) LIKE $2
                    ORDER BY created_at DESC
                    LIMIT 3
                    """,
                    bot_id, f"{prefix}%",
                )
            if not rows:
                return f"error: no {target_table} row matches `{prefix}`"
            if len(rows) > 1:
                return f"error: id prefix `{prefix}` is ambiguous; use more characters"
            return str(rows[0]["id"])
        except Exception as exc:
            log.error("resolve_memory_id: %s", exc)
            return f"error: {exc}"

    async def record_curator_action(
        self,
        *,
        bot_id: str,
        target_table: str,
        target_id: str,
        action: str,
        reason: str,
        curator_id: str,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        if target_table not in {"human_memory", "human_notebook", "bot_self_memory"}:
            return "error: invalid target_table"
        target_uuid = _uuid_text(target_id)
        if not target_uuid:
            return "error: invalid target_id"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO memory_curator_actions
                        (bot_id, target_table, target_id, action, reason, curator_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    bot_id, target_table, target_uuid, action, reason, curator_id,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("record_curator_action: %s", exc)
            return f"error: {exc}"

    async def update_curated_memory_status(
        self,
        *,
        bot_id: str,
        target_table: str,
        target_id: str,
        status: str,
        reason: str,
        curator_id: str,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        table = {
            "human_memory": "human_memory",
            "human_notebook": "human_notebook",
        }.get(target_table)
        if not table:
            return "error: curator status updates support human_memory or human_notebook"
        valid_statuses = {
            "human_memory": {"active", "provisional", "archived", "deleted"},
            "human_notebook": {"active", "completed", "archived", "deleted"},
        }[table]
        if status not in valid_statuses:
            return f"error: invalid status {status!r} for {target_table}"
        resolved = await self.resolve_memory_id(target_table, bot_id, target_id)
        if resolved.startswith("error:") or resolved.startswith("[no-postgres]"):
            return resolved
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    UPDATE {table}
                    SET status=$1, updated_at=now()
                    WHERE id=$2 AND bot_id=$3
                    RETURNING id
                    """,
                    status, resolved, bot_id,
                )
                if not row:
                    return "error: memory row not found"
                await conn.execute(
                    """
                    INSERT INTO memory_curator_actions
                        (bot_id, target_table, target_id, action, reason, curator_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    bot_id, target_table, resolved, f"set_status:{status}", reason, curator_id,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("update_curated_memory_status: %s", exc)
            return f"error: {exc}"

    async def recent_curator_actions(self, bot_id: str, limit: int = 10) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, target_table, target_id, action, reason, curator_id, created_at
                    FROM memory_curator_actions
                    WHERE bot_id=$1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    bot_id, max(1, min(limit, 30)),
                )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("recent_curator_actions: %s", exc)
            return f"error: {exc}"

    async def store_bot_self_memory_candidate(
        self,
        *,
        bot_id: str,
        memory_type: str,
        content: str,
        confidence: float = 0.4,
        source_kind: str = "bot",
        identity_relevant: bool = False,
        promotion_reason: str = "",
        tags: Optional[List[str]] = None,
        trace_id: str = "",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        if source_kind == "human":
            return "error: bot_self_memory cannot be human-authored; route human material to human_memory, influence_events, or notebook."
        embedding = await self._embed(content)
        trace_uuid = _uuid_text(trace_id) if trace_id else None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO bot_self_memory
                        (bot_id, memory_type, content, embedding, confidence,
                         promotion_status, source_actor, source_kind, human_authored,
                         identity_relevant, promotion_reason, trace_id, tags)
                    VALUES ($1, $2, $3, $4, $5, 'candidate', $6, $7, false, $8, $9, $10, $11)
                    RETURNING id
                    """,
                    bot_id, memory_type, content, embedding, confidence,
                    bot_id, source_kind, identity_relevant,
                    promotion_reason or None, trace_uuid, tags or [],
                )
            return str(row["id"])
        except Exception as exc:
            log.error("store_bot_self_memory_candidate: %s", exc)
            return f"error: {exc}"

    async def recent_layered_memory(
        self,
        layer: str,
        bot_id: str,
        human_id: str = "",
        limit: int = 5,
        include_terminal: bool = False,
    ) -> str:
        if not self.available:
            return "[]"
        limit = max(1, min(limit, 20))
        try:
            async with self._pool.acquire() as conn:
                if layer == "human_memory":
                    rows = await conn.fetch(
                        """
                        SELECT id, memory_type, content, confidence, consent_status, status, tags, created_at
                        FROM human_memory
                        WHERE bot_id=$1 AND ($2='' OR human_id=$2)
                          AND ($4::bool OR status NOT IN ('archived', 'deleted'))
                        ORDER BY created_at DESC
                        LIMIT $3
                        """,
                        bot_id, human_id, limit, include_terminal,
                    )
                elif layer == "human_notebook":
                    rows = await conn.fetch(
                        """
                        SELECT id, entry_type, title, content, due_at, recurrence, status, tags, created_at
                        FROM human_notebook
                        WHERE bot_id=$1 AND ($2='' OR human_id=$2)
                          AND ($4::bool OR status NOT IN ('completed', 'archived', 'deleted'))
                        ORDER BY created_at DESC
                        LIMIT $3
                        """,
                        bot_id, human_id, limit, include_terminal,
                    )
                elif layer == "bot_self_memory":
                    rows = await conn.fetch(
                        """
                        SELECT id, memory_type, content, confidence, promotion_status,
                               source_kind, identity_relevant, tags, created_at
                        FROM bot_self_memory
                        WHERE bot_id=$1
                          AND ($2::bool OR promotion_status NOT IN ('rejected', 'archived', 'discarded'))
                        ORDER BY created_at DESC
                        LIMIT $3
                        """,
                        bot_id, include_terminal, limit,
                    )
                else:
                    return "error: layer must be human_memory, human_notebook, or bot_self_memory"
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("recent_layered_memory: %s", exc)
            return f"error: {exc}"

    async def ensure_memory_context(
        self,
        bot_id: str,
        key: str = "general",
        title: str = "General",
        summary: str = "Neutral default memory context.",
        created_by: str = "system",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        key = _context_key(key)
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO memory_contexts (bot_id, key, title, summary, created_by)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (bot_id, key) DO UPDATE
                        SET updated_at = now()
                    RETURNING id
                    """,
                    bot_id, key, title, summary or None, created_by,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("ensure_memory_context: %s", exc)
            return f"error: {exc}"

    async def list_memory_contexts(self, bot_id: str, include_archived: bool = False) -> str:
        if not self.available:
            return "[]"
        await self.ensure_memory_context(bot_id)
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, key, title, summary, status, created_by, created_at, updated_at
                    FROM memory_contexts
                    WHERE bot_id=$1 AND ($2::bool OR status <> 'archived')
                    ORDER BY key
                    """,
                    bot_id, include_archived,
                )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("list_memory_contexts: %s", exc)
            return f"error: {exc}"

    async def review_candidates(self, bot_id: str, limit: int = 8, include_identity: bool = True) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT b.id, b.memory_type, b.content, b.confidence, b.promotion_status,
                           source_kind, human_authored, identity_relevant,
                           recurrence_count, promotion_reason, b.created_at, b.tags,
                           c.key AS context_key, c.title AS context_title
                    FROM bot_self_memory b
                    LEFT JOIN memory_contexts c ON c.id = b.context_id
                    WHERE b.bot_id=$1
                      AND b.promotion_status IN ('candidate', 'provisional', 'held_open')
                      AND ($2::bool OR identity_relevant = false)
                    ORDER BY b.identity_relevant DESC, b.created_at ASC
                    LIMIT $3
                    """,
                    bot_id, include_identity, max(1, min(limit, 20)),
                )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("review_candidates: %s", exc)
            return f"error: {exc}"

    async def decide_memory_review(
        self,
        *,
        bot_id: str,
        candidate_id: str,
        decision: str,
        reason: str,
        context_key: str = "general",
        reviewed_by: str = "bot",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        candidate_uuid = _uuid_text(candidate_id)
        if not candidate_uuid:
            return "error: invalid candidate_id"
        if not reason.strip():
            return "error: review reason is required"
        context_id = await self.ensure_memory_context(bot_id, key=context_key)
        if context_id.startswith("error:"):
            return context_id
        try:
            async with self._pool.acquire() as conn:
                candidate = await conn.fetchrow(
                    """
                    SELECT id, promotion_status, source_kind, human_authored,
                           identity_relevant, recurrence_count, created_at
                    FROM bot_self_memory
                    WHERE id=$1 AND bot_id=$2
                    """,
                    candidate_uuid, bot_id,
                )
                if not candidate:
                    return "error: candidate not found"
                allowed, error = _review_allowed(dict(candidate), decision)
                if not allowed:
                    return f"error: {error}"
                new_status = _status_for_decision(str(candidate["promotion_status"]), decision)
                review = await conn.fetchrow(
                    """
                    INSERT INTO memory_promotion_reviews
                        (bot_id, candidate_table, candidate_id, decision, reason, context_id, reviewed_by)
                    VALUES ($1, 'bot_self_memory', $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    bot_id, candidate_uuid, decision, reason, context_id, reviewed_by,
                )
                if new_status:
                    if decision == "reinforce":
                        await conn.execute(
                            """
                            UPDATE bot_self_memory
                            SET promotion_status=$1,
                                context_id=$2,
                                promotion_reason=$3,
                                recurrence_count=recurrence_count + 1,
                                last_reinforced_at=now(),
                                status_updated_at=now()
                            WHERE id=$4
                            """,
                            new_status, context_id, reason, candidate_uuid,
                        )
                    else:
                        await conn.execute(
                            """
                            UPDATE bot_self_memory
                            SET promotion_status=$1,
                                context_id=$2,
                                promotion_reason=$3,
                                status_updated_at=now()
                            WHERE id=$4
                            """,
                            new_status, context_id, reason, candidate_uuid,
                        )
            return str(review["id"])
        except Exception as exc:
            log.error("decide_memory_review: %s", exc)
            return f"error: {exc}"

    # -- RA routing trace -------------------------------------------------

    async def log_interaction_trace(
        self,
        *,
        event_id: str = "",
        bot_id: str,
        human_id: str = "",
        channel: str = "",
        incoming_preview: str = "",
        selected_mode: str = "answer",
        weather_snapshot: Optional[Dict[str, Any]] = None,
        coherence_snapshot: Optional[Dict[str, Any]] = None,
        memory_writes: Optional[List[Dict[str, Any]]] = None,
        reasoning_summary: str = "",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        event_uuid = _uuid_text(event_id) if event_id else None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO interaction_trace
                        (event_id, bot_id, human_id, channel, incoming_preview,
                         selected_mode, weather_snapshot, coherence_snapshot,
                         memory_writes, reasoning_summary)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10)
                    RETURNING id
                    """,
                    event_uuid,
                    bot_id,
                    human_id or None,
                    channel or None,
                    incoming_preview[:1000],
                    selected_mode,
                    json.dumps(weather_snapshot or {}),
                    json.dumps(coherence_snapshot or {}),
                    json.dumps(memory_writes or []),
                    reasoning_summary,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("log_interaction_trace: %s", exc)
            return f"error: {exc}"

    async def log_influence_event(
        self,
        *,
        trace_id: str,
        bot_id: str,
        human_id: str = "",
        influence_type: str,
        target_layer: str,
        content: str,
        confidence: float = 0.5,
        identity_write_allowed: bool = False,
        memory_write_allowed: bool = False,
        notes: str = "",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        trace_uuid = _uuid_text(trace_id)
        if not trace_uuid:
            return "error: invalid trace_id"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO influence_events
                        (trace_id, bot_id, human_id, influence_type, target_layer,
                         content, confidence, identity_write_allowed,
                         memory_write_allowed, notes)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    trace_uuid,
                    bot_id,
                    human_id or None,
                    influence_type,
                    target_layer,
                    content,
                    confidence,
                    identity_write_allowed,
                    memory_write_allowed,
                    notes or None,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("log_influence_event: %s", exc)
            return f"error: {exc}"

    async def log_role_invitation(
        self,
        *,
        trace_id: str,
        bot_id: str,
        human_id: str = "",
        proposed_role: str,
        invitation_text: str,
        action: str,
        bot_memory_weight: float = 0.0,
        human_memory_weight: float = 0.2,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        trace_uuid = _uuid_text(trace_id)
        if not trace_uuid:
            return "error: invalid trace_id"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO role_invitations
                        (trace_id, bot_id, human_id, proposed_role, invitation_text,
                         action, identity_write_allowed, bot_memory_weight, human_memory_weight)
                    VALUES ($1, $2, $3, $4, $5, $6, false, $7, $8)
                    RETURNING id
                    """,
                    trace_uuid,
                    bot_id,
                    human_id or None,
                    proposed_role,
                    invitation_text,
                    action,
                    bot_memory_weight,
                    human_memory_weight,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("log_role_invitation: %s", exc)
            return f"error: {exc}"

    async def recent_interaction_traces(self, limit: int = 3) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        t.id, t.bot_id, t.human_id, t.channel, t.incoming_preview,
                        t.selected_mode, t.weather_snapshot, t.coherence_snapshot,
                        t.reasoning_summary, t.created_at,
                        COALESCE(
                            jsonb_agg(DISTINCT jsonb_build_object(
                                'type', i.influence_type,
                                'target_layer', i.target_layer,
                                'identity_write_allowed', i.identity_write_allowed,
                                'memory_write_allowed', i.memory_write_allowed,
                                'notes', i.notes
                            )) FILTER (WHERE i.id IS NOT NULL),
                            '[]'::jsonb
                        ) AS influences,
                        COALESCE(
                            jsonb_agg(DISTINCT jsonb_build_object(
                                'proposed_role', r.proposed_role,
                                'action', r.action,
                                'identity_write_allowed', r.identity_write_allowed,
                                'bot_memory_weight', r.bot_memory_weight
                            )) FILTER (WHERE r.id IS NOT NULL),
                            '[]'::jsonb
                        ) AS role_invitations
                    FROM interaction_trace t
                    LEFT JOIN influence_events i ON i.trace_id = t.id
                    LEFT JOIN role_invitations r ON r.trace_id = t.id
                    GROUP BY t.id
                    ORDER BY t.created_at DESC
                    LIMIT $1
                    """,
                    max(1, min(limit, 10)),
                )
            records = []
            for row in rows:
                item = dict(row)
                item["weather_snapshot"] = _json_value(item.get("weather_snapshot"))
                item["coherence_snapshot"] = _json_value(item.get("coherence_snapshot"))
                item["influences"] = _json_value(item.get("influences"))
                item["role_invitations"] = _json_value(item.get("role_invitations"))
                records.append(item)
            return json.dumps(records, default=str)
        except Exception as exc:
            log.error("recent_interaction_traces: %s", exc)
            return "[]"

    async def trace_aggregate(self, bot_id: str = "", days: int = 7) -> str:
        if not self.available:
            return "{}"
        days = max(1, min(days, 90))
        try:
            async with self._pool.acquire() as conn:
                summary = await conn.fetchrow(
                    """
                    SELECT
                        COUNT(*) AS trace_count,
                        COUNT(DISTINCT human_id) FILTER (WHERE human_id IS NOT NULL) AS human_count,
                        COALESCE(AVG((coherence_snapshot->>'pressure')::float), 0) AS avg_pressure,
                        COUNT(*) FILTER (WHERE (coherence_snapshot->>'identity_write_allowed')::bool = true) AS identity_write_allowed_count
                    FROM interaction_trace
                    WHERE created_at >= now() - ($1::int * interval '1 day')
                      AND ($2 = '' OR bot_id = $2)
                    """,
                    days, bot_id,
                )
                modes = await conn.fetch(
                    """
                    SELECT selected_mode, COUNT(*) AS count
                    FROM interaction_trace
                    WHERE created_at >= now() - ($1::int * interval '1 day')
                      AND ($2 = '' OR bot_id = $2)
                    GROUP BY selected_mode
                    ORDER BY count DESC, selected_mode
                    """,
                    days, bot_id,
                )
                roles = await conn.fetch(
                    """
                    SELECT proposed_role, action, COUNT(*) AS count
                    FROM role_invitations
                    WHERE created_at >= now() - ($1::int * interval '1 day')
                      AND ($2 = '' OR bot_id = $2)
                    GROUP BY proposed_role, action
                    ORDER BY count DESC, proposed_role, action
                    LIMIT 12
                    """,
                    days, bot_id,
                )
                influences = await conn.fetch(
                    """
                    SELECT influence_type, target_layer, COUNT(*) AS count
                    FROM influence_events
                    WHERE created_at >= now() - ($1::int * interval '1 day')
                      AND ($2 = '' OR bot_id = $2)
                    GROUP BY influence_type, target_layer
                    ORDER BY count DESC, influence_type, target_layer
                    LIMIT 12
                    """,
                    days, bot_id,
                )
                bots = await conn.fetch(
                    """
                    SELECT bot_id, COUNT(*) AS trace_count
                    FROM interaction_trace
                    WHERE created_at >= now() - ($1::int * interval '1 day')
                    GROUP BY bot_id
                    ORDER BY trace_count DESC, bot_id
                    """,
                    days,
                )
            payload = {
                "bot_id": bot_id or "all",
                "days": days,
                "summary": dict(summary or {}),
                "modes": [dict(r) for r in modes],
                "roles": [dict(r) for r in roles],
                "influences": [dict(r) for r in influences],
                "bots": [dict(r) for r in bots],
            }
            return json.dumps(payload, default=str)
        except Exception as exc:
            log.error("trace_aggregate: %s", exc)
            return f"error: {exc}"

    async def trace_compare(self, days: int = 7) -> str:
        if not self.available:
            return "[]"
        days = max(1, min(days, 90))
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    WITH base AS (
                        SELECT
                            bot_id,
                            COUNT(*) AS trace_count,
                            COUNT(DISTINCT human_id) FILTER (WHERE human_id IS NOT NULL) AS human_count,
                            COALESCE(AVG((coherence_snapshot->>'pressure')::float), 0) AS avg_pressure,
                            COUNT(*) FILTER (WHERE (coherence_snapshot->>'identity_write_allowed')::bool = true) AS identity_write_allowed_count
                        FROM interaction_trace
                        WHERE created_at >= now() - ($1::int * interval '1 day')
                        GROUP BY bot_id
                    ),
                    role_counts AS (
                        SELECT bot_id, COUNT(*) AS role_count
                        FROM role_invitations
                        WHERE created_at >= now() - ($1::int * interval '1 day')
                        GROUP BY bot_id
                    ),
                    influence_counts AS (
                        SELECT bot_id, COUNT(*) AS influence_count
                        FROM influence_events
                        WHERE created_at >= now() - ($1::int * interval '1 day')
                        GROUP BY bot_id
                    ),
                    top_modes AS (
                        SELECT bot_id, selected_mode, COUNT(*) AS mode_count,
                               ROW_NUMBER() OVER (PARTITION BY bot_id ORDER BY COUNT(*) DESC, selected_mode) AS rank
                        FROM interaction_trace
                        WHERE created_at >= now() - ($1::int * interval '1 day')
                        GROUP BY bot_id, selected_mode
                    )
                    SELECT
                        b.bot_id,
                        b.trace_count,
                        b.human_count,
                        b.avg_pressure,
                        b.identity_write_allowed_count,
                        COALESCE(r.role_count, 0) AS role_count,
                        COALESCE(i.influence_count, 0) AS influence_count,
                        tm.selected_mode AS top_mode,
                        tm.mode_count AS top_mode_count
                    FROM base b
                    LEFT JOIN role_counts r ON r.bot_id = b.bot_id
                    LEFT JOIN influence_counts i ON i.bot_id = b.bot_id
                    LEFT JOIN top_modes tm ON tm.bot_id = b.bot_id AND tm.rank = 1
                    ORDER BY b.trace_count DESC, b.bot_id
                    """,
                    days,
                )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("trace_compare: %s", exc)
            return f"error: {exc}"

    async def hold_interpretation(
        self,
        interpretation_id: str,
        held_reason: str = "",
        revisit_after: Optional[str] = None,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        interpretation_id = _uuid_text(interpretation_id)
        if not interpretation_id:
            return "error: invalid interpretation_id; use a full UUID returned by memory_interpret, memory_recent, or memory_search"
        try:
            ts = None
            if revisit_after:
                try:
                    from dateutil.parser import parse as _parse
                    ts = _parse(revisit_after)
                except Exception:
                    ts = None
            async with self._pool.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM memory_interpretations WHERE id=$1)",
                    interpretation_id,
                )
                if not exists:
                    return f"error: interpretation_id not found: {interpretation_id}; use memory_recent/search or store it first with memory_interpret"
                row = await conn.fetchrow(
                    """
                    INSERT INTO vestibule_held (interpretation_id, held_reason, revisit_after)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (interpretation_id) DO UPDATE
                        SET held_reason = EXCLUDED.held_reason,
                            revisit_after = EXCLUDED.revisit_after
                    RETURNING id
                    """,
                    interpretation_id, held_reason or None, ts,
                )
                await conn.execute(
                    "UPDATE memory_interpretations SET status='held_open' WHERE id=$1",
                    interpretation_id,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("hold_interpretation: %s", exc)
            return f"error: {exc}"

    async def check_vestibule(self, limit: int = 5) -> str:
        if not self.available:
            return "[]"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT v.id, v.interpretation_id, v.held_reason, v.revisit_after,
                           m.content, m.type, m.confidence
                    FROM vestibule_held v
                    JOIN memory_interpretations m ON m.id = v.interpretation_id
                    WHERE (v.revisit_after IS NULL OR v.revisit_after <= now())
                    ORDER BY v.revisit_after ASC NULLS FIRST
                    LIMIT $1
                    """,
                    limit,
                )
                # bump revisit counts
                ids = [str(r["id"]) for r in rows]
                if ids:
                    await conn.execute(
                        """
                        UPDATE vestibule_held
                        SET revisit_count = revisit_count + 1, last_revisited_at = now()
                        WHERE id = ANY($1::uuid[])
                        """,
                        ids,
                    )
            return json.dumps([dict(r) for r in rows], default=str)
        except Exception as exc:
            log.error("check_vestibule: %s", exc)
            return "[]"

    # ── deferred responses ────────────────────────────────────────────

    async def defer_response(
        self,
        incoming_text: str,
        author: str,
        channel: str,
        answer_after: Optional[str] = None,
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        try:
            ts = answer_after or _utc()
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO deferred_responses (incoming_text, author, channel, answer_after)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    incoming_text, author, channel, ts,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("defer_response: %s", exc)
            return f"error: {exc}"

    async def pending_deferred(self, limit: int = 5) -> List[Dict[str, Any]]:
        if not self.available:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, incoming_text, author, channel, answer_after
                    FROM deferred_responses
                    WHERE status = 'pending' AND answer_after <= now()
                    ORDER BY answer_after ASC
                    LIMIT $1
                    """,
                    limit,
                )
            return [dict(r) for r in rows]
        except Exception as exc:
            log.error("pending_deferred: %s", exc)
            return []

    async def mark_answered(self, id: str, answer_text: str) -> str:
        if not self.available:
            return "[no-postgres]"
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE deferred_responses
                    SET status='answered', answered_at=now(), answer_text=$1
                    WHERE id=$2
                    """,
                    answer_text, id,
                )
            return f"marked answered: {id}"
        except Exception as exc:
            log.error("mark_answered: %s", exc)
            return f"error: {exc}"

    # ── events + tool calls ───────────────────────────────────────────

    async def recent_conversations(self, limit: int = 8) -> List[Dict[str, Any]]:
        """Fetch recent human/bot turn pairs from the events table, chronological."""
        if not self.available:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT source_type, source_actor, content, created_at
                    FROM events
                    WHERE source_type IN ('human_turn', 'bot_turn')
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            records = []
            for r in reversed(rows):
                content = r["content"] or ""
                if r["source_type"] == "human_turn" and (
                    is_no_reply_marker(content) or is_acknowledgement_only(content)
                ):
                    continue
                records.append({
                    "source_type": r["source_type"],
                    "source_actor": r["source_actor"] or "",
                    "content": content,
                    "created_at": r["created_at"].isoformat() if r["created_at"] else "",
                })
            return records
        except Exception as exc:
            log.error("recent_conversations: %s", exc)
            return []

    async def log_event(
        self,
        source_type: str,
        content: str,
        source_actor: str = "bot",
        channel: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        bot_id: str = "",
        human_id: str = "",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO events (source_type, source_actor, channel, content, metadata, bot_id, human_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    source_type, source_actor, channel or None,
                    content, json.dumps(metadata or {}), bot_id or None, human_id or None,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("log_event: %s", exc)
            return f"error: {exc}"

    async def log_tool_call(
        self,
        event_id: str,
        tool_name: str,
        phase: str,
        args: Dict[str, Any],
        result_preview: str = "",
        success: bool = True,
        bot_id: str = "",
    ) -> str:
        if not self.available:
            return "[no-postgres]"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO tool_calls (event_id, tool_name, phase, args, result_preview, success, bot_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    event_id or None, tool_name, phase,
                    json.dumps(args), result_preview[:500], success, bot_id or None,
                )
            return str(row["id"])
        except Exception as exc:
            log.error("log_tool_call: %s", exc)
            return f"error: {exc}"

    async def recent_tool_calls(self, bot_id: str = "", limit: int = 8) -> str:
        if not self.available:
            return "[]"
        action_tools = [
            "creation_store", "posture_update",
            "memory_review_decide", "memory_context_create",
            "game_status", "game_act",
        ]
        try:
            async with self._pool.acquire() as conn:
                if bot_id:
                    rows = await conn.fetch(
                        """
                        SELECT tool_name, phase, args, result_preview, success, created_at
                        FROM tool_calls
                        WHERE (bot_id = $1 OR bot_id IS NULL)
                          AND tool_name = ANY($3::text[])
                        ORDER BY created_at DESC
                        LIMIT $2
                        """,
                        bot_id, max(1, min(limit, 20)), action_tools,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT tool_name, phase, args, result_preview, success, created_at
                        FROM tool_calls
                        WHERE tool_name = ANY($2::text[])
                        ORDER BY created_at DESC
                        LIMIT $1
                        """,
                        max(1, min(limit, 20)), action_tools,
                    )
            return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
        except Exception as exc:
            log.error("recent_tool_calls: %s", exc)
            return f"error: {exc}"
