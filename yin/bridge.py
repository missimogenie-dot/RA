"""
YinStore — the local store cognition and bot talk to.

Drop-in replacement for Ra's BotPostgres, backed by Yin's organs:
SQLite for logs, JSON lanes for memory, habitat, creations, notebook,
vestibule. Method names and return shapes mirror the Postgres class so
the swap is one wiring change in runtime.py.

Ra machinery the v2 design dropped (bot-self candidate pipeline, memory
review, identity threads, curator, traces) answers honestly here: it
names what does work instead of pretending or dead-ending. Those tools
leave the registry entirely during the Yin tool rewrite.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .habitat import Habitat
from .logs import YinLogs
from .memory.creations import Creations
from .memory.human import HumanMemory
from .memory.jsonstore import load_entries, make_entry, save_entries, utc_now
from .memory.notebook import Notebook
from .memory.paths import lane_path
from .memory.recall import YinMemory
from .memory.vestibule import Vestibule
from .memory.working import WorkingMemory

log = logging.getLogger("yin.bridge")

NO_PIPELINE = (
    "The candidate/review pipeline is not part of this build. Bot-originated "
    "memory forms through reflection: lessons and preferences, evidence-gated."
)

# Ra consent labels that don't exist in the Yin lane map onto open use.
_CONSENT_MAP = {
    "inferred_low_risk": "ok",
    "explicit": "ok",
    "ok": "ok",
    "ask_before_use": "ask_before_use",
    "sensitive_pending": "sensitive_pending",
}


class YinStore:
    available = True

    def __init__(self, memory=None) -> None:
        self.logs = YinLogs()
        self.yin = YinMemory()
        self.habitat = Habitat()
        self.creations = Creations()
        self._bot_memory = memory  # Ra's JSONL BotMemory, for conversations
        self._posture_path = lane_path("posture.json")

    async def connect(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    # ── posture (runtime state only — not identity) ──────────────────

    def _posture(self) -> Dict[str, Any]:
        try:
            return json.loads(self._posture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    async def read_posture(self) -> Dict[str, Any]:
        return self._posture()

    async def update_posture(self, key: str, value: Any) -> str:
        state = self._posture()
        state[key] = value
        self._posture_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return "ok"

    # ── logs (SQLite — the actual step-4 swap) ────────────────────────

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
        meta = dict(metadata or {})
        if source_actor != "bot":
            meta["source_actor"] = source_actor
        if channel:
            meta["channel"] = channel
        if human_id:
            meta["human_id"] = human_id
        return str(self.logs.log_event(source_type, content, meta or None))

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
        return str(self.logs.log_tool_call(tool_name, phase, args, result_preview, success))

    async def recent_tool_calls(self, bot_id: str = "", limit: int = 8) -> str:
        """JSON shape prompt_builder renders."""
        with self.logs._connect() as conn:
            rows = conn.execute(
                "SELECT tool_name, phase, result_preview, success FROM tool_calls "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 50)),),
            ).fetchall()
        return json.dumps(
            [{
                "tool_name": row["tool_name"], "phase": row["phase"],
                "result_preview": row["result_preview"], "success": bool(row["success"]),
            } for row in rows],
            ensure_ascii=False,
        )

    async def recent_conversations(self, limit: int = 8) -> List[Dict[str, Any]]:
        if not self._bot_memory:
            return []
        turns: List[Dict[str, Any]] = []
        for row in self._bot_memory.read_recent("conversations", limit=limit):
            if row.get("user_text"):
                turns.append({"source_actor": row.get("author", "human"), "content": row["user_text"]})
            if row.get("assistant_text"):
                turns.append({"source_actor": "bot", "content": row["assistant_text"]})
        return turns[-limit * 2:]

    # ── human lane and notebook ───────────────────────────────────────

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
        ok, msg = self.yin.human.store(
            human_id,
            content,
            admission_category=admission_category,
            admission_reason=admission_reason,
            consent_status=_CONSENT_MAP.get(consent_status, ""),
        )
        return msg

    async def recent_memory_admissions(self, bot_id: str, human_id: str = "", limit: int = 10) -> str:
        if not human_id:
            return "[]"
        entries = load_entries(self.yin.human._user_path(human_id))[-max(1, limit):]
        return json.dumps(
            [{
                "content": e["text"],
                "admission_category": e.get("admission_category", ""),
                "admission_reason": e.get("admission_reason", ""),
                "consent_status": e.get("consent_status", "ok"),
                "created_at": e.get("created_at", ""),
            } for e in reversed(entries)],
            ensure_ascii=False,
        )

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
        text = f"{title}: {content}" if title else content
        ok, msg = self.yin.notebook.store(human_id, text, kind=entry_type, due_at=due_at or "")
        return msg

    async def due_notebook_items(self, bot_id: str, limit: int = 10) -> str:
        """JSON list across all humans — bot.py parses and DMs reminders."""
        now = utc_now()
        items: List[Dict[str, Any]] = []
        notebook_dir = lane_path("notebook", "placeholder").parent
        for path in sorted(notebook_dir.glob("*.json")):
            user_id = path.stem
            for entry in load_entries(path):
                if entry.get("due_at") and not entry.get("completed") and entry["due_at"] <= now:
                    items.append({
                        "id": entry["id"],
                        "human_id": user_id,
                        "entry_type": entry.get("kind", "note"),
                        "content": entry["text"],
                        "title": "",
                        "due_at": entry.get("due_at"),
                    })
        return json.dumps(items[: max(1, limit)], ensure_ascii=False)

    async def complete_notebook_item(self, bot_id: str, notebook_id: str, reason: str = "") -> str:
        notebook_dir = lane_path("notebook", "placeholder").parent
        for path in sorted(notebook_dir.glob("*.json")):
            ok, msg = self.yin.notebook.complete(path.stem, notebook_id)
            if ok:
                return msg
        return f"No open notebook item {notebook_id} for any human."

    # ── creations and habitat ─────────────────────────────────────────

    async def store_creation(
        self,
        mode: str,
        content: str,
        prompted_by: Optional[str] = None,
        tags: Optional[List[str]] = None,
        cycle: Optional[int] = None,
    ) -> str:
        ok, msg = self.creations.store(mode, content, prompted_by or "", tags, cycle)
        return msg

    async def recent_creations(self, limit: int = 5, mode_filter: Optional[str] = None) -> str:
        return self.creations.recent_json(limit, mode_filter)

    async def ensure_habitat_area(self, bot_id: str, area: str, state: Optional[Dict[str, Any]] = None) -> str:
        return self.habitat.ensure_area(area, state)

    async def habitat_snapshot(self, bot_id: str, area: str = "", event_limit: int = 8) -> str:
        return self.habitat.snapshot(area, event_limit)

    async def place_habitat_entry(self, **kwargs: Any) -> str:
        kwargs.pop("bot_id", None)
        return self.habitat.place_entry(**kwargs)

    async def update_habitat_state(self, **kwargs: Any) -> str:
        kwargs.pop("bot_id", None)
        return self.habitat.update_state(**kwargs)

    async def log_habitat_event(self, **kwargs: Any) -> str:
        kwargs.pop("bot_id", None)
        return self.habitat.log_event(**kwargs)

    async def log_habitat_residue_decision(self, **kwargs: Any) -> str:
        kwargs.pop("bot_id", None)
        kwargs.pop("tool_call_id", None)
        return self.habitat.log_residue_decision(**kwargs)

    async def recent_habitat_residue_decisions(self, bot_id: str, limit: int = 10) -> str:
        return self.habitat.recent_residue_decisions(limit)

    # ── layered memory (prompt context blocks) ────────────────────────

    async def recent_layered_memory(
        self,
        layer: str,
        bot_id: str,
        human_id: str = "",
        limit: int = 5,
        include_terminal: bool = False,
    ) -> str:
        if layer == "human_memory" and human_id:
            entries = load_entries(self.yin.human._user_path(human_id))[-limit:]
            return json.dumps(
                [{
                    "memory_type": e.get("admission_category", "fact"),
                    "consent_status": e.get("consent_status", "ok"),
                    "content": e["text"],
                } for e in reversed(entries)],
                ensure_ascii=False,
            )
        if layer == "human_notebook" and human_id:
            entries = [
                e for e in load_entries(self.yin.notebook._user_path(human_id))
                if not e.get("completed")
            ][-limit:]
            return json.dumps(
                [{
                    "entry_type": e.get("kind", "note"),
                    "status": "active",
                    "title": "",
                    "content": e["text"],
                    "due_at": e.get("due_at"),
                } for e in reversed(entries)],
                ensure_ascii=False,
            )
        # bot_self_memory: no candidate pipeline in this build — the block
        # simply doesn't render. Lessons/preferences arrive via recall.
        return "[]"

    # ── legacy working memory (memory_interpret family) ───────────────

    async def store_interpretation(
        self,
        type: str,
        content: str,
        confidence: float = 0.5,
        status: str = "provisional",
        tags: Optional[List[str]] = None,
        source_actor: str = "bot",
    ) -> str:
        ok, msg = self.yin.working.add(content, source=f"interpret:{type}")
        return msg

    async def recent_interpretations(self, limit: int = 10, **_: Any) -> str:
        return self.yin.working.read(limit)

    async def search_interpretations(self, query: str, limit: int = 10, **_: Any) -> str:
        return self.yin.working.read(limit)

    async def update_interpretation_status(self, id: str, new_status: str, note: str = "") -> str:
        return (
            "Interpretation statuses are not tracked in this build; working "
            "memory holds the content. Lessons/preferences carry what endures."
        )

    async def link_interpretations(self, from_id: str, to_id: str, link_type: str, note: str = "") -> str:
        return (
            "Interpretation links are not tracked in this build. The knowledge "
            "graph will hold relationships once the Neo4j store lands."
        )

    async def hold_interpretation(
        self,
        interpretation_id: str,
        held_reason: str = "",
        revisit_after: Optional[str] = None,
    ) -> str:
        question = held_reason or f"revisit working note {interpretation_id}"
        ok, msg = self.yin.vestibule.hold(question, context=interpretation_id)
        return msg

    async def check_vestibule(self, limit: int = 5) -> str:
        return self.yin.vestibule.check(limit)

    # ── deferrals ─────────────────────────────────────────────────────

    async def defer_response(
        self,
        incoming_text: str,
        author: str,
        channel: str,
        answer_after: Optional[str] = None,
    ) -> str:
        path = lane_path("deferred.json")
        entries = load_entries(path)
        entry = make_entry(incoming_text, author=author, channel=channel,
                           answer_after=answer_after, answered=False)
        entries.append(entry)
        save_entries(path, entries)
        return f"Deferred ({entry['id']})."

    async def pending_deferred(self, limit: int = 5) -> List[Dict[str, Any]]:
        entries = load_entries(lane_path("deferred.json"))
        pending = [e for e in entries if not e.get("answered")]
        return [
            {"id": e["id"], "incoming_text": e["text"], "author": e.get("author", ""),
             "channel": e.get("channel", ""), "created_at": e.get("created_at", "")}
            for e in pending[-max(1, limit):]
        ]

    async def mark_answered(self, id: str, answer_text: str) -> str:
        path = lane_path("deferred.json")
        entries = load_entries(path)
        for entry in entries:
            if entry["id"] == id:
                entry["answered"] = True
                entry["answer_text"] = answer_text
                entry["updated_at"] = utc_now()
                save_entries(path, entries)
                return "answered"
        return f"no deferred item {id}"

    # ── initiations (DM reminders etc.) ───────────────────────────────

    async def log_initiation_attempt(self, **fields: Any) -> str:
        path = lane_path("initiations.json")
        entries = load_entries(path)
        entry = make_entry(fields.get("reason", ""), **{
            key: value for key, value in fields.items()
            if key not in {"reason", "metadata"}
        })
        entries.append(entry)
        save_entries(path, entries[-100:])
        return entry["id"]

    async def recent_initiation_attempts(self, bot_id: str, human_id: str = "", limit: int = 10) -> str:
        entries = load_entries(lane_path("initiations.json"))
        if human_id:
            entries = [e for e in entries if e.get("human_id") == human_id]
        recent = entries[-max(1, limit):]
        return json.dumps(
            [{
                "created_at": e.get("created_at", ""),
                "human_id": e.get("human_id", ""),
                "initiation_type": e.get("initiation_type", ""),
                "status": e.get("status", ""),
                "reason": e.get("text", ""),
            } for e in reversed(recent)],
            ensure_ascii=False,
        )

    # ── machinery the v2 design dropped: honest, never a dead end ─────

    async def store_bot_self_memory_candidate(self, **_: Any) -> str:
        return NO_PIPELINE

    async def review_candidates(self, bot_id: str, limit: int = 8, include_identity: bool = True) -> str:
        return NO_PIPELINE

    async def decide_memory_review(self, **_: Any) -> str:
        return NO_PIPELINE

    async def identity_threads(self, limit: int = 10) -> str:
        return "[]"

    async def list_memory_contexts(self, bot_id: str, include_archived: bool = False) -> str:
        return (
            "Memory contexts are not part of this build. Lanes: human, notebook, "
            "lessons, goals, preferences, autobiography, timeline, working, vestibule."
        )

    async def ensure_memory_context(self, *args: Any, **kwargs: Any) -> str:
        return "ok"

    async def resolve_memory_id(self, target_table: str, bot_id: str, id_prefix: str) -> str:
        lanes = {
            "human_memory": lane_path("human", "placeholder").parent,
            "human_notebook": lane_path("notebook", "placeholder").parent,
        }
        directory = lanes.get(target_table)
        if directory is None:
            return ""
        for path in sorted(directory.glob("*.json")):
            for entry in load_entries(path):
                if entry["id"].startswith(id_prefix):
                    return entry["id"]
        return ""

    async def record_curator_action(self, **fields: Any) -> str:
        path = lane_path("curator.json")
        entries = load_entries(path)
        entries.append(make_entry(str(fields.get("reason", "")), **{
            key: value for key, value in fields.items() if key != "reason"
        }))
        save_entries(path, entries[-100:])
        return "curator action recorded"

    async def update_curated_memory_status(self, **fields: Any) -> str:
        await self.record_curator_action(**fields)
        return (
            "Recorded. Memory status edits happen by hand in this build — the "
            "lanes are plain JSON under yin/memory/data/, prunable directly."
        )

    async def recent_curator_actions(self, bot_id: str, limit: int = 10) -> str:
        entries = load_entries(lane_path("curator.json"))[-max(1, limit):]
        return json.dumps(
            [{"action": e.get("action", ""), "target_table": e.get("target_table", ""),
              "reason": e.get("text", ""), "created_at": e.get("created_at", "")}
             for e in reversed(entries)],
            ensure_ascii=False,
        )

    async def log_interaction_trace(self, **fields: Any) -> str:
        return await self.log_event("system", "interaction_trace", metadata=_jsonable(fields))

    async def log_influence_event(self, **fields: Any) -> str:
        return await self.log_event("system", "influence_event", metadata=_jsonable(fields))

    async def log_role_invitation(self, **fields: Any) -> str:
        return await self.log_event("system", "role_invitation", metadata=_jsonable(fields))

    async def recent_interaction_traces(self, limit: int = 3) -> str:
        return "[]"

    async def trace_aggregate(self, bot_id: str = "", days: int = 7) -> str:
        return "Trace aggregation is not part of this build. !status and the events log cover activity."

    async def trace_compare(self, days: int = 7) -> str:
        return "Trace comparison is not part of this build."


def _jsonable(fields: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in fields.items():
        try:
            json.dumps(value)
            out[key] = value
        except (TypeError, ValueError):
            out[key] = str(value)
    return out
