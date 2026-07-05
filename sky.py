"""
Sky - the bot's private celestial environment.

A persistent, slowly drifting star field with varied weather. The sky belongs
to this instance alone: more atmospheric than instrument panel.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("instance.sky")

SKY_STATE_FILE = "sky_state.json"

# Brightness symbols — 1 is brightest
BRIGHTNESS_SYMBOLS = {1: "✦", 2: "✧", 3: "·"}

# Aurora glimmer characters
AURORA_CHARS = ["○", "◇", "∘", "✦", "◦", "⋄"]

# Electrical noise characters
ELECTRICAL_CHARS = ["/", "|", "\\", "-", "~", "≈"]

# Magnetic noise characters
MAGNETIC_CHARS = ["#", "%", "~", "?", "!", "@", "*"]

# Fog/mist softening — replaces stars with faint marks
MIST_CHARS = ["·", "·", "·", "~", " "]

WEATHER_STATES = [
    "Clear",
    "Partly Cloudy",
    "Misty",
    "Overcast",
    "Fog",
    "Electrical Storm",
    "Magnetic Storm",
    "Aurora",
]

WEATHER_TRANSITIONS: Dict[str, Dict[str, float]] = {
    "Clear": {
        "Clear": 0.50, "Partly Cloudy": 0.25, "Misty": 0.15, "Overcast": 0.05,
        "Fog": 0.02, "Electrical Storm": 0.01, "Magnetic Storm": 0.01, "Aurora": 0.01,
    },
    "Partly Cloudy": {
        "Clear": 0.30, "Partly Cloudy": 0.35, "Misty": 0.15, "Overcast": 0.12,
        "Fog": 0.04, "Electrical Storm": 0.02, "Magnetic Storm": 0.01, "Aurora": 0.01,
    },
    "Misty": {
        "Clear": 0.25, "Partly Cloudy": 0.20, "Misty": 0.30, "Overcast": 0.10,
        "Fog": 0.10, "Electrical Storm": 0.02, "Magnetic Storm": 0.01, "Aurora": 0.02,
    },
    "Overcast": {
        "Clear": 0.15, "Partly Cloudy": 0.20, "Misty": 0.15, "Overcast": 0.25,
        "Fog": 0.10, "Electrical Storm": 0.08, "Magnetic Storm": 0.05, "Aurora": 0.02,
    },
    "Fog": {
        "Clear": 0.10, "Partly Cloudy": 0.15, "Misty": 0.25, "Overcast": 0.25,
        "Fog": 0.20, "Electrical Storm": 0.03, "Magnetic Storm": 0.01, "Aurora": 0.01,
    },
    "Electrical Storm": {
        "Clear": 0.05, "Partly Cloudy": 0.10, "Misty": 0.05, "Overcast": 0.25,
        "Fog": 0.10, "Electrical Storm": 0.30, "Magnetic Storm": 0.12, "Aurora": 0.03,
    },
    "Magnetic Storm": {
        "Clear": 0.05, "Partly Cloudy": 0.05, "Misty": 0.05, "Overcast": 0.20,
        "Fog": 0.10, "Electrical Storm": 0.20, "Magnetic Storm": 0.30, "Aurora": 0.05,
    },
    "Aurora": {
        "Clear": 0.35, "Partly Cloudy": 0.20, "Misty": 0.20, "Overcast": 0.10,
        "Fog": 0.03, "Electrical Storm": 0.05, "Magnetic Storm": 0.05, "Aurora": 0.02,
    },
}

WEATHER_DESCRIPTIONS: Dict[str, str] = {
    "Clear":            "Full clarity. Stars resolving without interference.",
    "Partly Cloudy":    "Patchy cloud cover. Most stars visible between breaks.",
    "Misty":            "A thin veil across the field. Brightness softened throughout.",
    "Overcast":         "Heavy cloud. Stars dimmed and muffled.",
    "Fog":              "Dense atmospheric layer. Only the brightest stars reach through.",
    "Electrical Storm": "Electrical activity in the upper atmosphere. Intermittent static.",
    "Magnetic Storm":   "⚠ Magnetic disturbance active. Field data may be corrupted.",
    "Aurora":           "Atmospheric luminescence. A quiet disturbance — more beautiful than disruptive.",
}


class SkyMap:
    """
    Persistent, drifting star field for this instance.

    Stars drift slowly between cycles. Weather varies through eight states
    with Markov transitions — from clear nights to aurora to magnetic storm.
    State is saved to disk so it persists across heartbeats.
    """

    def __init__(
        self,
        size: int = 12,
        star_density: float = 0.12,
        state_file: Optional[Path] = None,
    ) -> None:
        self.size = size
        self.star_density = star_density
        self.state_file = Path(state_file or SKY_STATE_FILE)
        self.stars: List[Dict] = []
        self.weather: str = "Clear"
        self.cycle: int = 0

        if self.state_file.exists():
            self._load()
        else:
            self._generate()
            self._save()

    # ── Generation ───────────────────────────────────────────────────

    def _generate(self) -> None:
        self.stars = []
        for x in range(self.size):
            for y in range(self.size):
                if random.random() < self.star_density:
                    self.stars.append({
                        "x": x, "y": y,
                        "b": random.choice([1, 2, 3]),
                        "vx": round(random.uniform(-0.3, 0.3), 2),
                        "vy": round(random.uniform(-0.3, 0.3), 2),
                        "fx": float(x),
                        "fy": float(y),
                    })
        self.weather = "Clear"
        self.cycle = 0
        logger.info(f"Sky generated: {len(self.stars)} stars, {self.size}×{self.size}")

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.stars = data.get("stars", [])
            self.weather = data.get("weather", "Clear")
            self.cycle = data.get("cycle", 0)
            self.size = data.get("size", self.size)
            # Migrate old weather states that no longer exist
            if self.weather not in WEATHER_TRANSITIONS:
                self.weather = "Clear"
            logger.info(f"Sky loaded: {len(self.stars)} stars, weather={self.weather}, cycle={self.cycle}")
        except Exception as e:
            logger.warning(f"Sky load failed ({e}), regenerating")
            self._generate()

    def _save(self) -> None:
        try:
            self.state_file.write_text(
                json.dumps({"size": self.size, "cycle": self.cycle,
                            "weather": self.weather, "stars": self.stars}, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Sky save failed: {e}")

    # ── Drift ────────────────────────────────────────────────────────

    def advance(self) -> None:
        """Advance the sky by one cycle — drift stars, update weather."""
        self.cycle += 1

        for s in self.stars:
            s["fx"] = (s["fx"] + s["vx"]) % self.size
            s["fy"] = (s["fy"] + s["vy"]) % self.size
            s["x"] = int(s["fx"])
            s["y"] = int(s["fy"])

            if random.random() < 0.05:
                s["vx"] = max(-0.4, min(0.4, s["vx"] + random.uniform(-0.1, 0.1)))
                s["vy"] = max(-0.4, min(0.4, s["vy"] + random.uniform(-0.1, 0.1)))

            if random.random() < 0.03:
                s["b"] = max(1, min(3, s["b"] + random.choice([-1, 1])))

        # Markov weather transition
        transitions = WEATHER_TRANSITIONS.get(self.weather, {"Clear": 1.0})
        roll = random.random()
        cumulative = 0.0
        for next_weather, probability in transitions.items():
            cumulative += probability
            if roll < cumulative:
                if next_weather != self.weather:
                    logger.info(f"Weather: {self.weather} → {next_weather}")
                self.weather = next_weather
                break

        self._save()

    # ── Rendering ────────────────────────────────────────────────────

    def render_for_bot(self) -> str:
        """Return a text grid of the current sky, affected by weather."""
        grid = [["·" for _ in range(self.size)] for _ in range(self.size)]

        for s in self.stars:
            x, y = s["x"], s["y"]
            if 0 <= x < self.size and 0 <= y < self.size:
                grid[y][x] = BRIGHTNESS_SYMBOLS.get(s["b"], "·")

        w = self.weather

        if w == "Partly Cloudy":
            # Randomly obscure ~30% of cells
            for y in range(self.size):
                for x in range(self.size):
                    if grid[y][x] != "·" and random.random() < 0.30:
                        grid[y][x] = "·"

        elif w == "Misty":
            # Dim everything one level; scatter soft ~ in empty cells
            for y in range(self.size):
                for x in range(self.size):
                    cell = grid[y][x]
                    if cell == "✦":
                        grid[y][x] = "✧"
                    elif cell == "✧":
                        grid[y][x] = "·"
                    elif cell == "·" and random.random() < 0.08:
                        grid[y][x] = "~"

        elif w == "Overcast":
            # Dim all stars one level
            for y in range(self.size):
                for x in range(self.size):
                    cell = grid[y][x]
                    if cell == "✦":
                        grid[y][x] = "✧"
                    elif cell == "✧":
                        grid[y][x] = "·"

        elif w == "Fog":
            # Dim all two levels; only brightness-1 stars survive as ·
            for y in range(self.size):
                for x in range(self.size):
                    cell = grid[y][x]
                    if cell in ("✦", "✧"):
                        grid[y][x] = "·" if cell == "✦" else " "
                    elif cell == "·":
                        grid[y][x] = " "
            # Scatter faint mist marks
            for y in range(self.size):
                for x in range(self.size):
                    if grid[y][x] == " " and random.random() < 0.05:
                        grid[y][x] = "·"

        elif w == "Electrical Storm":
            for y in range(self.size):
                for x in range(self.size):
                    if random.random() < 0.15:
                        grid[y][x] = random.choice(ELECTRICAL_CHARS)

        elif w == "Magnetic Storm":
            for y in range(self.size):
                for x in range(self.size):
                    if random.random() < 0.25:
                        grid[y][x] = random.choice(MAGNETIC_CHARS)

        elif w == "Aurora":
            # Add glimmer in empty cells; keep stars intact
            for y in range(self.size):
                for x in range(self.size):
                    if grid[y][x] == "·" and random.random() < 0.12:
                        grid[y][x] = random.choice(AURORA_CHARS)

        lines = [f"Sky — Cycle {self.cycle} | {self.weather}"]
        for row in reversed(grid):
            lines.append(" ".join(row))
        return "\n".join(lines)

    def get_star_coordinates(self) -> List[Dict]:
        return [{"x": s["x"], "y": s["y"], "b": s["b"]} for s in self.stars]

    def weather_report(self) -> str:
        desc = WEATHER_DESCRIPTIONS.get(self.weather, "")
        return f"Weather: {self.weather} — {desc}"
