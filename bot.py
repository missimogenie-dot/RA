from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import discord

from cognition import CognitionEngine, CognitionResult
from config import (
    AMBIENT_CHANNEL_NAME,
    AMBIENT_CHANNEL_ID,
    AMBIENT_ENABLED,
    AUTO_RESPONSE_ENABLED,
    CHAT_CHANNEL_ID,
    CREATES_CHANNEL_NAME,
    CREATES_CHANNEL_ID,
    CURATOR_CHANNEL_ID,
    CURATOR_CHANNEL_NAME,
    DAY_NIGHT_ENABLED,
    DISCORD_TOKEN,
    DM_CHAT_ENABLED,
    DM_COMMANDS_ENABLED,
    DM_INITIATIONS_ENABLED,
    DM_NOTEBOOK_REMINDERS,
    DREAM_HOUR,
    GAMES_CHANNEL_ID,
    GAMES_CHANNEL_NAME,
    GENERAL_CHANNEL_NAME,
    HABITAT_CHANNEL_NAME,
    INITIATION_COOLDOWN_MINUTES,
    LOGS_CHANNEL_ID,
    LOGS_CHANNEL_NAME,
    MIND_CHANNEL_ID,
    NIGHT_SLEEP_INTERVAL,
    PRIMARY_CHAT_CHANNEL_NAME,
    RECENT_CONTEXT_LIMIT,
    RUNTIME_STATE_DIR,
    THOUGHTS_CHANNEL_NAME,
)
from dream_cycle import DreamCycle
from heartbeat import Heartbeat
from influence_router import is_acknowledgement_only, is_no_reply_marker
from memory import BotMemory
from world_clock import WorldClock

log = logging.getLogger("ra.bot")

DISCORD_LIMIT = 2000


