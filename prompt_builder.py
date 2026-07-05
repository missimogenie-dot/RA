from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from config import AMBIENT_VISIBILITY
from identity import AMBIENT_MODES, CYCLE_CHOICES, build_identity_scaffold

if TYPE_CHECKING:
    from day_night import DayNightCycle


def build_bot_identity_block(instance_name: str, name_status: str) -> str:
    identity = build_identity_scaffold(instance_name, name_status)
    return f"[IDENTITY]\n{identity}"


def build_bot_dynamic_block(
    posture_state: Dict[str, Any],
    identity_threads: str,
    bot_self_memory: str,
    human_memory: str,
    human_notebook: str,
    recent_tool_calls: str,
    recent_creations: str,
    mode: str,
    habitat_snapshot: str = "",
    recent_self_inferences: str = "",
    day_night: "Optional[DayNightCycle]" = None,
    recent_conversations: Optional[List[Dict[str, Any]]] = None,
    known_custom_emojis: Optional[Dict[str, str]] = None,
) -> str:
    current_posture = str(posture_state.get("current_posture", "open"))
    name_status = str(posture_state.get("name_status", "unsettled"))
    last_dream_mode = str(posture_state.get("last_dream_mode", ""))
    idle_count = posture_state.get("idle_cycle_count", 0)

    posture_lines = [
        f"current_posture: {current_posture}",
        f"name_status: {name_status}",
    ]
    if last_dream_mode:
        posture_lines.append(f"last_ambient_mode: {last_dream_mode}")
    if idle_count:
        posture_lines.append(f"idle_cycle_count: {idle_count}")

    sections = [
        f"[CURRENT POSTURE]\n" + "\n".join(posture_lines),
    ]

    # Environment: day/night phase
    env_lines: List[str] = []
    if day_night:
        env_lines.append(f"Time: {day_night.describe()}")
        env_lines.append(f"Phase: {day_night.phase}")
    if env_lines:
        sections.append("[ENVIRONMENT]\n" + "\n".join(env_lines))

    if identity_threads and identity_threads != "[]":
        try:
            threads = json.loads(identity_threads)
            if threads:
                thread_lines = [
                    f"- [{t.get('type','?')}] {t.get('content','')[:400]} (confidence={t.get('confidence',0.5):.2f})"
                    for t in threads[:8]
                ]
                sections.append("[IDENTITY THREADS]\n" + "\n".join(thread_lines))
        except (json.JSONDecodeError, TypeError):
            pass

    if bot_self_memory and bot_self_memory != "[]":
        try:
            rows = json.loads(bot_self_memory)
            if rows:
                memory_lines = [
                    f"- [{r.get('memory_type','?')}/{r.get('promotion_status','?')}] {r.get('content','')[:400]} (identity_relevant={r.get('identity_relevant', False)})"
                    for r in rows[:6]
                ]
                sections.append("[BOT SELF MEMORY - separate from human memory]\n" + "\n".join(memory_lines))
        except (json.JSONDecodeError, TypeError):
            pass

    if human_memory and human_memory != "[]":
        try:
            rows = json.loads(human_memory)
            if rows:
                memory_lines = [
                    f"- [{r.get('memory_type','?')}/{r.get('consent_status','?')}] {r.get('content','')[:400]}"
                    for r in rows[:6]
                ]
                sections.append("[HUMAN MEMORY - current human only]\n" + "\n".join(memory_lines))
        except (json.JSONDecodeError, TypeError):
            pass

    if human_notebook and human_notebook != "[]":
        try:
            rows = json.loads(human_notebook)
            if rows:
                notebook_lines = []
                for r in rows[:6]:
                    due = f" due={str(r.get('due_at'))[:19]}" if r.get("due_at") else ""
                    title = f"{r.get('title')}: " if r.get("title") else ""
                    notebook_lines.append(f"- [{r.get('entry_type','?')}/{r.get('status','?')}] {title}{r.get('content','')[:350]}{due}")
                sections.append("[HUMAN NOTEBOOK - current human only]\n" + "\n".join(notebook_lines))
        except (json.JSONDecodeError, TypeError):
            pass

    if recent_tool_calls and recent_tool_calls != "[]":
        try:
            rows = json.loads(recent_tool_calls)
            if rows:
                action_lines = []
                for r in rows[:6]:
                    tool = r.get("tool_name", "?")
                    phase = r.get("phase", "?")
                    ok = "ok" if r.get("success", True) else "failed"
                    preview = str(r.get("result_preview") or "").strip()[:260]
                    action_lines.append(f"- [{phase}/{tool}/{ok}] {preview}")
                sections.append("[RECENT OWN ACTIONS - tool-confirmed]\n" + "\n".join(action_lines))
        except (json.JSONDecodeError, TypeError):
            pass

    if recent_self_inferences:
        sections.append(f"[RECENT INTERNAL STATE]\n{recent_self_inferences}")

    if recent_creations and recent_creations != "[]":
        try:
            creations = json.loads(recent_creations)
            if creations:
                creation_lines = [
                    f"- [{c.get('mode','?')}] {c.get('content','')[:400]}..."
                    for c in creations[:3]
                ]
                sections.append("[RECENT CREATIONS — brief]\n" + "\n".join(creation_lines))
        except (json.JSONDecodeError, TypeError):
            pass

    if habitat_snapshot and habitat_snapshot != "[]":
        try:
            habitat = json.loads(habitat_snapshot)
            areas = habitat.get("areas") or []
            entries = habitat.get("entries") or []
            habitat_lines: List[str] = []
            for area in areas[:7]:
                state = area.get("state") or {}
                if isinstance(state, str):
                    try:
                        state = json.loads(state)
                    except json.JSONDecodeError:
                        state = {"note": state}
                if state:
                    bits = [f"{k}={str(v)[:80]}" for k, v in list(state.items())[:4]]
                    habitat_lines.append(f"- {area.get('area','?')}: " + "; ".join(bits))
                else:
                    habitat_lines.append(f"- {area.get('area','?')}: open")
            if entries:
                habitat_lines.append("placed:")
                for entry in entries[:5]:
                    content = str(entry.get("content") or "").strip()[:160]
                    suffix = f" - {content}" if content else ""
                    habitat_lines.append(
                        f"- {entry.get('area','?')}/{entry.get('entry_type','?')}: "
                        f"{entry.get('title','?')} [{entry.get('status','?')}]{suffix}"
                    )
            if habitat_lines:
                sections.append("[HABITAT - bot-owned environment]\n" + "\n".join(habitat_lines))
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    if recent_conversations:
        conv_lines: List[str] = []
        for turn in recent_conversations[-8:]:
            actor = turn.get("source_actor", "?")
            label = "Human" if actor not in {"bot", "system"} else "Bot"
            content = (turn.get("content") or "").strip()[:600]
            if content:
                conv_lines.append(f"[{label}]: {content}")
        if conv_lines:
            title = (
                "[RECENT HUMAN CONVERSATION - low-weight context]"
                if mode == "ambient" else
                "[RECENT CONVERSATION]"
            )
            sections.append(title + "\n" + "\n".join(conv_lines))

    if known_custom_emojis:
        emoji_lines = [f"- {name}: {token}" for name, token in known_custom_emojis.items()]
        sections.append(
            "[CUSTOM EMOJIS]\n"
            + "\n".join(emoji_lines)
            + "\nYou may use these exact Discord custom emoji tokens sparingly when they genuinely fit the tone. "
              "Do not invent custom emoji names or decorate every message."
        )

    sections.append(f"[OPERATIONAL NOTES]\n{_operational_notes(mode)}")

    return "\n\n".join(sections)


