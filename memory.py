from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BotMemory:
    """Local JSONL-backed memory. Fallback when Postgres is unavailable."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def save(self) -> None:
        pass  # nothing to flush — JSONL appends are immediate

    # ── low-level JSONL ───────────────────────────────────────────────

    def _jsonl_path(self, name: str) -> Path:
        return self.state_dir / f"{name}.jsonl"

    def append(self, name: str, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            enriched = {"ts": utc_now(), **record}
            with self._jsonl_path(name).open("a", encoding="utf-8") as f:
                f.write(json.dumps(enriched, ensure_ascii=False) + "\n")
            return enriched

    def read_recent(self, name: str, limit: int = 20) -> List[Dict[str, Any]]:
        path = self._jsonl_path(name)
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-max(1, min(limit, 200)):]

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        needle = (query or "").lower().strip()
        if not needle:
            return self.read_recent("conversations", limit=limit)
        names = ["conversations", "reflections", "ambient", "turns"]
        matches: List[Dict[str, Any]] = []
        for name in names:
            path = self._jsonl_path(name)
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if needle in line.lower():
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        row["_source"] = name
                        matches.append(row)
        return matches[-max(1, min(limit, 50)):]

    # ── typed appends ─────────────────────────────────────────────────

    def append_conversation(self, author: str, user_text: str, assistant_text: str) -> Dict[str, Any]:
        return self.append("conversations", {
            "type": "conversation",
            "author": author,
            "user": user_text,
            "assistant": assistant_text,
        })

    def append_reflection(self, content: str) -> Dict[str, Any]:
        return self.append("reflections", {
            "type": "reflection",
            "content": content,
        })

    def append_creation(self, mode: str, content: str) -> Dict[str, Any]:
        return self.append("creations", {
            "type": "creation",
            "mode": mode,
            "content": content,
        })

    def append_ambient(self, mode: str, content: str) -> Dict[str, Any]:
        return self.append("ambient", {
            "type": "ambient",
            "mode": mode,
            "content": content,
        })

    # ── compact context ───────────────────────────────────────────────

    def compact_context(self, query: str = "", limit: int = 8) -> str:
        parts: List[str] = []
        if query:
            hits = self.search(query, limit=limit)
            if hits:
                parts.append("[RELATED MEMORY]\n" + "\n".join(self._fmt(h) for h in hits))
        convs = self.read_recent("conversations", limit=limit)
        if convs:
            parts.append("[RECENT CONVERSATIONS]\n" + "\n".join(self._fmt(c) for c in convs))
        reflections = self.read_recent("reflections", limit=4)
        if reflections:
            parts.append("[RECENT REFLECTIONS]\n" + "\n".join(self._fmt(r) for r in reflections))
        creations = self.read_recent("creations", limit=3)
        if creations:
            parts.append("[RECENT CREATIONS]\n" + "\n".join(self._fmt(c) for c in creations))
        ambient = self.read_recent("ambient", limit=4)
        if ambient:
            parts.append("[RECENT AMBIENT]\n" + "\n".join(self._fmt(a) for a in ambient))
        return "\n\n".join(parts)

    @staticmethod
    def _fmt(row: Dict[str, Any]) -> str:
        ts = str(row.get("ts", ""))[:19]
        kind = row.get("type") or row.get("_source") or "record"
        text = (
            row.get("content")
            or row.get("assistant")
            or row.get("user")
            or str(row)
        )
        return f"- {ts} [{kind}] {str(text)[:500]}"

    @staticmethod
    def _parse_ts(value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