def _chunks(text: str, limit: int = DISCORD_LIMIT) -> List[str]:
    if not text:
        return []
    chunks: List[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at < 1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks


def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _looks_like_diagnostic_response(text: str) -> bool:
    first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return (
        first_line.startswith("**Yin status**")
        or first_line.startswith("**Recent routing traces**")
        or first_line.startswith("**Trace ")
        or first_line.startswith("**Memory commands**")
        or first_line.startswith("**Habitat residue audit**")
        or first_line.startswith("**Due notebook items**")
        or first_line.startswith("**Initiation audit**")
        or first_line.startswith("**Threshold Atlas**")
        or first_line in {
            "Human memory",
            "Human notebook",
            "Bot self-memory",
            "Protected identity threads",
            "Memory review candidates",
            "Memory contexts",
            "Memory admissions",
            "Memory curator actions",
            "Habitat",
            "No due notebook items.",
            "No initiation attempts recorded.",
        }
        or first_line.startswith("No records found for `")
    )


def _context_excerpt(text: str, limit: int = 2500) -> str:
    cleaned = " ".join((text or "").strip().split())
    if len(cleaned) <= limit:
        return cleaned
    window = cleaned[:limit]
    cut_points = [window.rfind(mark) for mark in [". ", "! ", "? ", "\n"]]
    cut_at = max(cut_points)
    if cut_at < int(limit * 0.55):
        cut_at = window.rfind(" ")
    if cut_at < int(limit * 0.35):
        cut_at = limit
    return window[:cut_at].rstrip(" .,;:") + "..."


class BotClient(discord.Client):
    def __init__(
        self,
        cognition: CognitionEngine,
        memory: BotMemory,
        heartbeat: Heartbeat,
        token: str = DISCORD_TOKEN,
    ) -> None:
        intents = discord.Intents.default()
        intents.messages = True
        intents.dm_messages = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.cognition = cognition
        self.memory = memory
        self.heartbeat = heartbeat
        self.token = token
        self.channel_ids: Dict[str, int] = {
            "chat":    CHAT_CHANNEL_ID,
            "mind":    MIND_CHANNEL_ID,
            "logs":    LOGS_CHANNEL_ID,
            "ambient": AMBIENT_CHANNEL_ID,
            "creates": CREATES_CHANNEL_ID,
            "games":   GAMES_CHANNEL_ID,
            "curator": CURATOR_CHANNEL_ID,
        }
        self._process_lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._ambient_enabled = AMBIENT_ENABLED
        self._human_turn_count = 0
        self._last_activity = "startup"
        self._last_dream_date = ""
        self.world_clock = WorldClock(Path(RUNTIME_STATE_DIR))

    async def setup_hook(self) -> None:
        self.cognition.send_callback = self._send_callback
        self.cognition.mentor.send_callback = self._send_callback

    async def on_ready(self) -> None:
        log.info("Connected as %s (%s)", self.user, getattr(self.user, "id", "?"))
        channel_report = await self._channel_report()
        await self.send_named(
            "logs",
            f"Online as **{self.user}**. Auto-response: {'on' if AUTO_RESPONSE_ENABLED else 'off'}.\n\n{channel_report}",
        )
        await self.send_named("mind", self._status_report("startup"))
        if self._ambient_enabled and not self._heartbeat_task:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            log.info("Heartbeat loop started.")
        await self.cognition.store.update_posture(
            "boot_completed_at", __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user or message.author.bot:
            return
        content = (message.content or "").strip()
        if not content and not message.attachments:
            return
        is_dm = self._is_dm(message)
        if content.startswith("!"):
            if is_dm and not DM_COMMANDS_ENABLED:
                await message.channel.send("DM commands are not enabled.")
                return
            await self._handle_command(message, content)
            return
        if not AUTO_RESPONSE_ENABLED:
            return
        if is_dm:
            if not DM_CHAT_ENABLED:
                return
            async with self._process_lock:
                await self._process_human_message(message, channel_name="dm", source_type="dm")
            return
        if message.channel.id != CHAT_CHANNEL_ID:
            if not (self.user and self.user.mentioned_in(message)):
                return
            source_type = "mention"
        else:
            source_type = "primary_chat"

        async with self._process_lock:
            await self._process_human_message(message, source_type=source_type)

    def _is_dm(self, message: discord.Message) -> bool:
        return isinstance(message.channel, discord.DMChannel)

    async def _process_human_message(
        self,
        message: discord.Message,
        channel_name: Optional[str] = None,
        source_type: str = "primary_chat",
    ) -> None:
        self.world_clock.record_human_message()
        content = (message.content or "").strip()
        self._human_turn_count += 1
        self._last_activity = f"human turn #{self._human_turn_count}"
        channel_label = channel_name or getattr(message.channel, "name", "chat")
        location = "DM" if self._is_dm(message) else f"#{getattr(message.channel, 'name', message.channel.id)}"
        reply_context = await self._reply_context(message)
        message_context = self._message_context(
            message,
            channel_label=channel_label,
            source_type=source_type,
            reply_context=reply_context,
        )

        await self.send_named(
            "mind",
            f"Human turn #{self._human_turn_count} from `{message.author}` "
            f"in `{location}`.\n"
            f"Preview: {content[:300] or '[attachment-only]'}",
        )

        recent_context = await self._fetch_recent_context(message)

        previous_reaction_callback = self.cognition.reaction_callback
        self.cognition.reaction_callback = lambda emoji: self._react_to_message(message, emoji)
        try:
            result = await self.cognition.respond(
                user_text=content,
                author=str(message.author),
                human_id=str(message.author.id),
                channel_name=channel_label,
                recent_context=recent_context,
                message_context=message_context,
            )
        except Exception as exc:
            log.exception("Human message processing failed")
            await self.send_named("logs", f"Human message processing failed: `{exc}`")
            await message.channel.send(f"I hit an internal error before I could answer: `{exc}`")
            return
        finally:
            self.cognition.reaction_callback = previous_reaction_callback

        await self._send_result(message.channel, result)
        await self._stream_trace(result)
        if result.text:
            # Mentor reflection — async, after the reply is out. Never blocks.
            asyncio.create_task(
                self.cognition.mentor.reflect(content, result.text, str(message.author.id))
            )
        self._last_activity = f"human turn #{self._human_turn_count}: {'no reply' if not result.text else 'complete'}"

    def _message_context(
        self,
        message: discord.Message,
        channel_label: str,
        source_type: str,
        reply_context: Dict[str, object],
    ) -> Dict[str, object]:
        is_dm = self._is_dm(message)
        channel_name = getattr(message.channel, "name", channel_label)
        if is_dm:
            scope = "private_dm"
            channel_display = "DM"
        elif source_type == "primary_chat":
            scope = "primary_chat"
            channel_display = f"#{channel_name}"
        else:
            scope = "shared_mention"
            channel_display = f"#{channel_name}"
        guild = getattr(message, "guild", None)
        context: Dict[str, object] = {
            "source_type": "dm" if is_dm else source_type,
            "scope": scope,
            "channel_label": channel_display,
            "channel_name": channel_label,
            "channel_purpose": self._channel_purpose(message, source_type),
            "channel_id": str(getattr(message.channel, "id", "")),
            "guild_id": str(getattr(guild, "id", "")) if guild else "",
            "guild_label": str(getattr(guild, "name", "")) if guild else "",
            "message_id": str(getattr(message, "id", "")),
            "mention_only": (not is_dm and source_type == "mention"),
            "privacy_level": "private" if is_dm else "shared",
        }
        if reply_context:
            context["reply_to"] = reply_context
        return context

    def _channel_purpose(self, message: discord.Message, source_type: str) -> str:
        if self._is_dm(message):
            return "private human DM"
        channel_name = str(getattr(message.channel, "name", "") or "").lower()
        channel_id = int(getattr(message.channel, "id", 0) or 0)
        if channel_id == CHAT_CHANNEL_ID or channel_name == PRIMARY_CHAT_CHANNEL_NAME.lower():
            return "primary Ra chat"
        if channel_name == GENERAL_CHANNEL_NAME.lower():
            return "general shared chat / mention space"
        if channel_id == GAMES_CHANNEL_ID:
            return "Threshold Atlas game channel"
        if channel_name == GAMES_CHANNEL_NAME.lower():
            return "Threshold Atlas game channel"
        if channel_id == AMBIENT_CHANNEL_ID or channel_name == AMBIENT_CHANNEL_NAME.lower():
            return "Ra ambient activity channel"
        if channel_id == CREATES_CHANNEL_ID or channel_name == CREATES_CHANNEL_NAME.lower():
            return "Ra creations channel"
        if channel_id == MIND_CHANNEL_ID or channel_name == THOUGHTS_CHANNEL_NAME.lower():
            return "Ra thoughts / internal trace channel"
        if channel_id == LOGS_CHANNEL_ID or channel_name == LOGS_CHANNEL_NAME.lower():
            return "Ra logs channel"
        if channel_name == HABITAT_CHANNEL_NAME.lower():
            return "Ra habitat inspection channel"
        if channel_id == CURATOR_CHANNEL_ID or channel_name == CURATOR_CHANNEL_NAME.lower():
            return "curator/admin channel"
        return "non-primary shared/mention channel" if source_type == "mention" else "shared channel"

    async def _reply_context(self, message: discord.Message) -> Dict[str, object]:
        reference = getattr(message, "reference", None)
        if not reference:
            return {}
        resolved = getattr(reference, "resolved", None)
        if resolved is None and getattr(reference, "message_id", None):
            try:
                resolved = await message.channel.fetch_message(reference.message_id)
            except Exception as exc:
                return {
                    "present": True,
                    "message_id": str(reference.message_id),
                    "unavailable": True,
                    "error": str(exc)[:200],
                }
        if not isinstance(resolved, discord.Message):
            return {
                "present": True,
                "message_id": str(getattr(reference, "message_id", "")),
                "unavailable": True,
            }
        author = resolved.author
        return {
            "present": True,
            "message_id": str(resolved.id),
            "author": str(author),
            "author_id": str(getattr(author, "id", "")),
            "author_is_bot": bool(getattr(author, "bot", False)),
            "author_is_self": author == self.user,
            "channel_id": str(getattr(resolved.channel, "id", "")),
            "content": _context_excerpt(resolved.content or "", limit=1200),
        }

    async def _react_to_message(self, message: discord.Message, emoji: str) -> None:
        emoji = (emoji or "").strip()
        if not emoji:
            raise ValueError("emoji is required")
        reaction: object = emoji
        if emoji.startswith("<") and emoji.endswith(">"):
            reaction = discord.PartialEmoji.from_str(emoji)
        await message.add_reaction(reaction)

    async def _reflect_background(self, user_text: str, assistant_text: str, author: str) -> None:
        try:
            await self.cognition.reflect(user_text, assistant_text, author)
        except Exception as exc:
            log.exception("Reflection failed")
            await self.send_named("logs", f"Reflection failed: `{exc}`")

    async def _heartbeat_loop(self) -> None:
        await asyncio.sleep(15)
        _was_night = False
        _dusk_check_done = False
        _dawn_check_done = False
        _active_override = False  # True when the bot woke early; cleared at real day start

        while not self.is_closed():
            try:
                day_night = self.cognition.day_night

                # Clear early-wake override once real day arrives
                if _active_override and day_night and day_night.is_day:
                    _active_override = False

                is_night = (day_night and day_night.is_night) and not _active_override

                if is_night:
                    # Night entry — announce and run dusk check
                    if not _was_night:
                        _was_night = True
                        _dusk_check_done = False
                        _dawn_check_done = False
                        await self.send_named(
                            "mind",
                            f"Night begun ({day_night.describe()}). Dusk check running.",
                        )

                    if not _dusk_check_done:
                        _dusk_check_done = True
                        self._last_activity = "night — dusk check"
                        wants_up = await self._run_night_check("dusk")
                        if wants_up:
                            # One extra active cycle before sleeping
                            self._last_activity = "night — stayed up (dusk)"
                            await self._run_ambient()
                        self._last_activity = "night — resting"
                        await asyncio.sleep(NIGHT_SLEEP_INTERVAL)
                        continue

                    # Pre-dawn check — 1 hour before day_start
                    if not _dawn_check_done and day_night:
                        dawn_check_hour = (day_night.day_start - 1) % 24
                        if day_night.hour_utc == dawn_check_hour:
                            _dawn_check_done = True
                            self._last_activity = "night — pre-dawn check"
                            wants_up = await self._run_night_check("dawn")
                            if wants_up:
                                _active_override = True
                                _was_night = False
                                self._last_activity = "day — woke early"
                                await self.send_named("mind", "Woke early. Day resumed.")
                                # Fall through to day logic this iteration
                            else:
                                self._last_activity = "night — resting the final hour"
                                await asyncio.sleep(NIGHT_SLEEP_INTERVAL)
                                continue
                        else:
                            await asyncio.sleep(NIGHT_SLEEP_INTERVAL)
                            continue
                    else:
                        await asyncio.sleep(NIGHT_SLEEP_INTERVAL)
                        continue

                # Day logic
                if _was_night:
                    _was_night = False
                    _dusk_check_done = False
                    _dawn_check_done = False
                    desc = day_night.describe() if day_night else "morning"
                    await self.send_named("mind", f"Day begun ({desc}). Resuming.")

                await self._maybe_dream()
                await self._run_due_scheduled_tasks()

                state = self.heartbeat.tick()
                self._last_activity = f"heartbeat: {state}"
                if state == "IDLE":
                    await asyncio.sleep(self.heartbeat.sleep_for)
                    continue
                if self._process_lock.locked():
                    await asyncio.sleep(30)
                    continue
                if await self._run_due_notebook_initiation():
                    await asyncio.sleep(self.heartbeat.sleep_for)
                    continue
                await self._run_ambient()

            except Exception as exc:
                log.exception("Heartbeat loop error")
                await self.send_named("logs", f"Heartbeat error: `{exc}`")
            await asyncio.sleep(self.heartbeat.sleep_for)

    async def _maybe_dream(self) -> None:
        """Run the 3am consolidation once per day, under the process lock."""
        from datetime import datetime

        now = datetime.now()
        today = now.date().isoformat()
        if now.hour != DREAM_HOUR or self._last_dream_date == today:
            return
        self._last_dream_date = today
        self._last_activity = "dream cycle"
        async with self._process_lock:
            dream = DreamCycle(
                self.cognition.ambient_model_adapter,
                self.cognition.ambient_model,
                self.cognition.store.yin,
                self.cognition.store.logs,
                graph=self.cognition.graph,
            )
            paragraph = await dream.run()
            await self.send_named("mind", f"🌙 Dream cycle:\n{paragraph[:1500]}")

    async def _run_due_scheduled_tasks(self) -> None:
        """Run any due Yin-created tasks against the ambient prompt."""
        if self._process_lock.locked():
            return
        for task in self.cognition.scheduler.due_tasks():
            self._last_activity = f"scheduled task {task['id']}"
            async with self._process_lock:
                result = await self.cognition.run_scheduled_task(task["instruction"])
                self.cognition.scheduler.mark_ran(
                    task["id"], (result.text or "")[:400]
                )
                if result.text:
                    await self.send_named("ambient", result.text[:1800], result.files or None)
                await self._stream_trace(result)

    async def _run_ambient(self) -> None:
        """Run one ambient cycle under the process lock and stream the trace."""
        async with self._process_lock:
            result = await self.cognition.ambient_cycle(cycle=self.heartbeat.tick_count)
            self.world_clock.record_ambient_result(
                result.text[:500] if result.text else "(empty)",
                [e.get("tool", "") for e in result.tool_log],
            )
            await self._stream_trace(result)

    async def _run_due_notebook_initiation(self) -> bool:
        if not (DM_INITIATIONS_ENABLED and DM_NOTEBOOK_REMINDERS):
            return False
        if self._process_lock.locked():
            return False
        raw = await self.cognition.store.due_notebook_items(
            bot_id=self.cognition.instance_name,
            limit=5,
        )
        try:
            due_items = json.loads(raw)
        except json.JSONDecodeError:
            await self.send_named("logs", f"Notebook due check failed: `{raw[:500]}`")
            return False
        for item in due_items:
            if await self._attempt_notebook_dm(item):
                return True
        return False

    async def _attempt_notebook_dm(self, item: Dict[str, object]) -> bool:
        human_id = str(item.get("human_id") or "")
        notebook_id = str(item.get("id") or "")
        entry_type = str(item.get("entry_type") or "note")
        if entry_type not in {"reminder", "task", "calendar", "date", "event"}:
            return False
        recent_raw = await self.cognition.store.recent_initiation_attempts(
            bot_id=self.cognition.instance_name,
            human_id=human_id,
            limit=5,
        )
        try:
            recent = json.loads(recent_raw)
        except json.JSONDecodeError:
            recent = []
        if self._recent_initiation_within_cooldown(recent):
            await self.cognition.store.log_initiation_attempt(
                bot_id=self.cognition.instance_name,
                human_id=human_id,
                target_table="human_notebook",
                target_id=notebook_id,
                initiation_type="notebook_due",
                channel_type="none",
                status="skipped",
                reason=f"Cooldown active ({INITIATION_COOLDOWN_MINUTES} minutes).",
                metadata={"entry_type": entry_type},
            )
            return False
        message = self._format_notebook_due_message(item)
        try:
            user = await self.fetch_user(int(human_id))
            await user.send(message)
        except Exception as exc:
            await self.cognition.store.log_initiation_attempt(
                bot_id=self.cognition.instance_name,
                human_id=human_id,
                target_table="human_notebook",
                target_id=notebook_id,
                initiation_type="notebook_due",
                channel_type="dm",
                status="failed",
                reason="Due notebook reminder could not be sent by DM.",
                message_preview=message,
                error=str(exc),
                metadata={"entry_type": entry_type},
            )
            await self.send_named("logs", f"Notebook DM reminder failed for `{human_id}`: `{exc}`")
            return False
        await self.cognition.store.log_initiation_attempt(
            bot_id=self.cognition.instance_name,
            human_id=human_id,
            target_table="human_notebook",
            target_id=notebook_id,
            initiation_type="notebook_due",
            channel_type="dm",
            status="sent",
            reason="Due notebook item explicitly belonged to reminder/task/calendar/date/event flow.",
            message_preview=message,
            metadata={"entry_type": entry_type},
        )
        if not str(item.get("recurrence") or "").strip():
            await self.cognition.store.complete_notebook_item(
                bot_id=self.cognition.instance_name,
                notebook_id=notebook_id,
                reason="dm_reminder_sent",
            )
        return True

    def _format_notebook_due_message(self, item: Dict[str, object]) -> str:
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        due = str(item.get("due_at") or "")[:19]
        heading = title or "Notebook reminder"
        lines = [heading]
        if due:
            lines.append(f"Due: {due}")
        if content and content != title:
            lines.append(content)
        return "\n".join(lines)[:1800]

    def _recent_initiation_within_cooldown(self, rows: List[Dict[str, object]]) -> bool:
        if INITIATION_COOLDOWN_MINUTES <= 0:
            return False
        try:
            from datetime import datetime, timezone, timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(minutes=INITIATION_COOLDOWN_MINUTES)
            for row in rows:
                if row.get("status") not in {"sent", "failed"}:
                    continue
                created = str(row.get("created_at") or "")
                if not created:
                    continue
                when = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if when >= cutoff:
                    return True
        except Exception:
            return False
        return False

    async def _run_night_check(self, phase: str) -> bool:
        """Run a dusk/dawn sovereignty check. Returns True if the bot wants to be active."""
        if self._process_lock.locked():
            return False
        try:
            async with self._process_lock:
                return await self.cognition.night_check(phase=phase)
        except Exception as exc:
            log.exception("Night check failed (%s)", phase)
            await self.send_named("logs", f"Night check ({phase}) failed: `{exc}`")
            return False

    async def _handle_command(self, message: discord.Message, content: str) -> None:
        cmd = content.strip()
        cmd_lower = cmd.lower()
        if self._is_dm(message) and not self._dm_command_allowed(cmd_lower):
            await message.channel.send(
                "That command is only available in the server/curator channels. "
                "In DM you can use `!status`, `!memory human`, `!memory notebook`, `!memory admissions`, and `!trace last`."
            )
            return
        if cmd_lower == "!status":
            await self._send_command_reply(message, self._status_report("status"))
            return
        if cmd_lower == "!debug":
            await self._send_command_reply(message, self._status_report("debug") + "\n\n" + await self._channel_report())
            return
        if cmd_lower.startswith("!trace"):
            await self._send_command_reply(message, await self._trace_report(cmd))
            return
        if cmd_lower.startswith("!habitat"):
            await self._send_command_reply(message, await self._habitat_report(cmd))
            return
        if cmd_lower.startswith("!game"):
            await self._send_command_reply(message, await self._game_report(cmd))
            return
        if cmd_lower.startswith("!notebook"):
            await self._send_command_reply(message, await self._notebook_report(cmd, str(message.author.id), self._is_dm(message)))
            return
        if cmd_lower.startswith("!initiation"):
            await self._send_command_reply(message, await self._initiation_report(cmd, str(message.author.id), self._is_dm(message)))
            return
        if cmd_lower.startswith("!memory"):
            await self._send_command_reply(
                message,
                await self._memory_report(cmd, str(message.author.id), message.channel.id),
            )
            return
        if cmd_lower in {"!sleep", "!pauseambient"}:
            self._ambient_enabled = False
            self.heartbeat.paused = True
            await message.channel.send("Ambient paused.")
            return
        if cmd_lower in {"!wake", "!resumeambient"}:
            self._ambient_enabled = True
            self.heartbeat.paused = False
            await message.channel.send("Ambient resumed.")
            return
        if cmd_lower == "!consolidate":
            if self._process_lock.locked():
                await message.channel.send("Already thinking; try again shortly.")
                return
            async with self._process_lock:
                result = await self.cognition.ambient_cycle()
                await self._stream_trace(result)
                await message.channel.send(result.text.strip() or "(no result)")
            return
        if cmd_lower == "!defer":
            pending = await self.cognition.store.pending_deferred(limit=5)
            if not pending:
                await message.channel.send("No deferred responses pending.")
                return
            for item in pending:
                try:
                    result = await self.cognition.respond(
                        user_text=item["incoming_text"],
                        author=item["author"],
                        human_id=str(item.get("human_id", "")),
                        channel_name=item["channel"],
                    )
                    await self.send_named("chat", result.text[:1800])
                    await self.cognition.store.mark_answered(str(item["id"]), result.text)
                except Exception as exc:
                    log.error("Deferred response failed: %s", exc)
            return

    async def _send_command_reply(self, message: discord.Message, content: str) -> None:
        if self._is_dm(message):
            await message.channel.send(content)
            return
        if CURATOR_CHANNEL_ID and message.channel.id != CURATOR_CHANNEL_ID:
            sent = await self.send_named("curator", content)
            if sent:
                return
        await message.channel.send(content)

    def _dm_command_allowed(self, cmd_lower: str) -> bool:
        if cmd_lower == "!status":
            return True
        if cmd_lower in {"!trace", "!trace last"}:
            return True
        if cmd_lower.startswith("!memory"):
            parts = cmd_lower.split()
            layer = parts[1] if len(parts) > 1 else "help"
            return layer in {"help", "?", "human", "notebook", "admissions", "audit"}
        if cmd_lower.startswith("!notebook"):
            return cmd_lower.split()[1:2] in ([], ["due"])
        if cmd_lower.startswith("!initiation"):
            return cmd_lower.split()[1:2] in ([], ["audit"], ["recent"])
        return False

    async def _fetch_recent_context(self, current: discord.Message) -> str:
        lines: List[str] = []
        try:
            fetch_limit = max(RECENT_CONTEXT_LIMIT, min(50, RECENT_CONTEXT_LIMIT * 3))
            async for msg in current.channel.history(limit=fetch_limit, before=current):
                if msg.author.bot and msg.author != self.user:
                    continue
                text = (msg.content or "").strip()
                if not text:
                    continue
                if text.startswith("!"):
                    continue
                if is_no_reply_marker(text) or is_acknowledgement_only(text):
                    continue
                if msg.author == self.user and _looks_like_diagnostic_response(text):
                    continue
                name = msg.author.display_name
                prefix = "DM " if self._is_dm(msg) else ""
                lines.append(f"[{prefix}{name}]: {_context_excerpt(text)}")
                if len(lines) >= RECENT_CONTEXT_LIMIT:
                    break
        except Exception as exc:
            log.warning("Could not fetch message history: %s", exc)
        lines.reverse()
        return "\n".join(lines)

    async def _send_result(self, channel: discord.abc.Messageable, result: CognitionResult) -> None:
        if not result.text and not result.files:
            return
        text = result.text.strip() if result.text else "(no response)"
        send_files = [p for p in result.files if p.exists() and p.stat().st_size < 8_000_000]
        for chunk in _chunks(text):
            try:
                if send_files:
                    await channel.send(chunk, files=[discord.File(str(p), filename=p.name) for p in send_files[:10]])
                    send_files = []
                else:
                    await channel.send(chunk)
            except Exception as exc:
                log.exception("Channel send failed")
                await self.send_named("logs", f"Channel send failed: `{exc}`")
                return
        if send_files:
            try:
                await channel.send(files=[discord.File(str(p), filename=p.name) for p in send_files[:10]])
            except Exception as exc:
                log.exception("File send failed")
                await self.send_named("logs", f"File send failed: `{exc}`")

    async def _stream_trace(self, result: CognitionResult) -> None:
        if result.tool_log:
            lines = ["Tools:"]
            for entry in result.tool_log[-12:]:
                lines.append(f"- {entry['tool']} → {str(entry.get('result', ''))[:250]}")
            await self.send_named("mind", "\n".join(lines))

    async def _send_callback(self, channel_name: str, message: str, files: Optional[List[Path]] = None) -> None:
        await self.send_named(channel_name, message, files)

    async def send_named(self, channel_name: str, message: str, files: Optional[List[Path]] = None) -> bool:
        channel_id = self.channel_ids.get(channel_name.strip().lower(), 0)
        if not channel_id:
            return False
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception as exc:
                log.warning("Could not fetch channel %s: %s", channel_name, exc)
                return False
        file_paths = [p for p in (files or []) if p.exists() and p.stat().st_size < 8_000_000]
        try:
            for chunk in _chunks(message):
                if file_paths:
                    await channel.send(chunk, files=[discord.File(str(p), filename=p.name) for p in file_paths[:10]])
                    file_paths = []
                else:
                    await channel.send(chunk)
            if file_paths:
                await channel.send(files=[discord.File(str(p), filename=p.name) for p in file_paths[:10]])
        except Exception as exc:
            log.warning("send_named failed for %s: %s", channel_name, exc)
            return False
        return True

    async def _channel_report(self) -> str:
        lines = ["Channel map:"]
        for name, channel_id in self.channel_ids.items():
            if not channel_id:
                lines.append(f"- {name}: not configured")
                continue
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except Exception as exc:
                    lines.append(f"- {name}: {channel_id} fetch failed: {exc}")
                    continue
            lines.append(f"- {name}: #{getattr(channel, 'name', channel_id)} ({channel_id})")
        return "\n".join(lines)

    def _status_report(self, label: str) -> str:
        return "\n".join([
            f"**Yin status** `{label}`",
            f"Bot: `{self.user}`",
            f"Auto-response: `{'on' if AUTO_RESPONSE_ENABLED else 'off'}`",
            f"DM chat: `{'on' if DM_CHAT_ENABLED else 'off'}`",
            f"Ambient: `{'on' if self._ambient_enabled else 'off'}`",
            f"Day/night: `{'on' if DAY_NIGHT_ENABLED else 'off'}`",
            "Memory: `local (SQLite + JSON lanes)`",
            f"Last activity: `{self._last_activity}`",
            f"Human turns: `{self._human_turn_count}`",
            self.world_clock.render(),
        ])

    async def _trace_report(self, cmd: str = "!trace") -> str:
        parts = cmd.split()
        subcommand = parts[1] if len(parts) > 1 else "recent"
        if subcommand == "compare":
            days = _safe_int(parts[2], 7) if len(parts) > 2 else 7
            return await self._trace_compare_report(days=days)
        if subcommand in {"stats", "aggregate"}:
            days = _safe_int(parts[2], 7) if len(parts) > 2 else 7
            return await self._trace_stats_report(days=days)

        full = subcommand == "full"
        limit = 1 if subcommand in {"last", "full"} else 3
        if subcommand.isdigit():
            limit = max(1, min(int(subcommand), 5))
        raw = await self.cognition.store.recent_interaction_traces(limit=limit)
        try:
            traces = json.loads(raw)
        except json.JSONDecodeError:
            return f"Trace unavailable: `{raw[:1500]}`"
        if not traces:
            return "No interaction traces recorded yet."

        lines = ["**Recent routing traces**"]
        for trace in traces:
            lines.extend(self._format_trace_card(trace, full=full))
        return "\n".join(lines)[:1900]

    async def _trace_compare_report(self, days: int = 7) -> str:
        raw = await self.cognition.store.trace_compare(days=days)
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return f"Trace comparison unavailable: `{raw[:1500]}`"
        if not rows:
            return f"No trace data in the last {days} day(s)."
        lines = [f"**Trace comparison** `{days}d`"]
        for row in rows[:10]:
            lines.append(
                f"`{row.get('bot_id') or '?'}`: "
                f"traces `{row.get('trace_count', 0)}`, "
                f"humans `{row.get('human_count', 0)}`, "
                f"roles `{row.get('role_count', 0)}`, "
                f"influences `{row.get('influence_count', 0)}`, "
                f"avg pressure `{float(row.get('avg_pressure') or 0):.2f}`, "
                f"identity writes `{row.get('identity_write_allowed_count', 0)}`, "
                f"top mode `{row.get('top_mode') or '?'}:{row.get('top_mode_count') or 0}`"
            )
        return "\n".join(lines)[:1900]

    async def _habitat_report(self, cmd: str = "!habitat") -> str:
        parts = cmd.split()
        subcommand = parts[1].lower() if len(parts) > 1 else ""
        area = parts[1].lower() if len(parts) > 1 and not parts[1].isdigit() else ""
        numeric_parts = [part for part in parts[1:] if part.isdigit()]
        limit = _safe_int(numeric_parts[0], 8) if numeric_parts else 8
        if subcommand in {"audit", "decisions", "residue"}:
            raw = await self.cognition.store.recent_habitat_residue_decisions(
                bot_id=self.cognition.instance_name,
                limit=limit,
            )
            try:
                rows = json.loads(raw)
            except json.JSONDecodeError:
                return f"Habitat audit unavailable: `{raw[:1500]}`"
            if not rows:
                return "No habitat residue classifier decisions recorded yet."
            lines = ["**Habitat residue audit**"]
            for row in rows[:limit]:
                row_id = str(row.get("id", ""))[:8]
                tool = row.get("tool_name") or "?"
                phase = row.get("phase") or "?"
                reason = str(row.get("reason") or "").replace("`", "'")[:280]
                if row.get("has_residue"):
                    target = f"{row.get('area') or '?'}/{row.get('entry_type') or '?'}"
                    confidence = row.get("confidence")
                    conf_text = f" conf `{float(confidence):.2f}`" if confidence is not None else ""
                    lines.append(f"`{row_id}` `{tool}`/{phase}: {target}{conf_text} - {reason}")
                else:
                    lines.append(f"`{row_id}` `{tool}`/{phase}: none - {reason}")
            return "\n".join(lines)[:1900]
        if subcommand in {"recent-events", "events"}:
            area = ""
        raw = await self.cognition.store.habitat_snapshot(
            bot_id=self.cognition.instance_name,
            area=area,
            event_limit=limit,
        )
        try:
            habitat = json.loads(raw)
        except json.JSONDecodeError:
            return f"Habitat unavailable: `{raw[:1500]}`"
        areas = habitat.get("areas") or []
        entries = habitat.get("entries") or []
        events = habitat.get("recent_events") or []
        if not areas and not entries and not events:
            return "No habitat records found."
        lines = ["**Habitat**"]
        for row in areas[:8]:
            state = row.get("state") or {}
            if isinstance(state, str):
                try:
                    state = json.loads(state)
                except json.JSONDecodeError:
                    state = {"note": state}
            state_text = "open"
            if isinstance(state, dict) and state:
                state_text = "; ".join(f"{k}={str(v)[:90]}" for k, v in list(state.items())[:4])
            lines.append(f"`{row.get('area') or '?'}` {state_text}")
        if entries:
            lines.append("")
            lines.append("Placed entries")
            for entry in entries[:limit]:
                entry_id = str(entry.get("id", ""))[:8]
                content = str(entry.get("content") or "").replace("`", "'")[:220]
                suffix = f" - {content}" if content else ""
                actions = entry.get("suggested_actions") or []
                action_text = f" actions `{', '.join(actions[:4])}`" if actions else ""
                lines.append(
                    f"`{entry_id}` [{entry.get('area') or '?'}/{entry.get('entry_type') or '?'}/"
                    f"{entry.get('status') or '?'}]{action_text} {entry.get('title') or '?'}{suffix}"
                )
        if events:
            lines.append("")
            lines.append("Recent events")
            for event in events[:limit]:
                event_id = str(event.get("id", ""))[:8]
                content = str(event.get("content") or "").replace("`", "'")[:260]
                suffix = f" - {content}" if content else ""
                lines.append(f"`{event_id}` [{event.get('area') or '?'}/{event.get('action') or '?'}]{suffix}")
        return "\n".join(lines)[:1900]

    async def _game_report(self, cmd: str = "!game") -> str:
        parts = cmd.split(maxsplit=2)
        if len(parts) == 1 or (len(parts) > 1 and parts[1].lower() in {"status", "state"}):
            return "**Threshold Atlas**\n" + self.cognition.game_status_text()
        action = parts[1].lower()
        detail = parts[2] if len(parts) > 2 else ""
        result = await self.cognition.game_act(action=action, detail=detail, phase="command")
        return "**Threshold Atlas**\n" + result[:1800]

    async def _notebook_report(self, cmd: str, author: str, is_dm: bool = False) -> str:
        parts = cmd.split()
        subcommand = parts[1].lower() if len(parts) > 1 else "due"
        numeric_parts = [part for part in parts[1:] if part.isdigit()]
        limit = _safe_int(numeric_parts[0], 8) if numeric_parts else 8
        if subcommand != "due":
            return "Notebook commands: `!notebook due [n]`"
        raw = await self.cognition.store.due_notebook_items(
            bot_id=self.cognition.instance_name,
            limit=limit,
        )
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return f"Notebook due unavailable: `{raw[:1500]}`"
        if is_dm:
            rows = [row for row in rows if str(row.get("human_id") or "") == author]
        if not rows:
            return "No due notebook items."
        lines = ["**Due notebook items**"]
        for row in rows[:limit]:
            row_id = str(row.get("id", ""))[:8]
            title = f"{row.get('title')}: " if row.get("title") else ""
            content = str(row.get("content") or "").replace("`", "'")[:300]
            due = str(row.get("due_at") or "")[:19]
            human = str(row.get("human_id") or "")[:22]
            lines.append(f"`{row_id}` [{row.get('entry_type') or '?'}/due `{due}`/human `{human}`] {title}{content}")
        return "\n".join(lines)[:1900]

    async def _initiation_report(self, cmd: str, author: str, is_dm: bool = False) -> str:
        parts = cmd.split()
        subcommand = parts[1].lower() if len(parts) > 1 else "audit"
        numeric_parts = [part for part in parts[1:] if part.isdigit()]
        limit = _safe_int(numeric_parts[0], 8) if numeric_parts else 8
        if subcommand not in {"audit", "recent"}:
            return "Initiation commands: `!initiation audit [n]`"
        raw = await self.cognition.store.recent_initiation_attempts(
            bot_id=self.cognition.instance_name,
            human_id=author if is_dm else "",
            limit=limit,
        )
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return f"Initiation audit unavailable: `{raw[:1500]}`"
        if not rows:
            return "No initiation attempts recorded."
        lines = ["**Initiation audit**"]
        for row in rows[:limit]:
            row_id = str(row.get("id", ""))[:8]
            target = f"{row.get('target_table') or '?'}:{str(row.get('target_id') or '')[:8]}"
            reason = str(row.get("reason") or "").replace("`", "'")[:280]
            error = f" error `{str(row.get('error'))[:160]}`" if row.get("error") else ""
            lines.append(
                f"`{row_id}` [{row.get('initiation_type') or '?'}/{row.get('channel_type') or '?'}/"
                f"{row.get('status') or '?'}] {target} - {reason}{error}"
            )
        return "\n".join(lines)[:1900]

    async def _memory_report(self, cmd: str, author: str, channel_id: int = 0) -> str:
        parts = cmd.split()
        parts_lower = [part.lower() for part in parts]
        layer = parts_lower[1] if len(parts_lower) > 1 else "help"
        include_terminal = any(part in {"all", "audit", "terminal"} for part in parts_lower[2:])
        numeric_parts = [part for part in parts_lower[2:] if part.isdigit()]
        limit = _safe_int(numeric_parts[0], 6) if numeric_parts else 6
        limit = max(1, min(limit, 12))

        if layer in {"help", "?"}:
            return "\n".join([
                "**Memory commands**",
                "`!memory human [n]` - current human memory",
                "`!memory notebook [n]` - current human notebook",
                "`!memory self [n]` - bot self-memory candidates/stable rows",
                "`!memory self all [n]` - include rejected/archived/discarded self-memory audit rows",
                "`!memory identity [n]` - protected identity threads",
                "`!memory admissions [n]` - recent human-memory admission reasons",
                "`!memory reviews [n]` - bot-self review candidates",
                "`!memory contexts` - neutral memory grouping labels",
                "`!memory archive human|notebook|self <id> [reason]` - curator-only soft archive",
                "`!memory delete human|notebook <id> [reason]` - curator-only soft delete",
                "`!memory reject self <id> [reason]` - curator-only reject a self-memory candidate",
                "`!memory curator [n]` - recent curator memory actions",
            ])

        if layer in {"archive", "delete", "reject", "restore", "complete"}:
            return await self._memory_curator_action(parts, parts_lower, author, channel_id)

        if layer == "identity":
            raw = await self.cognition.store.identity_threads(limit=limit)
            title = "Protected identity threads"
            formatter = self._format_identity_rows
        elif layer == "reviews":
            raw = await self.cognition.store.review_candidates(
                bot_id=self.cognition.instance_name,
                limit=limit,
                include_identity=True,
            )
            title = "Memory review candidates"
            formatter = self._format_memory_rows
        elif layer == "contexts":
            raw = await self.cognition.store.list_memory_contexts(
                bot_id=self.cognition.instance_name,
                include_archived=True,
            )
            title = "Memory contexts"
            formatter = self._format_context_rows
        elif layer in {"admissions", "audit"}:
            raw = await self.cognition.store.recent_memory_admissions(
                bot_id=self.cognition.instance_name,
                human_id=author,
                limit=limit,
            )
            title = "Memory admissions"
            formatter = self._format_admission_rows
        elif layer == "curator":
            raw = await self.cognition.store.recent_curator_actions(
                bot_id=self.cognition.instance_name,
                limit=limit,
            )
            title = "Memory curator actions"
            formatter = self._format_curator_rows
        else:
            layer_map = {
                "human": "human_memory",
                "notebook": "human_notebook",
                "self": "bot_self_memory",
            }
            db_layer = layer_map.get(layer)
            if not db_layer:
                return "Unknown memory layer. Try `!memory help`."
            raw = await self.cognition.store.recent_layered_memory(
                layer=db_layer,
                bot_id=self.cognition.instance_name,
                human_id=author if db_layer != "bot_self_memory" else "",
                limit=limit,
                include_terminal=include_terminal,
            )
            title = {
                "human_memory": "Human memory",
                "human_notebook": "Human notebook",
                "bot_self_memory": "Bot self-memory",
            }[db_layer]
            formatter = self._format_memory_rows

        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return f"Memory unavailable: `{raw[:1500]}`"
        if not rows:
            return f"No records found for `{title}`."
        return formatter(title, rows)[:1900]

    async def _memory_curator_action(
        self,
        parts: List[str],
        parts_lower: List[str],
        author: str,
        channel_id: int,
    ) -> str:
        if not CURATOR_CHANNEL_ID or channel_id != CURATOR_CHANNEL_ID:
            return "Curator memory changes must be run in the curator channel."
        if len(parts) < 4:
            return "Usage: `!memory archive human|notebook|self <id> [reason]`"

        action = parts_lower[1]
        layer = parts_lower[2]
        target_id = parts[3]
        reason = " ".join(parts[4:]).strip() or f"Curator {action} via Discord command."
        bot_id = self.cognition.instance_name

        if layer == "self":
            decision_map = {
                "archive": "archive",
                "reject": "reject",
                "restore": "",
                "complete": "",
            }
            decision = decision_map.get(action, "")
            if not decision:
                return "Self-memory curator actions support `archive` or `reject`."
            resolved = await self.cognition.store.resolve_memory_id("bot_self_memory", bot_id, target_id)
            if resolved.startswith("error:"):
                return resolved
            review_id = await self.cognition.store.decide_memory_review(
                bot_id=bot_id,
                candidate_id=resolved,
                decision=decision,
                reason=reason,
                reviewed_by=f"curator:{author}",
            )
            if review_id.startswith("error:") or review_id.startswith("[no-postgres]"):
                return review_id
            await self.cognition.store.record_curator_action(
                bot_id=bot_id,
                target_table="bot_self_memory",
                target_id=resolved,
                action=decision,
                reason=reason,
                curator_id=author,
            )
            return f"Curator `{decision}` applied to self-memory `{resolved[:8]}`. Review `{review_id[:8]}` recorded."

        table_map = {
            "human": "human_memory",
            "notebook": "human_notebook",
        }
        table = table_map.get(layer)
        if not table:
            return "Curator actions support `human`, `notebook`, or `self`."

        status_map = {
            "archive": "archived",
            "delete": "deleted",
            "restore": "active",
            "complete": "completed",
        }
        status = status_map.get(action)
        if not status or action == "reject":
            return "Human/notebook curator actions support `archive`, `delete`, `restore`, or notebook `complete`."
        if table == "human_memory" and status == "completed":
            return "`complete` only applies to notebook rows."

        result = await self.cognition.store.update_curated_memory_status(
            bot_id=bot_id,
            target_table=table,
            target_id=target_id,
            status=status,
            reason=reason,
            curator_id=author,
        )
        if result.startswith("error:") or result.startswith("[no-postgres]"):
            return result
        return f"Curator `{action}` set `{layer}` memory `{result[:8]}` to `{status}`."

    def _format_memory_rows(self, title: str, rows: List[Dict[str, object]]) -> str:
        lines = [f"**{title}**"]
        for row in rows:
            row_id = str(row.get("id", ""))[:8]
            kind = row.get("memory_type") or row.get("entry_type") or "?"
            status = row.get("promotion_status") or row.get("status") or row.get("consent_status") or "?"
            content = str(row.get("content") or "").replace("`", "'")[:360]
            due = f" due `{str(row.get('due_at'))[:19]}`" if row.get("due_at") else ""
            title_text = f"{row.get('title')}: " if row.get("title") else ""
            lines.append(f"`{row_id}` [{kind}/{status}]{due} {title_text}{content}")
        return "\n".join(lines)

    def _format_identity_rows(self, title: str, rows: List[Dict[str, object]]) -> str:
        lines = [f"**{title}**"]
        for row in rows:
            row_id = str(row.get("id", ""))[:8]
            kind = row.get("type") or "?"
            status = row.get("status") or "?"
            confidence = float(row.get("confidence") or 0)
            content = str(row.get("content") or "").replace("`", "'")[:420]
            lines.append(f"`{row_id}` [{kind}/{status}] confidence `{confidence:.2f}` {content}")
        return "\n".join(lines)

    def _format_context_rows(self, title: str, rows: List[Dict[str, object]]) -> str:
        lines = [f"**{title}**"]
        for row in rows:
            row_id = str(row.get("id", ""))[:8]
            key = row.get("key") or "?"
            status = row.get("status") or "?"
            context_title = row.get("title") or key
            summary = str(row.get("summary") or "").replace("`", "'")[:300]
            lines.append(f"`{row_id}` [{key}/{status}] {context_title}: {summary}")
        return "\n".join(lines)

    def _format_admission_rows(self, title: str, rows: List[Dict[str, object]]) -> str:
        lines = [f"**{title}**"]
        for row in rows:
            row_id = str(row.get("id", ""))[:8]
            target = f"{row.get('target_table') or '?'}:{str(row.get('target_id') or '')[:8]}"
            category = row.get("admission_category") or "?"
            reason = str(row.get("admission_reason") or "").replace("`", "'")[:420]
            lines.append(f"`{row_id}` [{category}] {target} - {reason}")
        return "\n".join(lines)

    def _format_curator_rows(self, title: str, rows: List[Dict[str, object]]) -> str:
        lines = [f"**{title}**"]
        for row in rows:
            row_id = str(row.get("id", ""))[:8]
            target = f"{row.get('target_table') or '?'}:{str(row.get('target_id') or '')[:8]}"
            action = row.get("action") or "?"
            curator = str(row.get("curator_id") or "?")[:22]
            reason = str(row.get("reason") or "").replace("`", "'")[:360]
            lines.append(f"`{row_id}` [{action}] {target} by `{curator}` - {reason}")
        return "\n".join(lines)

    def _format_trace_card(self, trace: Dict[str, object], full: bool = False) -> List[str]:
        lines: List[str] = [""]
        lines.append(f"Trace `{str(trace.get('id', ''))[:8]}` | mode `{trace.get('selected_mode', '?')}`")
        lines.append(f"Bot: `{trace.get('bot_id') or '?'}` | Human: `{trace.get('human_id') or '?'}` | Channel: `{trace.get('channel') or '?'}`")
        preview = str(trace.get("incoming_preview") or "").replace("`", "'")[:500 if full else 220]
        if preview:
            lines.append(f"Preview: {preview}")
        interaction = trace.get("weather_snapshot") or {}
        coherence = trace.get("coherence_snapshot") or {}
        if not isinstance(interaction, dict):
            interaction = {}
        if not isinstance(coherence, dict):
            coherence = {}
        lines.append(
            "Interaction: "
            f"clarity `{interaction.get('clarity', interaction.get('visibility', '?'))}`, "
            f"pressure `{interaction.get('pressure', '?')}`, "
            f"boundary load `{interaction.get('boundary_load', interaction.get('storm_risk', '?'))}`"
        )
        lines.append(
            "Coherence: "
            f"roles `{coherence.get('role_invitation_count', 0)}`, "
            f"influences `{coherence.get('influence_count', 0)}`, "
            f"identity writes `{coherence.get('identity_write_allowed', False)}`, "
            f"pressure `{coherence.get('pressure', '?')}`"
        )
        roles = trace.get("role_invitations") or []
        if isinstance(roles, list) and roles:
            role_bits = [
                f"{r.get('proposed_role', '?')} -> {r.get('action', '?')}; identity={r.get('identity_write_allowed', False)}"
                for r in roles[:8 if full else 4]
                if isinstance(r, dict)
            ]
            if role_bits:
                lines.append("Roles: " + "; ".join(role_bits))
        influences = trace.get("influences") or []
        if isinstance(influences, list) and influences:
            influence_bits = [
                f"{i.get('type', '?')}->{i.get('target_layer', '?')}; mem={i.get('memory_write_allowed', False)}; id={i.get('identity_write_allowed', False)}"
                for i in influences[:8 if full else 5]
                if isinstance(i, dict)
            ]
            if influence_bits:
                lines.append("Influences: " + "; ".join(influence_bits))
        reason = str(trace.get("reasoning_summary") or "")[:500 if full else 220]
        if reason:
            lines.append(f"Reason: {reason}")
        return lines

    async def _trace_stats_report(self, days: int = 7) -> str:
        raw = await self.cognition.store.trace_aggregate(days=days)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return f"Trace stats unavailable: `{raw[:1500]}`"
        summary = data.get("summary") or {}
        lines = [f"**Trace aggregate** `{days}d`"]
        lines.append(
            f"Traces: `{summary.get('trace_count', 0)}` | "
            f"Humans: `{summary.get('human_count', 0)}` | "
            f"Avg pressure: `{float(summary.get('avg_pressure') or 0):.2f}` | "
            f"Identity writes allowed: `{summary.get('identity_write_allowed_count', 0)}`"
        )
        modes = data.get("modes") or []
        if modes:
            lines.append("Modes: " + "; ".join(f"{m.get('selected_mode') or '?'}={m.get('count', 0)}" for m in modes[:8]))
        roles = data.get("roles") or []
        if roles:
            lines.append("Roles: " + "; ".join(
                f"{r.get('proposed_role', '?')}->{r.get('action', '?')}={r.get('count', 0)}"
                for r in roles[:8]
            ))
        influences = data.get("influences") or []
        if influences:
            lines.append("Influences: " + "; ".join(
                f"{i.get('influence_type', '?')}->{i.get('target_layer', '?')}={i.get('count', 0)}"
                for i in influences[:8]
            ))
        bots = data.get("bots") or []
        if len(bots) > 1:
            lines.append("Bots: " + "; ".join(f"{b.get('bot_id', '?')}={b.get('trace_count', 0)}" for b in bots[:8]))
        return "\n".join(lines)[:1900]

    async def run_bot(self) -> None:
        if not self.token:
            raise ValueError("DISCORD_TOKEN is not set.")
        await self.start(self.token)
