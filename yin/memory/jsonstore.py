"""
Hand-prunable JSON persistence.

JSON files are the source of truth for every memory lane. The semantic
mirror (ChromaDB or the local fallback) is rebuilt from them whenever
they disagree. Immie can open any lane file and prune it by hand; the
mirror self-heals on next load.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List

_LOCKS: Dict[str, RLock] = {}
_LOCKS_GUARD = RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _lock_for(path: Path) -> RLock:
    key = str(path)
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = RLock()
        return _LOCKS[key]


def load_entries(path: Path) -> List[Dict[str, Any]]:
    with _lock_for(path):
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt lane file is quarantined, not silently overwritten.
            quarantine = path.with_suffix(path.suffix + ".corrupt")
            path.rename(quarantine)
            return []
        return data if isinstance(data, list) else []


def save_entries(path: Path, entries: List[Dict[str, Any]]) -> None:
    with _lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)


def make_entry(text: str, **meta: Any) -> Dict[str, Any]:
    now = utc_now()
    entry: Dict[str, Any] = {
        "id": new_id(),
        "text": text,
        "created_at": now,
        "updated_at": now,
        "weight": 1,
    }
    entry.update(meta)
    return entry
