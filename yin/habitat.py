"""
Habitat — bot-owned environment state, Ra's format, JSON storage.

Replaces the Postgres habitat tables. The snapshot keeps the JSON shape
prompt_builder already renders: {"areas": [...], "entries": [...]}.
Hand-prunable like every other lane.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .memory.jsonstore import load_entries, make_entry, new_id, save_entries, utc_now
from .memory.paths import lane_path

DEFAULT_AREAS = ("observatory", "garden", "studio", "library", "atlas", "threshold", "game")
EVENTS_CAP = 60


class Habitat:
    def __init__(self) -> None:
        self.path = lane_path("habitat.json")
        self.residue_path = lane_path("habitat_residue.json")

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict) or "areas" not in data:
            data = {
                "areas": {area: {"state": {}} for area in DEFAULT_AREAS},
                "entries": [],
                "events": [],
            }
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def ensure_area(self, area: str, state: Optional[Dict[str, Any]] = None) -> str:
        data = self._load()
        data["areas"].setdefault(area, {"state": state or {}})
        self._save(data)
        return f"area {area} present"

    def snapshot(self, area: str = "", event_limit: int = 8) -> str:
        data = self._load()
        areas = [
            {"area": name, "state": info.get("state", {})}
            for name, info in data["areas"].items()
            if not area or name == area
        ]
        entries = [
            entry for entry in data["entries"]
            if (not area or entry.get("area") == area) and entry.get("status") != "closed"
        ][-max(1, event_limit):]
        return json.dumps({"areas": areas, "entries": entries}, ensure_ascii=False)

    def place_entry(
        self,
        *,
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
        if not area or not title:
            return "habitat placement needs an area and a title. Areas: " + ", ".join(DEFAULT_AREAS)
        data = self._load()
        data["areas"].setdefault(area, {"state": {}})
        entry_id = new_id()
        data["entries"].append({
            "id": entry_id,
            "area": area,
            "entry_type": entry_type or "note",
            "title": title,
            "content": content,
            "status": status,
            "source_type": source_type,
            "source_ref": source_ref,
            "reason": reason,
            "created_at": utc_now(),
        })
        self._save(data)
        return f"placed {entry_type or 'note'} in {area}: {title} ({entry_id})"

    def update_state(
        self,
        *,
        area: str,
        state_patch: Optional[Dict[str, Any]] = None,
        note: str = "",
        trace_id: str = "",
    ) -> str:
        data = self._load()
        if area not in data["areas"]:
            return f"no area '{area}'. Areas: {', '.join(sorted(data['areas']))}."
        if state_patch:
            data["areas"][area].setdefault("state", {}).update(state_patch)
        if note:
            data["areas"][area]["state"]["note"] = note
        self._save(data)
        return f"{area} state updated"

    def log_event(
        self,
        *,
        area: str,
        action: str,
        content: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: str = "",
    ) -> str:
        data = self._load()
        data["events"].append({
            "id": new_id(), "area": area, "action": action,
            "content": content, "created_at": utc_now(),
        })
        data["events"] = data["events"][-EVENTS_CAP:]
        self._save(data)
        return "habitat event logged"

    # ── residue decisions (ambient shelving audit trail) ─────────────

    def log_residue_decision(self, **fields: Any) -> str:
        entries = load_entries(self.residue_path)
        entry = make_entry(str(fields.get("reason", "")), **{
            key: value for key, value in fields.items() if key != "reason"
        })
        entries.append(entry)
        save_entries(self.residue_path, entries[-EVENTS_CAP:])
        return entry["id"]

    def recent_residue_decisions(self, limit: int = 10) -> str:
        entries = load_entries(self.residue_path)[-max(1, limit):]
        return json.dumps(
            [{
                "tool_name": e.get("tool_name", ""),
                "has_residue": e.get("has_residue", False),
                "reason": e.get("text", ""),
                "area": e.get("area", ""),
                "created_at": e.get("created_at", ""),
            } for e in reversed(entries)],
            ensure_ascii=False,
        )
