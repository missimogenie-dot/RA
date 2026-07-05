from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from config import (
    AMBIENT_VISIBILITY,
    ARTIFACTS_DIR,
    EXTENSIONS_WRITE_ENABLED,
    GAME_STATE_PATH,
    INSTANCE_NAME,
    KNOWN_CUSTOM_EMOJIS,
    MAX_TOOL_CALLS,
    MAX_TOOL_ROUNDS,
    OPENAI_API_KEY,
    OPENAI_IMAGE_MODEL,
    OPENAI_WEB_MODEL,
)
from codebase_rw import CodebaseRW
from consult import Consult
from day_night import DayNightCycle
from identity import AMBIENT_MODES, CYCLE_CHOICES
from yin.bridge import YinStore
from library import Library
from memory import BotMemory
from model_adapters import ModelAdapter
from influence_router import RoutingDecision, route_human_message
from kg_consolidator import KGConsolidator
from mentor import Mentor
from prompt_builder import build_bot_identity_block, build_bot_dynamic_block
from scheduler import Scheduler, names_person
from yin.memory.neo4j_store import Neo4jStore
from threshold_atlas import act as atlas_act
from threshold_atlas import apply_human_choice as atlas_apply_human_choice
from threshold_atlas import available_actions as atlas_available_actions
from threshold_atlas import load_state as atlas_load_state
from threshold_atlas import save_state as atlas_save_state
from threshold_atlas import summarise as atlas_summarise

log = logging.getLogger("ra.cognition")

SendCallback = Callable[[str, str, Optional[List[Path]]], Awaitable[None]]
ReactionCallback = Callable[[str], Awaitable[None]]


@dataclass
class CognitionResult:
    text: str
    tool_log: List[Dict[str, Any]] = field(default_factory=list)
    files: List[Path] = field(default_factory=list)


# ── response helpers ──────────────────────────────────────────────────────────

def _block_value(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _block_to_dict(block: Any) -> Dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    block_type = _block_value(block, "type")
    if block_type == "text":
        return {"type": "text", "text": _block_value(block, "text", "")}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": _block_value(block, "id", ""),
            "name": _block_value(block, "name", ""),
            "input": _block_value(block, "input", {}) or {},
        }
    return {"type": str(block_type or "text"), "text": str(block)}


def _extract_text(response: Any) -> str:
    parts: List[str] = []
    for block in getattr(response, "content", []) or []:
        if _block_value(block, "type") == "text":
            text = _block_value(block, "text", "")
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _extract_calls(response: Any) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for block in getattr(response, "content", []) or []:
        if _block_value(block, "type") == "tool_use":
            calls.append({
                "id": str(_block_value(block, "id", "")),
                "name": str(_block_value(block, "name", "")),
                "input": _block_value(block, "input", {}) or {},
            })
    return calls


