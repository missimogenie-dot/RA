from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Europe/London")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_age(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    if not dt:
        return "never"
    now = now or utc_now()
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h {minutes % 60}m ago"
    days = hours // 24
    return f"{days}d {hours % 24}h ago"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


@dataclass(frozen=True)
class WorldClockSnapshot:
    local_time: str
    runtime_age: str
    last_human_message: str
    last_ambient_action: str
    last_ambient_age: str
    last_research: str


class WorldClock:
    """Persisted runtime clock for status and consolidation context."""

    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "world_clock.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.started_at = utc_now()
        self.last_human_at: Optional[datetime] = None
        self.last_ambient_at: Optional[datetime] = None
        self.last_ambient_action = "none"
        self.last_research_at: Optional[datetime] = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.started_at = _parse_dt(data.get("started_at")) or self.started_at
        self.last_human_at = _parse_dt(data.get("last_human_at"))
        self.last_ambient_at = _parse_dt(data.get("last_ambient_at"))
        self.last_ambient_action = str(data.get("last_ambient_action") or "none")
        self.last_research_at = _parse_dt(data.get("last_research_at"))

    def _save(self) -> None:
        data = {
            "started_at": _iso(self.started_at),
            "last_human_at": _iso(self.last_human_at),
            "last_ambient_at": _iso(self.last_ambient_at),
            "last_ambient_action": self.last_ambient_action,
            "last_research_at": _iso(self.last_research_at),
        }
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def record_human_message(self) -> None:
        self.last_human_at = utc_now()
        self._save()

    def record_ambient_result(self, text: str, tool_names: Iterable[str]) -> None:
        now = utc_now()
        tools = {t for t in tool_names if t}
        self.last_ambient_at = now
        if "web_search" in tools:
            self.last_research_at = now
        self.last_ambient_action = self._action_label(text, tools)
        self._save()

    def _action_label(self, text: str, tools: set[str]) -> str:
        if not tools and (text or "").lower().strip() in {"sleep", "sleep.", "quiet", "quiet for now"}:
            return "sleep"
        if "memory_interpret" in tools or "working_memory_add" in tools:
            return "consolidation"
        if "kg_search" in tools or "kg_add_fact" in tools:
            return "graph_tending"
        if "extension_read" in tools or "extension_list" in tools:
            return "extension_read"
        if "web_search" in tools:
            return "research"
        return (text or "action").strip().splitlines()[0][:80] or "action"

    def snapshot(self) -> WorldClockSnapshot:
        now = utc_now()
        return WorldClockSnapshot(
            local_time=now.astimezone(LOCAL_TZ).strftime("%A %d %B %Y, %H:%M %Z"),
            runtime_age=_format_age(self.started_at, now).replace(" ago", ""),
            last_human_message=_format_age(self.last_human_at, now),
            last_ambient_action=self.last_ambient_action,
            last_ambient_age=_format_age(self.last_ambient_at, now),
            last_research=_format_age(self.last_research_at, now),
        )

    def render(self) -> str:
        snap = self.snapshot()
        return "\n".join(
            [
                "[WORLD CLOCK]",
                f"- Local time: {snap.local_time}",
                f"- Runtime age: {snap.runtime_age}",
                f"- Last human message: {snap.last_human_message}",
                f"- Last ambient action: {snap.last_ambient_action} ({snap.last_ambient_age})",
                f"- Last research: {snap.last_research}",
                "[END WORLD CLOCK]",
            ]
        )

    def as_dict(self) -> Dict[str, Any]:
        snap = self.snapshot()
        return {
            "local_time": snap.local_time,
            "runtime_age": snap.runtime_age,
            "last_human_message": snap.last_human_message,
            "last_ambient_action": snap.last_ambient_action,
            "last_ambient_age": snap.last_ambient_age,
            "last_research": snap.last_research,
        }
