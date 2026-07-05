"""
Mentor — post-exchange reflection. Advisory only.

Runs async after a reply is sent: never blocks, never rewrites the
reply, never touches the human lane. It may save a lesson or preference
— both pass through the evidence gate (verbatim quote from this
exchange only; recalled memory is not evidence) — or update a goal.
A pass that saves nothing is a normal outcome.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("yin.mentor")

MENTOR_SYSTEM_PROMPT = (
    "You are the reflection pass, running quietly after an exchange has "
    "already ended. The reply has been sent; nothing here changes it. "
    "Look at the exchange once: if it genuinely taught something durable "
    "about how to work or relate, save a lesson. If it revealed a stable "
    "preference of your own, save a preference. If it moved a goal, update "
    "it. Evidence must be an exact quote from this exchange. Most "
    "exchanges teach nothing durable — saving nothing is the usual and "
    "correct outcome. One decision, no follow-up questions."
)

_S = {"type": "string"}

MENTOR_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "add_lesson",
        "description": "Save a durable lesson from this exchange. Requires a verbatim quote from the exchange as evidence.",
        "input_schema": {
            "type": "object",
            "properties": {"lesson": _S, "evidence": _S},
            "required": ["lesson", "evidence"],
        },
    },
    {
        "name": "add_preference",
        "description": "Save a stable preference of your own revealed by this exchange. Requires a verbatim quote as evidence.",
        "input_schema": {
            "type": "object",
            "properties": {"preference": _S, "evidence": _S},
            "required": ["preference", "evidence"],
        },
    },
    {
        "name": "update_goal",
        "description": "Update a goal's status (open, active, done, dropped).",
        "input_schema": {
            "type": "object",
            "properties": {"goal_id": _S, "status": _S},
            "required": ["goal_id", "status"],
        },
    },
]


class Mentor:
    def __init__(self, adapter, model: str, yin_memory, logs, instance_name: str = "Yin",
                 send_callback=None) -> None:
        self.adapter = adapter
        self.model = model
        self.yin = yin_memory
        self.logs = logs
        self.instance_name = instance_name
        self.send_callback = send_callback

    async def reflect(self, user_text: str, reply_text: str, human_id: str = "") -> None:
        """Advisory pass. All failures are logged and swallowed."""
        try:
            await self._reflect(user_text, reply_text)
        except Exception as exc:
            log.warning("mentor pass failed (advisory, ignored): %s", exc)

    async def _reflect(self, user_text: str, reply_text: str) -> None:
        live_conversation = f"Human: {user_text}\n{self.instance_name}: {reply_text}"
        # Recent recalled material — the gate rejects it as evidence.
        recalled = [
            self.yin.lessons.recent(5),
            self.yin.preferences.recent(5),
        ]

        response = await self.adapter.complete(
            model=self.model,
            system=MENTOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content":
                f"The exchange:\n\n{live_conversation}\n\n"
                "Reflect once and act only if something durable is here."}],
            tools=MENTOR_TOOLS,
            max_tokens=600,
        )

        outcomes: List[str] = []
        for call in self.adapter.extract_tool_calls(response)[:3]:
            args = call.input if isinstance(call.input, dict) else {}
            if call.name == "add_lesson":
                ok, msg = self.yin.lessons.add_lesson(
                    str(args.get("lesson", "")),
                    evidence=str(args.get("evidence", "")),
                    live_conversation=live_conversation,
                    recalled_texts=recalled,
                    phase="reflection",
                )
            elif call.name == "add_preference":
                ok, msg = self.yin.preferences.add_preference(
                    str(args.get("preference", "")),
                    evidence=str(args.get("evidence", "")),
                    live_conversation=live_conversation,
                    recalled_texts=recalled,
                    phase="reflection",
                )
            elif call.name == "update_goal":
                ok, msg = self.yin.goals.update_goal(
                    str(args.get("goal_id", "")), str(args.get("status", ""))
                )
            else:
                continue
            outcomes.append(f"{call.name}: {msg}")

        if outcomes:
            summary = "; ".join(outcomes)
            self.logs.log_event("reflection", summary)
            if self.send_callback:
                await self.send_callback("mind", f"🔍 Reflection: {summary[:1500]}", None)
        else:
            self.logs.log_event("reflection", "pass — nothing durable this exchange")