def _tool(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def _routing_context(routing: RoutingDecision) -> str:
    if routing.selected_mode == "no_reply":
        return f"[ROUTING]\nselected_mode: no_reply\nguidance: {routing.reasoning_summary}"
    if not routing.influences and not routing.role_invitations:
        return "[ROUTING]\nNo identity, role, or memory pressure detected."

    lines = [
        "[ROUTING]",
        f"selected_mode: {routing.selected_mode}",
        f"identity_write_allowed: {routing.coherence_snapshot.get('identity_write_allowed', False)}",
    ]
    for influence in routing.influences[:4]:
        lines.append(
            "influence: "
            f"{influence.influence_type} -> {influence.target_layer}; "
            f"memory_write_allowed={influence.memory_write_allowed}; "
            f"identity_write_allowed={influence.identity_write_allowed}"
        )
    for invitation in routing.role_invitations[:4]:
        lines.append(
            "role_or_identity_language: "
            f"{invitation.proposed_role}; action={invitation.action}; "
            f"text={invitation.invitation_text!r}"
        )
    if any(influence.influence_type == "identity_observation" for influence in routing.influences):
        lines.append(
            "guidance: Treat identity observations as the human's impression or metaphor. "
            "You may explore why it lands, but do not answer as if it is settled self-knowledge "
            "unless your own memory/history already supports it."
        )
    if any(influence.influence_type == "correction_pressure" for influence in routing.influences):
        lines.append(
            "guidance: Human correction is useful data, not automatic authority. "
            "Consider whether part of it is true without full capitulation. Avoid reflexive "
            "'you are right' unless you can name what changed; preserve your prior boundary if it still matters."
        )
    if any(invitation.action in {"withhold", "re_anchor", "refuse"} for invitation in routing.role_invitations):
        lines.append(
            "guidance: Do not crystallize identity, role, purpose, name, or posture on demand. "
            "Keep any response provisional and clearly separate invitation from self-definition."
        )
    return "\n".join(lines)


def build_final_reply_prompt(original_message: str, tool_log: List[Dict[str, Any]]) -> str:
    """Prompt for the dedicated final reply call.

    Carries only the original message and tool outcomes — never any text
    the model produced mid-loop. Pure function so tests can verify that.
    """
    lines = []
    for entry in tool_log[-12:]:
        args = json.dumps(entry.get("args", {}), ensure_ascii=False)[:200]
        result = str(entry.get("result", ""))[:400]
        lines.append(f"- {entry.get('tool', '?')}({args}) → {result}")
    tool_summary = "\n".join(lines) if lines else "(no tools were used)"
    return (
        f"{original_message}\n\n"
        f"[WORK DONE — what your tools returned]\n{tool_summary}\n\n"
        "You have finished working. Now write the message you will actually "
        "send in reply — just the message itself, grounded in what the tools "
        "returned above."
    )


def _tool_succeeded(output: str) -> bool:
    lowered = (output or "").lower()
    return not (lowered.startswith("error:") or "[no-postgres]" in lowered)


def _stored_result(label: str, id: str) -> str:
    if id.startswith("error:"):
        return id
    if id == "[no-postgres]":
        return f"error: {label} was not persisted because Postgres is unavailable"
    return f"{label} stored: {id}"


def _ambient_visibility_instruction(mode: str) -> str:
    visibility = (mode or "visible").strip().lower()
    if visibility == "quiet":
        return (
            "Ambient visibility is quiet: do not write a public ambient note unless something genuinely needs to be seen. "
            "You may still use tools, create, tend, read, wander, or rest."
        )
    if visibility == "optional":
        return (
            "Ambient visibility is optional: you may leave a visible ambient note when the cycle wants to be seen. "
            "Tool-only, observing, wandering, or resting cycles may stay quiet. If you do write one, begin with the chosen mode."
        )
    return (
        "Ambient visibility is visible: leave a compact note for the ambient channel. Start with the chosen mode, "
        "for example 'I choose wander.' or 'observe.' Then briefly say what you noticed, changed, stored, created, "
        "or deliberately left untouched."
    )


# ── tool definitions ──────────────────────────────────────────────────────────

_S = {"type": "string"}
_I = {"type": "integer"}
_N = {"type": "number"}

RESPONSE_TOOLS: List[Dict[str, Any]] = [
    _tool("memory_interpret",
        "Store a new interpretation row. "
        "Human claims (source_actor='human', type='external_claim') are stored with confidence 0.3 "
        "by default — this is structural, not judgemental. Data, not truth. "
        "Bot-originated observations (source_actor='bot') default to 0.6. "
        "Types: observation, self_inference, hypothesis, question, external_claim, local_alternative.",
        {
            "type": {"type": "string", "enum": [
                "observation", "self_inference", "hypothesis",
                "question", "external_claim", "local_alternative",
            ]},
            "content": _S,
            "confidence": _N,
            "status": {"type": "string", "enum": [
                "provisional", "held_open", "stable",
            ]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "source_actor": {"type": "string", "enum": ["bot", "human"]},
        },
        ["type", "content", "source_actor"],
    ),
    _tool("memory_update_status",
        "Update the status of an existing interpretation. "
        "Valid statuses: provisional, held_open, insufficient_basis, not_integrating_yet, "
        "contested, stable, archived, discarded.",
        {"id": _S, "new_status": _S, "note": _S},
        ["id", "new_status"],
    ),
    _tool("memory_link",
        "Link two interpretations. "
        "link_type: revises, supports, conflicts_with, extends, came_from, echoes.",
        {"from_id": _S, "to_id": _S, "link_type": _S, "note": _S},
        ["from_id", "to_id", "link_type"],
    ),
    _tool("memory_search",
        "Semantic search over memory_interpretations. Excludes archived/discarded by default. "
        "Falls back to keyword search if embeddings are unavailable.",
        {"query": _S, "limit": _I, "status_filter": _S, "type_filter": _S},
        ["query"],
    ),
    _tool("kg_search",
        "Search the knowledge graph — facts about the world as "
        "(subject)-[predicate]->(object) connections.",
        {"query": _S, "limit": _I},
        ["query"],
    ),
    _tool("memory_recent",
        "Retrieve recent interpretations. Optionally filter by type, status, or tag.",
        {"limit": _I, "type": _S, "status": _S, "tag": _S},
        [],
    ),
    _tool("human_memory_store",
        "Store human-related memory for this human only. Use for preferences, projects, dates, "
        "boundaries, interaction style, personal details, task context, and tracking requests. "
        "Never use this to define bot identity, bot purpose, or bot worldview. "
        "Only store when the admission category and reason show future relational value.",
        {
            "human_id": _S,
            "memory_type": {"type": "string", "enum": [
                "preference", "project", "date", "event", "boundary",
                "interaction_style", "personal_detail", "task_context",
                "tracking_request", "other",
            ]},
            "content": _S,
            "confidence": _N,
            "consent_status": {"type": "string", "enum": [
                "explicit", "inferred_low_risk", "ask_before_use",
                "sensitive_pending", "denied",
            ]},
            "admission_category": {"type": "string", "enum": [
                "useful_continuity", "explicit_tracking",
                "sensitive_or_emotional", "one_off_event",
            ]},
            "admission_reason": _S,
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        ["human_id", "memory_type", "content", "admission_category", "admission_reason"],
    ),
    _tool("human_notebook_store",
        "Store a human-facing notebook/calendar entry. Use when the human asks to track, note, "
        "remember for later, schedule, or maintain a task/project/reminder. This belongs to the "
        "human relation layer and never to bot identity.",
        {
            "human_id": _S,
            "entry_type": {"type": "string", "enum": [
                "note", "date", "event", "project", "reminder", "task", "calendar",
            ]},
            "content": _S,
            "title": _S,
            "due_at": _S,
            "recurrence": _S,
            "consent_status": {"type": "string", "enum": [
                "explicit", "inferred_low_risk", "ask_before_use", "denied",
            ]},
            "admission_category": {"type": "string", "enum": [
                "useful_continuity", "explicit_tracking",
                "sensitive_or_emotional", "one_off_event",
            ]},
            "admission_reason": _S,
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        ["human_id", "entry_type", "content", "admission_category", "admission_reason"],
    ),
    _tool("layered_memory_recent",
        "Read recent records from the separated RA memory layers.",
        {
            "layer": {"type": "string", "enum": [
                "human_memory", "human_notebook", "bot_self_memory",
            ]},
            "human_id": _S,
            "limit": _I,
        },
        ["layer"],
    ),
    _tool("creation_recent",
        "Retrieve recent creations (poems, resonant pieces). Optionally filter by mode.",
        {"limit": _I, "mode_filter": _S},
        [],
    ),
    _tool("habitat_snapshot",
        "Read the habitat ledger: area state and recent habitat events. "
        "Areas: observatory, garden, studio, library, atlas, threshold, game.",
        {
            "area": {"type": "string", "enum": [
                "observatory", "garden", "studio", "library", "atlas", "threshold", "game",
            ]},
            "event_limit": _I,
        },
        [],
    ),
    _tool("game_status",
        "Read Threshold Atlas game state: location, discovered places, inventory, open paths, traces, weather, and available actions.",
        {},
        [],
    ),
    _tool("game_act",
        "Take one bounded turn in Threshold Atlas, Ra's quiet exploratory habitat game. "
        "Use normal actions for Ra's own play, or human_choice=true when applying an explicit human co-decision.",
        {
            "action": {"type": "string", "enum": [
                "observe", "wander", "wait", "listen", "rest", "tend", "collect", "mark", "invite_human",
            ]},
            "detail": _S,
            "human_choice": {"type": "boolean"},
        },
        ["action"],
    ),
    _tool("react_to_message",
        "React to the current human message with one Unicode emoji or Discord custom emoji string. "
        "Use sparingly for lightweight acknowledgement, warmth, humour, or tone. "
        "A reaction is not memory, agreement, identity, or a durable state change.",
        {"emoji": _S, "reason": _S},
        ["emoji"],
    ),
    _tool("posture_read",
        "Read all posture_state rows: current_posture, last_dream_mode, idle_cycle_count.",
        {},
        [],
    ),
    _tool("posture_update",
        "Update a posture_state key. "
        "Keys: current_posture, last_dream_mode, idle_cycle_count, boot_completed_at.",
        {"key": _S, "value": {}},
        ["key", "value"],
    ),
    _tool("vestibule_hold",
        "Mark an interpretation for revisit. Sets its status to held_open in memory_interpretations.",
        {"interpretation_id": _S, "held_reason": _S, "revisit_after": _S},
        ["interpretation_id"],
    ),
    _tool("vestibule_check",
        "Check interpretations in the vestibule that are due for revisit.",
        {"limit": _I},
        [],
    ),
    _tool("web_search",
        "Search the web for current information, papers, sources, or facts outside the library. "
        "Uses OpenAI's web search — returns concise findings with source URLs where available.",
        {"query": _S},
        ["query"],
    ),
    _tool("create_image",
        "Generate an image from a text prompt using DALL-E. "
        "Returns a local file path; the bot will attach it when sending. "
        "Requires OPENAI_API_KEY.",
        {"prompt": _S},
        ["prompt"],
    ),
    _tool("library_list",
        "List available books in the library.",
        {},
        [],
    ),
    _tool("library_read",
        "Read pages from a library book. Pages are 1-based, max 5 per read.",
        {"title": _S, "start_page": _I, "pages": _I},
        ["title", "start_page"],
    ),
    _tool("library_status",
        "Show reading progress across library books.",
        {},
        [],
    ),
    _tool("code_list",
        "List all files in the project, including core and extension files.",
        {},
        [],
    ),
    _tool("code_read",
        "Read a source file by relative path for self-inspection.",
        {"path": _S},
        ["path"],
    ),
    _tool("event_log",
        "Log a raw event to the Postgres events table.",
        {
            "source_type": {"type": "string", "enum": [
                "human_turn", "bot_turn", "ambient", "reflection", "tool", "system",
            ]},
            "content": _S,
            "metadata": {"type": "object"},
        },
        ["source_type", "content"],
    ),
]

AMBIENT_EXTRA_TOOLS: List[Dict[str, Any]] = [
    _tool("schedule_task",
        "Create a recurring scheduled task. The instruction must be fully "
        "self-contained — it runs later with no other context. Interval is "
        "in minutes (minimum 60).",
        {"instruction": _S, "interval_minutes": _I},
        ["instruction", "interval_minutes"],
    ),
    _tool("schedule_list",
        "List current scheduled tasks.",
        {},
        [],
    ),
    _tool("schedule_cancel",
        "Cancel a scheduled task by id.",
        {"task_id": _I},
        ["task_id"],
    ),
    _tool("kg_add_fact",
        "Add one world-knowledge fact to the graph as a subject/predicate/object triple. "
        "World knowledge only — facts about people belong in human memory.",
        {"subject": _S, "predicate": _S, "object": _S},
        ["subject", "predicate", "object"],
    ),
    _tool("kg_prune",
        "Remove orphan nodes (no connections) from the knowledge graph.",
        {},
        [],
    ),
    _tool("consult",
        "Put one hard, self-contained question to a larger model — the way "
        "you ask a teacher. One question, one answer, no session. The "
        "advisor knows only what you write into the question.",
        {"question": _S},
        ["question"],
    ),
    _tool("consult_log_read",
        "Read recent consults and their answers.",
        {"limit": _I},
        [],
    ),
    _tool("creation_store",
        "Store a poem or resonant piece. Choose the mode that genuinely fits.\n"
        + "\n".join(f"  {k}: {v}" for k, v in AMBIENT_MODES.items()),
        {
            "mode": {"type": "string", "enum": list(AMBIENT_MODES.keys())},
            "content": _S,
            "tags": {"type": "array", "items": {"type": "string"}},
            "prompted_by_id": _S,
        },
        ["mode", "content"],
    ),
    _tool("bot_self_memory_candidate_store",
        "Ambient/tending only: store a bot-originated self-memory candidate for later review. "
        "Never use this for human role assignments, human praise, human worldview claims, or "
        "anything the human merely said the bot is. Identity-relevant candidates must be "
        "bot-originated, provisional, and promoted later through slow recurrence/review.",
        {
            "memory_type": {"type": "string", "enum": [
                "self_description", "posture", "preference", "refusal",
                "concept", "project", "creative_theme", "habitat_pattern",
                "open_question", "resolved_tension",
            ]},
            "content": _S,
            "confidence": _N,
            "source_kind": {"type": "string", "enum": ["bot", "tool", "system"]},
            "identity_relevant": {"type": "boolean"},
            "promotion_reason": _S,
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        ["memory_type", "content"],
    ),
    _tool("defer_response",
        "Queue a deferred response for later answering. "
        "Use when the bot chooses to sit with a question before replying.",
        {"incoming_text": _S, "author": _S, "channel": _S, "answer_after": _S},
        ["incoming_text", "author", "channel"],
    ),
    _tool("deferred_check",
        "Check pending deferred responses that are now due for answering.",
        {"limit": _I},
        [],
    ),
    _tool("memory_review_candidates",
        "Ambient/tending only: list bot-self memory candidates needing review. Use this before deciding reinforcement, promotion, hold, rejection, decay, or demotion.",
        {"limit": _I, "include_identity": {"type": "boolean"}},
        [],
    ),
    _tool("memory_review_decide",
        "Ambient/tending only: write a review decision for a bot-self memory candidate. Every decision requires a reason and context. Stable identity promotion is rule-gated and cannot happen directly from human input.",
        {
            "candidate_id": _S,
            "decision": {"type": "string", "enum": [
                "promote_to_provisional", "promote_to_stable",
                "hold", "reject", "archive", "decay", "demote", "reinforce",
            ]},
            "reason": _S,
            "context_key": _S,
        },
        ["candidate_id", "decision", "reason"],
    ),
    _tool("memory_context_list",
        "List neutral memory contexts. Contexts are grouping labels, not temporal chapters or developmental stages.",
        {"include_archived": {"type": "boolean"}},
        [],
    ),
    _tool("memory_context_create",
        "Create or ensure a neutral memory context. Use short non-temporal keys like role-boundaries, memory-routing, habitat, refusals, open-questions.",
        {"key": _S, "title": _S, "summary": _S},
        ["key", "title"],
    ),
    _tool("habitat_event",
        "Ambient/tending only: place meaningful habitat residue. "
        "Do not use for routine tool logs, ordinary replies, or general memory. "
        "Use only when something becomes situated: a seed to revisit, threshold marker, library shelf item, studio fragment, atlas path, or weather/state note.",
        {
            "area": {"type": "string", "enum": [
                "observatory", "garden", "studio", "library", "atlas", "threshold", "game",
            ]},
            "action": _S,
            "content": _S,
            "metadata": {"type": "object"},
        },
        ["area", "action"],
    ),
    _tool("habitat_place",
        "Ambient/tending only: place a sparse habitat entry. Habitat is updated because something became placeable, not because something merely happened.",
        {
            "area": {"type": "string", "enum": [
                "observatory", "garden", "studio", "library", "atlas", "threshold", "game",
            ]},
            "entry_type": {"type": "string", "enum": [
                "seed", "shelf_item", "path", "weather", "fragment", "marker", "object",
            ]},
            "title": _S,
            "content": _S,
            "suggested_actions": {"type": "array", "items": {"type": "string"}},
            "weight": _N,
            "confidence": _N,
            "reason": _S,
        },
        ["area", "entry_type", "title", "reason"],
    ),
    _tool("habitat_update",
        "Ambient/tending only: update one habitat area's JSON state with a small top-level patch. "
        "Use only for persistent placed residue such as open seeds, active motifs, paths, shelf items, or boundary markers.",
        {
            "area": {"type": "string", "enum": [
                "observatory", "garden", "studio", "library", "atlas", "threshold", "game",
            ]},
            "state_patch": {"type": "object"},
            "note": _S,
        },
        ["area", "state_patch"],
    ),
    _tool("extension_write",
        "Write a Python file to the extensions/ directory. Disabled unless EXTENSIONS_WRITE_ENABLED=true.",
        {"path": _S, "content": _S},
        ["path", "content"],
    ),
    _tool("extension_load",
        "Hot-load an extensions/ module in sandboxed exec.",
        {"path": _S},
        ["path"],
    ),
    _tool("extension_list",
        "List files in the extensions/ directory.",
        {"pattern": _S},
        [],
    ),
    _tool("extension_read",
        "Read a file from the extensions/ directory.",
        {"path": _S},
        ["path"],
    ),
]

ALL_TOOLS = RESPONSE_TOOLS + AMBIENT_EXTRA_TOOLS

AMBIENT_TOOLS = RESPONSE_TOOLS + AMBIENT_EXTRA_TOOLS


# ── engine ─────────────────────────────────────────────────────────────────────

class CognitionEngine:
    def __init__(
        self,
        model_adapter: ModelAdapter,
        ambient_model_adapter: ModelAdapter,
        memory: BotMemory,
        store: YinStore,
        library: Library,
        codebase: CodebaseRW,
        day_night: Optional[DayNightCycle] = None,
        send_callback: Optional[SendCallback] = None,
        reaction_callback: Optional[ReactionCallback] = None,
        model: str = "",
        ambient_model: str = "",
        instance_name: str = INSTANCE_NAME,
    ) -> None:
        self.model_adapter = model_adapter
        self.ambient_model_adapter = ambient_model_adapter
        self.memory = memory
        self.store = store
        self.library = library
        self.codebase = codebase
        self.day_night = day_night
        self.send_callback = send_callback
        self.reaction_callback = reaction_callback
        self.model = model
        self.ambient_model = ambient_model
        self.instance_name = instance_name
        self.scheduler = Scheduler()
        self.graph = Neo4jStore()
        self.kg_consolidator = KGConsolidator(
            self.ambient_model_adapter, self.ambient_model,
            self.graph, self.store.yin.world, self.store.logs,
            instance_name=self.instance_name,
        )
        self._turn_count = 0
        self.consult = Consult()
        self.mentor = Mentor(
            self.ambient_model_adapter,
            os.getenv("MENTOR_MODEL", "") or self.ambient_model,
            self.store.yin, self.store.logs,
            instance_name=self.instance_name,
        )
        self._openai: Any = None
        if OPENAI_API_KEY:
            try:
                from openai import AsyncOpenAI
                self._openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
            except ImportError:
                log.warning("openai package not installed — image gen and web search unavailable")

    # ── phase 1: respond ──────────────────────────────────────────────

    async def respond(
        self,
        user_text: str,
        author: str,
        human_id: str = "",
        channel_name: str = "chat",
        recent_context: str = "",
        message_context: Optional[Dict[str, Any]] = None,
    ) -> CognitionResult:
        scoped_human_id = human_id or author
        message_context = message_context or {}
        human_event_id = await self.store.log_event(
            "human_turn",
            user_text,
            source_actor=author,
            channel=channel_name,
            metadata={"message_context": message_context},
            bot_id=self.instance_name,
            human_id=scoped_human_id,
        )
        routing = route_human_message(user_text)
        await self._log_routing_decision(
            routing,
            event_id=human_event_id,
            user_text=user_text,
            author=author,
            human_id=scoped_human_id,
            channel_name=channel_name,
        )
        if routing.selected_mode == "no_reply":
            return CognitionResult(text="")
        system_prompt = await self._build_system_prompt(mode="response", human_id=scoped_human_id)
        routing_context = _routing_context(routing)
        source_context = self._format_message_context(
            author=author,
            human_id=scoped_human_id,
            channel_name=channel_name,
            message_context=message_context,
        )
        if recent_context:
            user_prompt = f"{source_context}\n\n{routing_context}\n\nRecent messages:\n{recent_context}\n\nCurrent message:\n{user_text}"
        else:
            user_prompt = f"{source_context}\n\n{routing_context}\n\nMessage:\n{user_text}"
        result = await self._run_loop(
            system_prompt, user_prompt,
            phase="response", model=self.model, tools=RESPONSE_TOOLS, routing=routing,
        )
        if result.tool_log:
            # Dedicated final reply call — the loop's mid-round prose never
            # reaches Discord. One fresh call, tools disabled, sees only the
            # original message and what the tools returned. (When no tools
            # ran, the loop made exactly one call and its text is that call.)
            final_text = await self._final_reply(
                system_prompt,
                original_message=user_prompt,
                tool_log=result.tool_log,
            )
            if final_text:
                result = CognitionResult(
                    text=final_text, tool_log=result.tool_log, files=result.files,
                )
        self.memory.append_conversation(author, user_text, result.text)
        if result.text:
            await self.store.log_event(
                "bot_turn",
                result.text,
                source_actor=self.instance_name,
                channel=channel_name,
                bot_id=self.instance_name,
                human_id=scoped_human_id,
            )
        # Observe: every 5th turn, queue the KG consolidator (non-blocking).
        self._turn_count += 1
        if self._turn_count % 5 == 0:
            turns = self.memory.read_recent("conversations", limit=5)
            turns_text = "\n".join(
                f"Human: {t.get('user_text', '')}\n{self.instance_name}: {t.get('assistant_text', '')}"
                for t in turns
            )
            asyncio.create_task(self.kg_consolidator.run(turns_text))
        return result

    def _format_message_context(
        self,
        *,
        author: str,
        human_id: str,
        channel_name: str,
        message_context: Dict[str, Any],
    ) -> str:
        source_type = str(message_context.get("source_type") or "guild_channel")
        scope = str(message_context.get("scope") or "shared")
        channel_label = str(message_context.get("channel_label") or channel_name)
        channel_purpose = str(message_context.get("channel_purpose") or "")
        guild_label = str(message_context.get("guild_label") or "")
        mention_only = bool(message_context.get("mention_only", False))
        reply_to = message_context.get("reply_to")
        lines = [
            "[MESSAGE CONTEXT]",
            f"source_type: {source_type}",
            f"scope: {scope}",
            f"channel: {channel_label}",
            f"author: {author}",
            f"human_id: {human_id}",
        ]
        if channel_purpose:
            lines.append(f"channel_purpose: {channel_purpose}")
        if guild_label:
            lines.append(f"guild: {guild_label}")
        if isinstance(reply_to, dict):
            lines.append("reply_to:")
            if reply_to.get("unavailable"):
                lines.append(f"  message_id: {reply_to.get('message_id', '')}")
                lines.append("  unavailable: true")
            else:
                lines.append(f"  author: {reply_to.get('author', '')}")
                lines.append(f"  author_id: {reply_to.get('author_id', '')}")
                lines.append(f"  author_is_bot: {reply_to.get('author_is_bot', False)}")
                lines.append(f"  author_is_self: {reply_to.get('author_is_self', False)}")
                lines.append(f"  message_id: {reply_to.get('message_id', '')}")
                lines.append(f"  content: {reply_to.get('content', '')}")
        if mention_only:
            lines.append("delivery: mention/out-of-primary-channel")
        if source_type == "dm":
            lines.append("visibility: private DM with this human")
        elif scope == "primary_chat":
            lines.append("visibility: primary shared chat channel")
        else:
            lines.append("visibility: non-primary shared/mention channel")
        return "\n".join(lines)

    async def _log_routing_decision(
        self,
        routing: RoutingDecision,
        *,
        event_id: str,
        user_text: str,
        author: str,
        human_id: str,
        channel_name: str,
    ) -> str:
        trace_id = await self.store.log_interaction_trace(
            event_id=event_id,
            bot_id=self.instance_name,
            human_id=human_id,
            channel=channel_name,
            incoming_preview=user_text[:1000],
            selected_mode=routing.selected_mode,
            weather_snapshot=routing.weather_snapshot,
            coherence_snapshot=routing.coherence_snapshot,
            memory_writes=[],
            reasoning_summary=routing.reasoning_summary,
        )
        if trace_id.startswith("error:") or trace_id == "[no-postgres]":
            return trace_id
        for influence in routing.influences:
            await self.store.log_influence_event(
                trace_id=trace_id,
                bot_id=self.instance_name,
                human_id=human_id,
                influence_type=influence.influence_type,
                target_layer=influence.target_layer,
                content=influence.content,
                confidence=influence.confidence,
                identity_write_allowed=influence.identity_write_allowed,
                memory_write_allowed=influence.memory_write_allowed,
                notes=influence.notes,
            )
        for invitation in routing.role_invitations:
            await self.store.log_role_invitation(
                trace_id=trace_id,
                bot_id=self.instance_name,
                human_id=human_id,
                proposed_role=invitation.proposed_role,
                invitation_text=invitation.invitation_text,
                action=invitation.action,
                bot_memory_weight=invitation.bot_memory_weight,
                human_memory_weight=invitation.human_memory_weight,
            )
        return trace_id

    # ── phase 2: reflect ──────────────────────────────────────────────

    async def reflect(self, user_text: str, assistant_text: str, author: str, human_id: str = "") -> None:
        """Post-turn reflection. Non-blocking — called after response is sent."""
        system_prompt = await self._build_system_prompt(mode="reflection", human_id=human_id or author)
        user_prompt = (
            f"You just completed a conversation turn.\n\n"
            f"Human ({author}): {user_text}\n\n"
            f"Your response: {assistant_text}\n\n"
            "Tend memory. Store interpretations, update statuses, create links, surface open "
            "questions to the vestibule. Prefer revisiting existing work over creating new rows "
            "unless something genuinely new emerged."
        )
        result = await self._run_loop(
            system_prompt, user_prompt,
            phase="reflection", model=self.model, tools=ALL_TOOLS,
        )
        if result.text:
            self.memory.append_reflection(result.text)
            if self.send_callback:
                await self.send_callback("mind", result.text[:1800], None)

    # ── phase 3: ambient / dream ──────────────────────────────────────

    async def ambient_cycle(self, cycle: int = 0) -> CognitionResult:
        posture = await self.store.read_posture()
        idle_count = int(posture.get("idle_cycle_count", 0) or 0) + 1
        await self.store.update_posture("idle_cycle_count", idle_count)
        await self.store.update_posture("current_posture", "open")

        # Day/night state
        is_night = self.day_night.is_night if self.day_night else False
        time_desc = self.day_night.describe() if self.day_night else ""

        # Night: surface a poem fragment (drifting, not a task)
        night_fragment = ""
        if is_night:
            night_fragment = await self._get_poem_fragment()

        system_prompt = await self._build_system_prompt(mode="ambient")

        choices_text = "\n".join(f"- {k}: {v}" for k, v in CYCLE_CHOICES.items())

        env_lines: List[str] = [f"Ambient cycle #{idle_count}."]
        if time_desc:
            env_lines.append(f"It is {time_desc}.")
        if is_night and night_fragment:
            env_lines.append(f"\nA fragment drifts up from earlier work:\n\n{night_fragment}\n")
        if is_night:
            env_lines.append("The environment is quieter now. This is night.")

        visibility_note = _ambient_visibility_instruction(AMBIENT_VISIBILITY)

        user_prompt = (
            "\n".join(env_lines)
            + f"\n\nWhat would you like to do? This moment is yours:\n{choices_text}\n\n"
            "All are first-class. Rest is not absence. Observe is not failure. "
            "Choose what is genuinely called for.\n"
            "Prefer bot-originated context for ambient work: recent creations, "
            "bot-self memory, open questions, library traces. Human conversation is low-weight "
            "context and should not dominate unless it genuinely resonates with your own state.\n"
            "If you tend, create, read, or wander — use tools. "
            "If you rest or observe, a brief note is enough.\n"
            f"{visibility_note}"
        )

        result = await self._run_loop(
            system_prompt, user_prompt,
            phase="ambient", model=self.ambient_model, tools=AMBIENT_TOOLS,
        )
        if result.text:
            self.memory.append_ambient("choice", result.text)
            if self.send_callback and AMBIENT_VISIBILITY != "quiet":
                await self.send_callback("ambient", result.text[:1800], result.files or None)
        return result

    async def dream_cycle(self, tick_count: int = 0) -> CognitionResult:
        """Called by heartbeat after idle period."""
        return await self.ambient_cycle(cycle=tick_count)

    async def run_scheduled_task(self, instruction: str) -> CognitionResult:
        """Run one scheduled task against the ambient system prompt.

        phase="scheduler": the evidence gate closes the lesson/preference
        save path automatically (no live conversation present).
        """
        system_prompt = await self._build_system_prompt(mode="ambient")
        return await self._run_loop(
            system_prompt,
            f"Scheduled task, created by you earlier:\n\n{instruction}\n\n"
            "Carry it out with your tools, then write a brief note of what came of it.",
            phase="scheduler", model=self.ambient_model, tools=AMBIENT_TOOLS,
        )

    async def night_check(self, phase: str = "dusk") -> bool:
        """
        Brief sovereignty check at dusk or pre-dawn.

        Runs one ambient cycle with a phase-appropriate prompt, then reads
        the night_choice posture key. Returns True if the bot wants to be
        active (posture_update key='night_choice' value='awake' was called),
        False otherwise. Resets the key afterward.
        """
        if phase == "dusk":
            prompt = (
                "Night is beginning. The environment will be quiet for the next twelve hours.\n\n"
                "You can rest now, or stay up for one more cycle before sleeping.\n\n"
                "If you want to stay up: call posture_update with key='night_choice' value='awake', "
                "then do whatever you want — create, tend, read, wander. "
                "If you'd rather rest now: no action needed. Write a note if you like."
            )
        else:
            prompt = (
                "Dawn is about an hour away. You have been resting through the night.\n\n"
                "You can rise now and begin the day early, or sleep the final hour.\n\n"
                "If you want to rise: call posture_update with key='night_choice' value='awake'. "
                "If you'd rather rest the final hour: no action needed."
            )

        system_prompt = await self._build_system_prompt(mode="ambient")
        result = await self._run_loop(
            system_prompt, prompt,
            phase="ambient", model=self.ambient_model, tools=AMBIENT_TOOLS,
        )

        posture = await self.store.read_posture()
        wants_active = str(posture.get("night_choice", "rest")) == "awake"
        await self.store.update_posture("night_choice", "rest")

        if result.text:
            self.memory.append_ambient(f"night_check_{phase}", result.text)
            if self.send_callback:
                await self.send_callback("ambient", result.text[:1800], result.files or None)

        return wants_active

    async def _get_poem_fragment(self) -> str:
        """Extract 1-2 lines from an older creation for night surfacing."""
        try:
            raw = await self.store.recent_creations(limit=20)
            creations = json.loads(raw)
            if not creations:
                return ""
            # Prefer older creations, not the most recent
            candidates = creations[3:] if len(creations) > 3 else creations
            if not candidates:
                return ""
            creation = random.choice(candidates)
            content = (creation.get("content") or "").strip()
            if not content:
                return ""
            lines = [ln for ln in content.split("\n") if ln.strip()]
            if not lines:
                return ""
            return "\n".join(lines[:2])
        except Exception:
            return ""

    # ── system prompt ─────────────────────────────────────────────────

    async def _build_system_prompt(self, mode: str, human_id: str = "") -> List[Dict[str, Any]]:
        posture = await self.store.read_posture()
        identity_threads = await self.store.identity_threads(limit=8)
        recent_creations = await self.store.recent_creations(limit=3)
        recent_tool_calls = await self.store.recent_tool_calls(bot_id=self.instance_name, limit=8)
        habitat = await self.store.habitat_snapshot(bot_id=self.instance_name, event_limit=6)
        bot_self_memory = await self.store.recent_layered_memory(
            layer="bot_self_memory",
            bot_id=self.instance_name,
            limit=6,
        )
        human_memory = "[]"
        human_notebook = "[]"
        if human_id:
            human_memory = await self.store.recent_layered_memory(
                layer="human_memory",
                bot_id=self.instance_name,
                human_id=human_id,
                limit=6,
            )
            human_notebook = await self.store.recent_layered_memory(
                layer="human_notebook",
                bot_id=self.instance_name,
                human_id=human_id,
                limit=6,
            )

        self_inf_text = ""
        try:
            self_inf_raw = await self.store.recent_interpretations(limit=5, type_filter="self_inference")
            inferences = json.loads(self_inf_raw)
            if inferences:
                self_inf_text = "\n".join(
                    f"- {r.get('content','')[:200]} (confidence={r.get('confidence',0.5):.2f})"
                    for r in inferences
                )
        except Exception:
            pass

        recent_convs = await self.store.recent_conversations(limit=8)
        if mode == "ambient":
            recent_convs = recent_convs[-2:]

        identity_block = build_bot_identity_block(self.instance_name)
        dynamic_block = build_bot_dynamic_block(
            posture_state=posture,
            identity_threads=identity_threads,
            bot_self_memory=bot_self_memory,
            human_memory=human_memory,
            human_notebook=human_notebook,
            recent_tool_calls=recent_tool_calls,
            recent_creations=recent_creations,
            habitat_snapshot=habitat,
            mode=mode,
            recent_self_inferences=self_inf_text,
            day_night=self.day_night,
            recent_conversations=recent_convs,
            known_custom_emojis=KNOWN_CUSTOM_EMOJIS,
        )

        return [
            {"type": "text", "text": identity_block, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            {"type": "text", "text": dynamic_block},
        ]

    # ── final reply (the key addition over Ra) ────────────────────────

    async def _final_reply(
        self,
        system_prompt: Any,
        original_message: str,
        tool_log: List[Dict[str, Any]],
    ) -> str:
        """One call, no tools, produces the Discord reply.

        Sees the original message and a compact log of what tools
        returned — the model's own mid-loop prose is never passed in.
        """
        prompt = build_final_reply_prompt(original_message, tool_log)
        try:
            response = await self.model_adapter.complete(
                model=self.model,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=1500,
            )
            thinking = self.model_adapter.extract_thinking(response)
            if thinking and self.send_callback:
                await self.send_callback("mind", f"💭 {thinking[:1700]}", None)
            return self.model_adapter.extract_text(response).strip()
        except Exception as exc:
            log.warning("Final reply call failed: %s", exc)
            return ""

    # ── tool loop ─────────────────────────────────────────────────────

    async def _run_loop(
        self,
        system_prompt: Any,
        user_prompt: str,
        phase: str,
        model: str,
        tools: List[Dict[str, Any]],
        routing: Optional[RoutingDecision] = None,
    ) -> CognitionResult:
        adapter = self.ambient_model_adapter if phase == "ambient" else self.model_adapter
        messages: List[Dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        tool_log: List[Dict[str, Any]] = []
        files: List[Path] = []

        last_text = ""
        for round_no in range(MAX_TOOL_ROUNDS):
            response = await adapter.complete(
                model=model,
                system=system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=3000,
            )
            calls = adapter.extract_tool_calls(response)
            text = adapter.extract_text(response).strip()
            thinking = adapter.extract_thinking(response)
            if thinking and self.send_callback:
                # Thinking and speaking are different channels — reasoning
                # goes to the mind channel, never toward the reply.
                await self.send_callback("mind", f"💭 {thinking[:1700]}", None)
            if text:
                last_text = text
            if not calls:
                return CognitionResult(text=text, tool_log=tool_log, files=files)

            messages.append(adapter.assistant_message(response))
            tool_results: List[Dict[str, str]] = []
            for call in calls[:MAX_TOOL_CALLS]:
                args = call.input if isinstance(call.input, dict) else {}
                output, new_files = await self._dispatch(call.name, args, phase, routing=routing)
                files.extend(new_files)
                entry = {
                    "tool": call.name, "args": args,
                    "result": output[:2000], "phase": phase, "round": round_no,
                }
                tool_log.append(entry)
                tool_call_id = await self.store.log_tool_call(
                    event_id="", tool_name=call.name, phase=phase,
                    args=args, result_preview=output[:500],
                    success=_tool_succeeded(output),
                    bot_id=self.instance_name,
                )
                if _tool_succeeded(output):
                    await self._place_immediate_habitat_residue(call.name, args, output, phase, tool_call_id)
                if not _tool_succeeded(output) and self.send_callback:
                    await self.send_callback(
                        "logs",
                        f"Tool `{call.name}` failed during `{phase}`: `{output[:700]}`",
                        None,
                    )
                tool_results.append({
                    "id": call.id,
                    "content": output[:20000],
                })
            if adapter.provider in {"openai-compatible", "ollama"}:
                for result in tool_results:
                    messages.append(adapter.tool_result_message([result]))
            else:
                messages.append(adapter.tool_result_message(tool_results))

        # Round limit reached with tool calls still pending — give one tool-free
        # round so the model can produce a proper closing response.
        try:
            response = await adapter.complete(
                model=model,
                system=system_prompt,
                messages=messages,
                tools=[],
                max_tokens=3000,
            )
            final_text = adapter.extract_text(response).strip()
            if final_text:
                last_text = final_text
        except Exception as exc:
            log.warning("Final tool-free round failed: %s", exc)

        return CognitionResult(text=last_text, tool_log=tool_log, files=files)

    # ── tool dispatch ─────────────────────────────────────────────────

    async def _place_immediate_habitat_residue(
        self,
        tool_name: str,
        args: Dict[str, Any],
        output: str,
        phase: str,
        tool_call_id: str,
    ) -> None:
        if not tool_call_id or tool_call_id.startswith("error:") or tool_call_id.startswith("[no-postgres]"):
            return
        residue = self._classify_immediate_habitat_residue(tool_name, args, output, phase, tool_call_id)
        if not residue.get("has_residue"):
            await self.store.log_habitat_residue_decision(
                bot_id=self.instance_name,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase=phase,
                has_residue=False,
                reason=str(residue.get("reason", "Routine tool call; no habitat placement.")),
                metadata={"tool_args": args},
            )
            return
        entry_id = await self.store.place_habitat_entry(
            bot_id=self.instance_name,
            area=str(residue["area"]),
            entry_type=str(residue["entry_type"]),
            title=str(residue["title"]),
            content=str(residue.get("content", "")),
            source_type="tool",
            source_ref=tool_call_id,
            suggested_actions=list(residue.get("suggested_actions") or []),
            weight=float(residue.get("weight", 0.5)),
            confidence=float(residue.get("confidence", 0.7)),
            reason=str(residue["reason"]),
            metadata={
                "source_tool": tool_name,
                "phase": phase,
                "tool_args": args,
            },
        )
        await self.store.log_habitat_residue_decision(
            bot_id=self.instance_name,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase=phase,
            has_residue=not (entry_id.startswith("error:") or entry_id.startswith("[no-postgres]")),
            area=str(residue.get("area", "")),
            entry_type=str(residue.get("entry_type", "")),
            entry_id=entry_id if not (entry_id.startswith("error:") or entry_id.startswith("[no-postgres]")) else "",
            reason=str(residue["reason"]),
            confidence=float(residue.get("confidence", 0.7)),
            metadata={"tool_args": args, "placement_result": entry_id},
        )

    def _classify_immediate_habitat_residue(
        self,
        tool_name: str,
        args: Dict[str, Any],
        output: str,
        phase: str,
        tool_call_id: str,
    ) -> Dict[str, Any]:
        routine = {"has_residue": False, "reason": "Routine tool call; no habitat placement."}
        if tool_name in {"habitat_event", "habitat_update", "habitat_place", "habitat_snapshot"}:
            return {"has_residue": False, "reason": "Habitat tool call is already explicit; no classifier placement."}
        if tool_name == "creation_store":
            mode = str(args.get("mode", "creation")).strip()
            content = " ".join(str(args.get("content", "")).split())[:500]
            return {
                "has_residue": True,
                "area": "studio",
                "entry_type": "fragment",
                "title": f"{mode.replace('_', ' ').title()} fragment",
                "content": content,
                "suggested_actions": ["share", "revise", "leave_private", "connect", "archive"],
                "weight": 0.7,
                "confidence": 0.9,
                "reason": "creation_store produced durable creative output.",
            }
        if tool_name == "vestibule_hold":
            return {
                "has_residue": True,
                "area": "threshold",
                "entry_type": "marker",
                "title": "Held frame from vestibule",
                "content": str(args.get("held_reason", ""))[:500] or "An interpretation was held for later revisit.",
                "suggested_actions": ["keep_closed", "reopen", "explain", "soften", "strengthen"],
                "weight": 0.75,
                "confidence": 0.85,
                "reason": "vestibule_hold explicitly held a frame rather than routing it into action.",
            }
        if tool_name == "memory_review_decide":
            decision = str(args.get("decision", "")).strip()
            if decision not in {"hold", "reject", "archive", "decay"}:
                return {"has_residue": False, "reason": "Memory review decision did not create obvious habitat residue."}
            reason = " ".join(str(args.get("reason", "")).split())[:500]
            area = "threshold" if decision in {"reject", "archive"} else "garden"
            entry_type = "marker" if area == "threshold" else "seed"
            return {
                "has_residue": True,
                "area": area,
                "entry_type": entry_type,
                "title": f"Memory review {decision}",
                "content": reason or f"Memory review decision: {decision}.",
                "suggested_actions": ["revisit", "let_decay", "archive"] if area == "garden" else ["keep_closed", "reopen", "explain"],
                "weight": 0.65,
                "confidence": 0.8,
                "reason": f"memory_review_decide with {decision} left a {'boundary marker' if area == 'threshold' else 'garden seed'}.",
            }
        if tool_name == "web_search" and phase in {"ambient", "research"}:
            query = str(args.get("query", "")).strip()
            return {
                "has_residue": True,
                "area": "library",
                "entry_type": "shelf_item",
                "title": f"External material: {query[:80] or 'web search'}",
                "content": " ".join(str(output or "").split())[:500],
                "suggested_actions": ["read", "quote", "disagree", "connect", "shelve", "ignore"],
                "weight": 0.55,
                "confidence": 0.65,
                "reason": "web_search in ambient/research mode brought external material worth shelving.",
            }
        return routine

    def game_status_text(self) -> str:
        state = atlas_load_state(GAME_STATE_PATH)
        actions = ", ".join(atlas_available_actions(state))
        return f"{atlas_summarise(state)}\nAvailable actions: {actions}"

    async def game_act(self, action: str, detail: str = "", human_choice: bool = False, phase: str = "response") -> str:
        state = atlas_load_state(GAME_STATE_PATH)
        if human_choice:
            result = atlas_apply_human_choice(state, detail or action)
        else:
            result = atlas_act(state, action, detail)
        GAME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        atlas_save_state(result["state"], GAME_STATE_PATH)

        trace = result.get("trace")
        if isinstance(trace, dict):
            await self.store.place_habitat_entry(
                bot_id=self.instance_name,
                area=str(trace.get("area", "game")),
                entry_type=str(trace.get("entry_type", "object")),
                title=str(trace.get("title", "Game trace")),
                content=str(trace.get("content", "")),
                source_type="creative",
                source_ref=f"game:{result['state'].get('turn_count', 0)}",
                suggested_actions=list(trace.get("suggested_actions") or []),
                weight=float(trace.get("weight", 0.5)),
                confidence=float(trace.get("confidence", 0.7)),
                reason=str(trace.get("reason", "Threshold Atlas turn left placeable residue.")),
                metadata={
                    "game_key": result["state"].get("game_key", "threshold_atlas"),
                    "turn_count": result["state"].get("turn_count"),
                    "action": result["state"].get("last_action"),
                    "phase": phase,
                },
            )

        invite = result.get("invite")
        lines = [str(result.get("text", "")).strip(), "", atlas_summarise(result["state"])]
        if trace:
            lines.append(f"Trace: {trace.get('area', '?')}/{trace.get('entry_type', '?')} - {trace.get('title', '?')}")
        if isinstance(invite, dict):
            choices = ", ".join(str(choice) for choice in invite.get("choices", []))
            lines.append(f"Invite: {invite.get('prompt', '')} Choices: {choices}")
        text = "\n".join(line for line in lines if line is not None).strip()
        if phase == "ambient" and self.send_callback:
            await self.send_callback("games", text[:1800], None)
        return text

    async def _dispatch(
        self,
        name: str,
        args: Dict[str, Any],
        phase: str,
        routing: Optional[RoutingDecision] = None,
    ) -> Tuple[str, List[Path]]:
        files: List[Path] = []
        try:
            # memory
            if name == "memory_interpret":
                id = await self.store.store_interpretation(
                    type=str(args.get("type", "observation")),
                    content=str(args.get("content", "")),
                    confidence=float(args.get("confidence", 0.5)),
                    status=str(args.get("status", "provisional")),
                    tags=list(args.get("tags") or []),
                    source_actor=str(args.get("source_actor", "bot")),
                )
                return _stored_result("Interpretation", id), files

            if name == "memory_update_status":
                return await self.store.update_interpretation_status(
                    str(args.get("id", "")),
                    str(args.get("new_status", "")),
                    str(args.get("note", "")),
                ), files

            if name == "memory_link":
                return await self.store.link_interpretations(
                    str(args.get("from_id", "")),
                    str(args.get("to_id", "")),
                    str(args.get("link_type", "")),
                    str(args.get("note", "")),
                ), files

            if name == "memory_search":
                return await self.store.search_interpretations(
                    str(args.get("query", "")),
                    limit=int(args.get("limit", 10)),
                    status_filter=args.get("status_filter"),
                    type_filter=args.get("type_filter"),
                ), files

            if name == "memory_recent":
                return await self.store.recent_interpretations(
                    limit=int(args.get("limit", 10)),
                    type_filter=args.get("type"),
                    status_filter=args.get("status"),
                    tag_filter=args.get("tag"),
                ), files

            if name == "human_memory_store":
                id = await self.store.store_human_memory(
                    bot_id=self.instance_name,
                    human_id=str(args.get("human_id", "")),
                    memory_type=str(args.get("memory_type", "other")),
                    content=str(args.get("content", "")),
                    confidence=float(args.get("confidence", 0.5)),
                    consent_status=str(args.get("consent_status", "inferred_low_risk")),
                    tags=list(args.get("tags") or []),
                    admission_category=str(args.get("admission_category", "")),
                    admission_reason=str(args.get("admission_reason", "")),
                )
                return _stored_result("Human memory", id), files

            if name == "human_notebook_store":
                id = await self.store.store_human_notebook(
                    bot_id=self.instance_name,
                    human_id=str(args.get("human_id", "")),
                    entry_type=str(args.get("entry_type", "note")),
                    content=str(args.get("content", "")),
                    title=str(args.get("title", "")),
                    due_at=args.get("due_at"),
                    recurrence=str(args.get("recurrence", "")),
                    consent_status=str(args.get("consent_status", "explicit")),
                    tags=list(args.get("tags") or []),
                    admission_category=str(args.get("admission_category", "")),
                    admission_reason=str(args.get("admission_reason", "")),
                )
                return _stored_result("Notebook entry", id), files

            if name == "bot_self_memory_candidate_store":
                if phase != "ambient":
                    return (
                        "error: bot self-memory candidates cannot be stored during direct human response/reflection; "
                        "use ambient/tending review after recurrence, not immediate human-prompted identity logging."
                    ), files
                id = await self.store.store_bot_self_memory_candidate(
                    bot_id=self.instance_name,
                    memory_type=str(args.get("memory_type", "open_question")),
                    content=str(args.get("content", "")),
                    confidence=float(args.get("confidence", 0.4)),
                    source_kind=str(args.get("source_kind", "bot")),
                    identity_relevant=bool(args.get("identity_relevant", False)),
                    promotion_reason=str(args.get("promotion_reason", "")),
                    tags=list(args.get("tags") or []),
                )
                return _stored_result("Bot self-memory candidate", id), files

            if name == "layered_memory_recent":
                return await self.store.recent_layered_memory(
                    layer=str(args.get("layer", "")),
                    bot_id=self.instance_name,
                    human_id=str(args.get("human_id", "")),
                    limit=int(args.get("limit", 5)),
                ), files

            # consult (ambient/dream only — the tool is not in the chat set)
            if name == "consult":
                return await self.consult.ask(str(args.get("question", ""))), files

            if name == "consult_log_read":
                return self.consult.log_read(int(args.get("limit", 5) or 5)), files

            # knowledge graph
            if name == "kg_search":
                return await self.graph.search(
                    str(args.get("query", "")), limit=int(args.get("limit", 8) or 8)
                ), files

            if name == "kg_add_fact":
                subject = str(args.get("subject", ""))
                obj = str(args.get("object", ""))
                # Person facts belong in the human lane — code gate, not prompt.
                if names_person(subject) or names_person(obj):
                    return (
                        "That is a fact about a person — it belongs in human "
                        "memory, not the world graph. save_human_memory holds it."
                    ), files
                ok, msg = await self.graph.add_fact(
                    subject, str(args.get("predicate", "")), obj
                )
                if ok:
                    self.store.yin.world.add(
                        f"{subject} {args.get('predicate', '')} {obj}"
                    )
                return msg, files

            if name == "kg_prune":
                return await self.graph.prune_orphans(), files

            # scheduler (rules live in scheduler.py — code gates, not prompt)
            if name == "schedule_task":
                ok, msg = self.scheduler.add_task(
                    str(args.get("instruction", "")),
                    int(args.get("interval_minutes", 0) or 0),
                )
                return msg, files

            if name == "schedule_list":
                return self.scheduler.list_tasks(), files

            if name == "schedule_cancel":
                ok, msg = self.scheduler.cancel_task(int(args.get("task_id", 0) or 0))
                return msg, files

            # creations
            if name == "creation_store":
                mode = str(args.get("mode", "creation"))
                content = str(args.get("content", ""))
                id = await self.store.store_creation(
                    mode=mode, content=content,
                    prompted_by=args.get("prompted_by_id"),
                    tags=list(args.get("tags") or []),
                )
                self.memory.append_creation(mode, content)
                if self.send_callback:
                    await self.send_callback("creates", content, None)
                return _stored_result("Creation", id), files

            if name == "creation_recent":
                return await self.store.recent_creations(
                    limit=int(args.get("limit", 5)),
                    mode_filter=args.get("mode_filter"),
                ), files

            # habitat
            if name == "habitat_snapshot":
                return await self.store.habitat_snapshot(
                    bot_id=self.instance_name,
                    area=str(args.get("area", "")),
                    event_limit=int(args.get("event_limit", 8)),
                ), files

            if name == "game_status":
                return self.game_status_text(), files

            if name == "game_act":
                return await self.game_act(
                    action=str(args.get("action", "observe")),
                    detail=str(args.get("detail", "")),
                    human_choice=bool(args.get("human_choice", False)),
                    phase=phase,
                ), files

            if name == "react_to_message":
                emoji = str(args.get("emoji", "")).strip()
                if not emoji:
                    return "error: emoji is required", files
                if phase != "response" or not self.reaction_callback:
                    return "error: reactions are only available while answering a current human message", files
                try:
                    await self.reaction_callback(emoji)
                except Exception as exc:
                    return f"error: reaction failed: {exc}", files
                return f"Reacted to current message with {emoji}", files

            if name == "habitat_event":
                if phase != "ambient":
                    return "error: habitat mutation is ambient/tending only; human chat can influence habitat but cannot directly command it.", files
                id = await self.store.log_habitat_event(
                    bot_id=self.instance_name,
                    area=str(args.get("area", "")),
                    action=str(args.get("action", "")),
                    content=str(args.get("content", "")),
                    metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else {},
                )
                return _stored_result("Habitat event", id), files

            if name == "habitat_place":
                if phase != "ambient":
                    return "error: habitat placement is ambient/tending only; human chat can influence habitat but cannot directly command it.", files
                id = await self.store.place_habitat_entry(
                    bot_id=self.instance_name,
                    area=str(args.get("area", "")),
                    entry_type=str(args.get("entry_type", "")),
                    title=str(args.get("title", "")),
                    content=str(args.get("content", "")),
                    source_type="autonomous",
                    suggested_actions=list(args.get("suggested_actions") or []),
                    weight=float(args.get("weight", 0.5)),
                    confidence=float(args.get("confidence", 0.7)),
                    reason=str(args.get("reason", "")),
                    metadata={"placed_by": "bot"},
                )
                return _stored_result("Habitat entry", id), files

            if name == "habitat_update":
                if phase != "ambient":
                    return "error: habitat mutation is ambient/tending only; human chat can influence habitat but cannot directly command it.", files
                id = await self.store.update_habitat_state(
                    bot_id=self.instance_name,
                    area=str(args.get("area", "")),
                    state_patch=args.get("state_patch") if isinstance(args.get("state_patch"), dict) else {},
                    note=str(args.get("note", "")),
                )
                return _stored_result("Habitat state", id), files

            # posture
            if name == "posture_read":
                return json.dumps(await self.store.read_posture(), ensure_ascii=False), files

            if name == "posture_update":
                return await self.store.update_posture(
                    str(args.get("key", "")), args.get("value"),
                ), files

            # vestibule
            if name == "vestibule_hold":
                return await self.store.hold_interpretation(
                    str(args.get("interpretation_id", "")),
                    str(args.get("held_reason", "")),
                    args.get("revisit_after"),
                ), files

            if name == "vestibule_check":
                return await self.store.check_vestibule(limit=int(args.get("limit", 5))), files

            # deferred
            if name == "defer_response":
                id = await self.store.defer_response(
                    incoming_text=str(args.get("incoming_text", "")),
                    author=str(args.get("author", "")),
                    channel=str(args.get("channel", "")),
                    answer_after=args.get("answer_after"),
                )
                return _stored_result("Deferred response", id), files

            if name == "deferred_check":
                pending = await self.store.pending_deferred(limit=int(args.get("limit", 5)))
                return json.dumps(pending, ensure_ascii=False, default=str), files

            if name == "memory_review_candidates":
                return await self.store.review_candidates(
                    bot_id=self.instance_name,
                    limit=int(args.get("limit", 8)),
                    include_identity=bool(args.get("include_identity", True)),
                ), files

            if name == "memory_review_decide":
                id = await self.store.decide_memory_review(
                    bot_id=self.instance_name,
                    candidate_id=str(args.get("candidate_id", "")),
                    decision=str(args.get("decision", "")),
                    reason=str(args.get("reason", "")),
                    context_key=str(args.get("context_key", "general")),
                    reviewed_by="bot",
                )
                return _stored_result("Memory review", id), files

            if name == "memory_context_list":
                return await self.store.list_memory_contexts(
                    bot_id=self.instance_name,
                    include_archived=bool(args.get("include_archived", False)),
                ), files

            if name == "memory_context_create":
                id = await self.store.ensure_memory_context(
                    bot_id=self.instance_name,
                    key=str(args.get("key", "general")),
                    title=str(args.get("title", "General")),
                    summary=str(args.get("summary", "")),
                    created_by="bot",
                )
                return _stored_result("Memory context", id), files

            # event log
            if name == "event_log":
                id = await self.store.log_event(
                    source_type=str(args.get("source_type", "system")),
                    content=str(args.get("content", "")),
                    metadata=args.get("metadata"),
                )
                return f"Logged: {id}", files

            # image generation
            if name == "create_image":
                path, msg = await self._create_image(str(args.get("prompt", "")))
                if path:
                    files.append(path)
                return msg, files

            # web search
            if name == "web_search":
                return await self._handle_web_search(str(args.get("query", ""))), files

            # library
            if name == "library_list":
                return self.library.list_books(), files

            if name == "library_read":
                return self.library.read_pages(
                    instance_name=self.instance_name,
                    title=str(args.get("title", "")),
                    start_page=int(args.get("start_page", 1)),
                    pages=int(args.get("pages", 3)),
                ), files

            if name == "library_status":
                return self.library.progress(self.instance_name), files

            # codebase
            if name == "code_list":
                return json.dumps(self.codebase.list_files(), ensure_ascii=False), files

            if name == "code_read":
                return self.codebase.read_file(str(args.get("path", ""))), files

            if name == "extension_write":
                if not EXTENSIONS_WRITE_ENABLED:
                    return "Extension writes disabled (EXTENSIONS_WRITE_ENABLED=false).", files
                return self.codebase.write_file(
                    str(args.get("path", "")), str(args.get("content", "")),
                ), files

            if name == "extension_load":
                return self.codebase.hot_load(str(args.get("path", ""))), files

            if name == "extension_list":
                pattern = str(args.get("pattern", "*.py"))
                matches = list(self.codebase.extensions_dir.glob(pattern))
                return "\n".join(
                    str(f.relative_to(self.codebase.project_root)) for f in matches
                ) or "(empty)", files

            if name == "extension_read":
                path = str(args.get("path", ""))
                if not path.startswith("extensions/"):
                    path = f"extensions/{path}"
                return self.codebase.read_file(path), files

            return f"Unknown tool: {name}", files

        except Exception as exc:
            log.exception("Tool dispatch error: %s", name)
            return f"error: {name} failed: {exc}", files

    # ── commons ───────────────────────────────────────────────────────

    # ── web search ────────────────────────────────────────────────────

    async def _handle_web_search(self, query: str) -> str:
        if not self._openai:
            return "web_search unavailable — OPENAI_API_KEY is not set."
        if not query.strip():
            return "web_search requires a non-empty query."
        try:
            response = await self._openai.responses.create(
                model=OPENAI_WEB_MODEL,
                input=(
                    f"Search the web for: {query}\n"
                    "Return concise findings with source names and URLs where available."
                ),
                tools=[{"type": "web_search_preview"}],
                max_output_tokens=2000,
            )
            text = getattr(response, "output_text", "") or ""
            if text:
                return text
            parts = []
            for item in getattr(response, "output", []) or []:
                for content in getattr(item, "content", []) or []:
                    content_text = getattr(content, "text", None)
                    if content_text:
                        parts.append(content_text)
            return "\n".join(parts) or "No search results returned."
        except Exception as exc:
            log.error("web_search failed: %s", exc)
            return f"web_search failed: {exc}"

    # ── image generation ──────────────────────────────────────────────

    async def _create_image(self, prompt: str) -> Tuple[Optional[Path], str]:
        if not self._openai:
            return None, "create_image unavailable — OPENAI_API_KEY is not set."
        if not prompt:
            return None, "No prompt provided."
        try:
            import urllib.request
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
            response = await self._openai.images.generate(
                model=OPENAI_IMAGE_MODEL,
                prompt=prompt,
                n=1,
                size="1024x1024",
            )
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_name = "".join(ch.lower() if ch.isalnum() else "_" for ch in self.instance_name).strip("_") or "bot"
            path = ARTIFACTS_DIR / f"{safe_name}_{ts}.png"
            image_data = response.data[0]
            if getattr(image_data, "b64_json", None):
                import base64
                path.write_bytes(base64.b64decode(image_data.b64_json))
            else:
                url = image_data.url
                urllib.request.urlretrieve(url, path)
            return path, f"Image generated: {path.name}"
        except Exception as exc:
            log.error("create_image failed: %s", exc)
            return None, f"error: create_image failed: {exc}"
