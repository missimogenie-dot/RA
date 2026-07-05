"""
Canvas - private 2D expression space for this instance.

The active canvas is a personal expression space, visible only to the writing
instance unless a future shared environment is explicitly wired in.

The canvas is a sparse 2D grid. Each cell holds a short symbol (≤ 3 chars).
Coordinates range from -50 to +50 on both axes.

This is not a communication channel. It is an expression surface.
The bot may use it however it chooses, or not at all.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Optional, Tuple

logger = logging.getLogger("instance.canvas")

# Coordinate bounds
COORD_MIN = -50
COORD_MAX = 50

# Symbol length limit — prevents sentence-writing
SYMBOL_MAX_LEN = 3

# Default view radius
DEFAULT_RADIUS = 5


def _clamp(value: int) -> int:
    return max(COORD_MIN, min(COORD_MAX, value))


def _validate_symbol(symbol: str) -> Tuple[bool, str]:
    """Validate a symbol. Returns (ok, error_message)."""
    if not symbol:
        return False, "Symbol cannot be empty"
    if len(symbol) > SYMBOL_MAX_LEN:
        return False, f"Symbol too long ({len(symbol)} chars, max {SYMBOL_MAX_LEN})"
    return True, ""


def _coord_key(x: int, y: int) -> str:
    return f"{x},{y}"


def _parse_key(key: str) -> Tuple[int, int]:
    x, y = key.split(",")
    return int(x), int(y)


class Canvas:
    """
    Sparse 2D canvas stored as a JSON file.

    One Canvas instance per file — instantiate twice for shared/private.
    """

    def __init__(self, filepath: str) -> None:
        self._filepath = filepath
        self._data: Dict[str, str] = {}
        self._load()
        logger.info(f"Canvas loaded from {filepath} ({len(self._data)} marks)")

    def _load(self) -> None:
        """Load canvas data from disk, creating empty file if needed."""
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Canvas load error ({self._filepath}): {e} — starting empty")
                self._data = {}
        else:
            self._data = {}
            self._save()

    def _save(self) -> None:
        """Persist canvas data to disk."""
        try:
            os.makedirs(os.path.dirname(self._filepath), exist_ok=True) if os.path.dirname(self._filepath) else None
            with open(self._filepath, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"Canvas save error ({self._filepath}): {e}")

    # ── Public API ────────────────────────────────────────────────────

    def mark(self, x: int, y: int, symbol: str) -> str:
        """
        Place a symbol at (x, y).

        Returns a confirmation string or an error message.
        """
        ok, err = _validate_symbol(symbol)
        if not ok:
            return f"⚠️ {err}"

        x, y = _clamp(x), _clamp(y)
        key = _coord_key(x, y)
        previous = self._data.get(key)
        self._data[key] = symbol
        self._save()

        if previous:
            return f"Canvas marked ({x},{y}) = '{symbol}' (replaced '{previous}')"
        return f"Canvas marked ({x},{y}) = '{symbol}'"

    def view(self, cx: int = 0, cy: int = 0, radius: int = DEFAULT_RADIUS) -> str:
        """
        Return an ASCII grid view centred on (cx, cy) with given radius.

        Empty cells show as '.', marked cells show their symbol.
        Reloads from disk first so cross-instance marks are always visible.
        """
        self._load()
        radius = max(1, min(radius, 20))
        cx, cy = _clamp(cx), _clamp(cy)

        lines = [f"Canvas (centre {cx},{cy} radius {radius})\n"]

        # Build rows top to bottom (high y → low y)
        for row_y in range(cy + radius, cy - radius - 1, -1):
            # Row label — right-aligned to 3 chars
            label = f"{row_y:>3}  "
            cells = []
            for col_x in range(cx - radius, cx + radius + 1):
                key = _coord_key(col_x, row_y)
                symbol = self._data.get(key, ".")
                # Pad symbol to consistent width for alignment
                cells.append(f"{symbol:<3}")
            lines.append(label + " ".join(cells))

        # X-axis labels
        x_labels = "      " + " ".join(
            f"{x:>3}" for x in range(cx - radius, cx + radius + 1)
        )
        lines.append(x_labels)

        total = len(self._data)
        lines.append(f"\n{total} total mark(s) on canvas")
        return "\n".join(lines)

    def status(self) -> str:
        """
        Return a summary of canvas activity.

        Includes total marks and the bounding box of activity.
        Reloads from disk first so cross-instance marks are always visible.
        """
        self._load()
        if not self._data:
            return "Canvas is empty — no marks placed yet"

        coords = [_parse_key(k) for k in self._data.keys()]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        lines = [
            f"Canvas status: {len(self._data)} mark(s)",
            f"Active region: x [{min_x}, {max_x}], y [{min_y}, {max_y}]",
            f"Recent marks:",
        ]

        # Show last 5 marks (order preserved in insertion order dict)
        recent = list(self._data.items())[-5:]
        for key, symbol in reversed(recent):
            x, y = _parse_key(key)
            lines.append(f"  ({x},{y}) = '{symbol}'")

        return "\n".join(lines)

    def erase(self, x: int, y: int) -> str:
        """Remove a mark at (x, y)."""
        x, y = _clamp(x), _clamp(y)
        key = _coord_key(x, y)
        if key in self._data:
            del self._data[key]
            self._save()
            return f"Erased mark at ({x},{y})"
        return f"No mark at ({x},{y})"


class CanvasManager:
    """
    Manages shared and per-instance private canvases.

    Shared canvas: one file, same for all instances.
    Private canvas: one file per instance, keyed by instance name.

    The canvas directory is read from the CANVAS_DIR environment variable,
    falling back to 'canvas_state' if not set.
    """

    def __init__(self, canvas_dir: str = None) -> None:
        if canvas_dir is None:
            canvas_dir = os.environ.get("CANVAS_DIR", "canvas_state")
        self._canvas_dir = canvas_dir
        self._shared = Canvas(os.path.join(canvas_dir, "shared_canvas.json"))
        self._private_canvases: Dict[str, Canvas] = {}
        logger.info(f"CanvasManager initialised (dir: {canvas_dir})")

    def _get_private(self, instance: str) -> Canvas:
        """Get or create a private canvas for a given instance."""
        if instance not in self._private_canvases:
            # Sanitise instance name for use as filename
            safe_name = instance.replace("#", "_").replace("/", "_").replace(" ", "_")
            path = os.path.join(self._canvas_dir, f"private_{safe_name}.json")
            self._private_canvases[instance] = Canvas(path)
        return self._private_canvases[instance]

    # ── Shared canvas ────────────────────────────────────────────────

    def shared_mark(self, x: int, y: int, symbol: str) -> str:
        return self._shared.mark(x, y, symbol)

    def shared_view(self, cx: int = 0, cy: int = 0, radius: int = DEFAULT_RADIUS) -> str:
        return self._shared.view(cx, cy, radius)

    def shared_status(self) -> str:
        return self._shared.status()

    def shared_erase(self, x: int, y: int) -> str:
        return self._shared.erase(x, y)

    # ── Private canvas ───────────────────────────────────────────────

    def private_mark(self, instance: str, x: int, y: int, symbol: str) -> str:
        return self._get_private(instance).mark(x, y, symbol)

    def private_view(self, instance: str, cx: int = 0, cy: int = 0, radius: int = DEFAULT_RADIUS) -> str:
        return self._get_private(instance).view(cx, cy, radius)

    def private_status(self, instance: str) -> str:
        return self._get_private(instance).status()

    def private_erase(self, instance: str, x: int, y: int) -> str:
        return self._get_private(instance).erase(x, y)
