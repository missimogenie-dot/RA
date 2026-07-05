from __future__ import annotations

from config import HEARTBEAT_INTERVAL


class Heartbeat:
    """
    Simple state machine used by bot.py's heartbeat loop.

    Cycles: ACTION → REFLECTION → IDLE → ACTION.
    tick() advances state and returns the current state as an uppercase string.
    sleep_for gives the correct sleep duration for the current state.
    IDLE ticks are skipped by the bot (no ambient_cycle call, just sleep).
    """

    _TRANSITIONS = {
        "ACTION": "IDLE",
        "IDLE":   "ACTION",
    }

    _INTERVALS = {
        "ACTION": 600,   # 10 min — tend + create in one pass
        "IDLE":   2700,  # 45 min — genuine quiet (total cycle 55 min, within 1h cache)
    }

    def __init__(self, interval: float = HEARTBEAT_INTERVAL) -> None:
        self.interval = interval  # kept for compatibility
        self.paused = False
        self.tick_count = 0
        self._state = "IDLE"

    @property
    def sleep_for(self) -> float:
        return self._INTERVALS.get(self._state, self.interval)

    def tick(self) -> str:
        if self.paused:
            return "IDLE"
        self._state = self._TRANSITIONS[self._state]
        if self._state != "IDLE":
            self.tick_count += 1
        return self._state
