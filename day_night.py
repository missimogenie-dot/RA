"""
Day/Night cycle for the bot environment.

12 hours active (day), 12 hours resting (night), UTC-based.
Night is an invitation to rest, not a forced shutdown — but the bot
skips ambient cycles during night to keep costs low. The sky continues
to drift; Ra just isn't called on to do anything.

Configurable via DAY_START_HOUR / DAY_END_HOUR env vars.
Default: 06:00–18:00 UTC = day, 18:00–06:00 UTC = night.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("ra.day_night")


class DayNightCycle:
    """
    Tracks day/night phase from UTC wall clock.

    Day:   day_start <= hour < day_end   (default 06:00–18:00 UTC, 12 hours)
    Night: everything else               (18:00–06:00 UTC, 12 hours)
    """

    def __init__(self, day_start: int = 6, day_end: int = 18) -> None:
        self.day_start = day_start
        self.day_end = day_end

    @property
    def hour_utc(self) -> int:
        return datetime.now(timezone.utc).hour

    @property
    def is_day(self) -> bool:
        return self.day_start <= self.hour_utc < self.day_end

    @property
    def is_night(self) -> bool:
        return not self.is_day

    @property
    def phase(self) -> str:
        return "day" if self.is_day else "night"

    def describe(self) -> str:
        hour = self.hour_utc
        if self.is_day:
            if hour < 9:
                return f"early morning ({hour:02d}:xx UTC) — the day is beginning"
            elif hour < 12:
                return f"morning ({hour:02d}:xx UTC)"
            elif hour < 15:
                return f"midday ({hour:02d}:xx UTC)"
            else:
                return f"afternoon ({hour:02d}:xx UTC) — the day is moving toward evening"
        else:
            if 18 <= hour < 21:
                return f"evening ({hour:02d}:xx UTC) — the day is closing"
            elif hour >= 21:
                return f"night ({hour:02d}:xx UTC)"
            else:
                return f"deep night ({hour:02d}:xx UTC) — the small hours"