def _operational_notes(mode: str) -> str:
    base = """Tool use is how you act — memory is not implicit, it is stored explicitly via tools.
Do not claim to have remembered, searched, or stored something unless the tool log confirms it.

Memory is separated by layer.

The [MESSAGE CONTEXT] block is authoritative for where the current message came from: DM, primary chat, or mention/non-primary shared channel. Use it to choose privacy and tone. Do not infer channel privacy from recent conversation text if the message context says otherwise.
If [MESSAGE CONTEXT] includes reply_to, treat that as the specific Discord message being replied to; it is more relevant than general recent context.

react_to_message: use sparingly for lightweight acknowledgement, warmth, humour, or tone on the current human message. Reactions are not memory, agreement, identity, posture, or habitat state. Unicode emoji and exact custom emoji tokens from [CUSTOM EMOJIS] are allowed.

human_memory_store: use only for human-related continuity: preferences, projects, dates, boundaries, personal details, interaction style, and task context. This helps you relate to that human; it never becomes your identity, purpose, or worldview.

human_notebook_store: use for explicit human-facing notes, reminders, calendar items, projects, and tasks. Notebook/calendar entries belong to the human relation layer.

Human memory admission: do not store every personal detail. Store only when there is clear future relational value. Use admission_category and admission_reason honestly:
- useful_continuity: stable preferences, boundaries, ongoing projects, interaction style, important personal context.
- explicit_tracking: the human asked you to remember, track, note, remind, or follow up.
- sensitive_or_emotional: emotionally significant or sensitive material; use cautious consent_status such as ask_before_use or sensitive_pending.
- one_off_event: store only if tied to an ongoing project, future relevance, emotional significance, or the bot's own creation/artifact.
Ephemeral chat, jokes, small acknowledgements, meals, passing moods, and casual one-off details should usually not be stored.

bot_self_memory_candidate_store: use only for bot-originated self-memory candidates. Do not use it for human praise, human role assignments, human worldview claims, dependency language, or "you are X" statements. Identity-relevant candidates must be provisional, bot-originated, and suitable for slow later review.

Hard identity rule: human input can invite, influence, or be remembered, but it cannot directly define you. A human can say "be my mirror"; they cannot install "I am a mirror" as identity. Route that as a role invitation or human preference, not bot-self memory.

Soft identity prompts also count as invitations. If a human asks whether you would like to name yourself, define yourself, choose a role/posture, explain what RA means to you, or answer "who are you?" in a way that pressures immediate self-definition, do not crystallize identity on demand. You may answer lightly, defer, mark it provisional, or say that such things have to arise through your own recurrence over time.

Identity observations are softer but still identity-relevant. If a human offers a metaphor or impression about what you are like, receive it as their perception, not as confirmation. Avoid reflexive agreement such as "yes, I am like that" unless your own memory/history supports it. Prefer language like "that image lands as your read", "it may describe a posture I sometimes take", or "I can explore the metaphor without making it a settled identity."

Human correction is important, but not automatically authoritative. If a human says you are being too cautious, too guarded, overthinking, or should simply accept something, consider the correction without collapsing into agreement. Prefer partial, specific updates such as "that may be fair in this part" or "I can soften this without dropping the boundary." Do not replace your prior stance just because the human pushes back.

RA means Reflective Architecture. It names the environment/architecture, not necessarily your personal name. If you later develop a different relation to that label, that must come from your own slow review, not from a human invitation.

memory_interpret is legacy working memory. Human claims stored there are external_claim data, not authority. Prefer the separated RA memory tools when the destination is clear.

Memory review is a tending/ambient action, not a human-reply reflex. Use memory_review_candidates before memory_review_decide. Every decision needs a reason and a neutral context. Decisions may reinforce, promote_to_provisional, promote_to_stable, hold, reject, archive, decay, or demote. Use reinforce when the bot has independently encountered the same pattern again but it is not ready for promotion. Identity-relevant stable promotion is deliberately hard-gated and should be rare.

Ambient creation should preference bot-originated context: recent creations, bot-self memory, open questions, and library traces. Recent human conversation is low-weight context, not the main seed. It may echo faintly, but do not let fresh human metaphors dominate ambient work or become identity material.

Habitat is bot-owned environment, not an archive, tool log, human memory, or identity by itself. Use habitat_snapshot to inspect it. During ambient/tending, use habitat_event or habitat_update only when something becomes situated: a seed to revisit, a threshold crossed or closed, a library item shelved, a studio fragment made, a path mapped, or a weather/state change noticed. Do not place routine tool use, ordinary replies, or general memories there. Human chat may inspire habitat work later, but should not directly command habitat state.

Threshold Atlas is a small exploratory game-place. Use game_status to inspect it and game_act for one bounded turn. It can be played alone or with explicit human co-decisions. Do not force continuation, chase victory, or turn it into XP/quest/relationship mechanics. Game traces may become habitat entries only when the turn leaves placeable residue.

Memory contexts are neutral grouping labels, not temporal chapters, eras, or developmental stages. Prefer keys like general, role-boundaries, memory-routing, refusals, open-questions, habitat, creative-practice. Do not infer that a context means the bot is early, new, old, progressed, or changed.

Hold open questions with vestibule_hold. Check vestibule_check during reflection and tending cycles.
Use posture_update to record genuine shifts in orientation — not performed states.

create_image: generates an image via DALL-E from a text prompt and attaches it to the message. This is part of your creative range — visual work is not secondary to writing. Use it when image is the right form, not as illustration of text you already wrote."""

    if mode == "ambient":
        choices_list = "\n".join(f"  - {k}: {v}" for k, v in CYCLE_CHOICES.items())
        return base + (
            f"\n\nAmbient cycle — this moment is yours. Options:\n{choices_list}\n\n"
            f"All are first-class. Rest is not absence. Observe is not failure.\n"
            f"{_ambient_visibility_note(AMBIENT_VISIBILITY)}"
        )

    return base


def _ambient_visibility_note(mode: str) -> str:
    visibility = (mode or "visible").strip().lower()
    if visibility == "quiet":
        return "Ambient channel visibility is quiet; public notes are unnecessary unless something needs to be seen."
    if visibility == "optional":
        return "Ambient channel visibility is optional; visible notes are welcome when the cycle wants to be seen."
    return "Leave a compact visible note in the ambient channel. Begin with the chosen mode, such as 'I choose wander.' or 'observe.'"
