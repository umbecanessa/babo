"""NLS Circadian Clock -- Schedule-Based Sleep/Wake Cycle.

Replaces reactive sleep triggers (idle timeout, periodic timer) with
a biologically-inspired circadian schedule: configurable bedtime/wake
time per agent, optional nap windows, and signal pressure safety valve.

The clock is timezone-aware and fully deterministic given a timestamp,
making it easy to test.

Three-tier sleep model:
    Tier 1: Nightly sleep at bedtime (full triage + consolidation + integration)
    Tier 2: Optional nap windows (consolidation only, if signal pressure exists)
    Tier 3: Emergency sleep (error rate, voluntary) -- unchanged, handled by ANS
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


@dataclass
class NapWindow:
    """A configured daytime nap opportunity."""

    start: time
    end: time
    condition: str = "signal_pressure"

    def contains(self, t: time) -> bool:
        return self.start <= t < self.end


@dataclass
class CircadianConfig:
    """Circadian schedule configuration for one agent."""

    enabled: bool = True
    timezone: str = "UTC"
    bedtime: str = "00:00"
    wake_time: str = "08:00"
    nap_windows: list[dict] = field(default_factory=list)
    wake_on_user_message: bool = True
    max_nightly_cycles: int = 5
    signal_pressure_cap_multiplier: float = 3.0

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def bedtime_time(self) -> time:
        h, m = (int(x) for x in self.bedtime.split(":"))
        return time(h, m)

    @property
    def wake_time_time(self) -> time:
        h, m = (int(x) for x in self.wake_time.split(":"))
        return time(h, m)

    @property
    def parsed_nap_windows(self) -> list[NapWindow]:
        windows = []
        for w in self.nap_windows:
            sh, sm = (int(x) for x in w["start"].split(":"))
            eh, em = (int(x) for x in w["end"].split(":"))
            windows.append(NapWindow(
                start=time(sh, sm),
                end=time(eh, em),
                condition=w.get("condition", "signal_pressure"),
            ))
        return windows


class CircadianClock:
    """Timezone-aware circadian clock for an agent.

    Determines whether the agent should be sleeping or awake based on
    the current time and the agent's configured schedule.
    """

    def __init__(self, config: CircadianConfig):
        self.config = config
        self._tz = config.tz
        self._bedtime = config.bedtime_time
        self._wake_time = config.wake_time_time
        self._nap_windows = config.parsed_nap_windows
        self._max_nightly_cycles = config.max_nightly_cycles
        self._signal_pressure_multiplier = config.signal_pressure_cap_multiplier

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def timezone(self) -> ZoneInfo:
        return self._tz

    def _local_now(self, now: datetime | None = None) -> datetime:
        if now is not None:
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            return now.astimezone(self._tz)
        return datetime.now(self._tz)

    def is_sleep_hours(self, now: datetime | None = None) -> bool:
        """Return True if current time falls within bedtime -> wake_time.

        Handles overnight spans (e.g. bedtime=23:00, wake=07:00).
        """
        local = self._local_now(now)
        t = local.time()

        if self._bedtime <= self._wake_time:
            # Same-day span (e.g. 01:00 - 08:00)
            return self._bedtime <= t < self._wake_time
        else:
            # Overnight span (e.g. 23:00 - 07:00)
            return t >= self._bedtime or t < self._wake_time

    def is_bedtime(self, now: datetime | None = None) -> bool:
        """Return True if it's currently within sleep hours."""
        return self.is_sleep_hours(now)

    def is_nap_window(self, now: datetime | None = None) -> Optional[NapWindow]:
        """Return the active nap window, or None if not in one."""
        local = self._local_now(now)
        t = local.time()
        for w in self._nap_windows:
            if w.contains(t):
                return w
        return None

    def is_awake_hours(self, now: datetime | None = None) -> bool:
        """Return True if it's currently awake hours (not bedtime, not nap)."""
        return not self.is_sleep_hours(now)

    def next_wake_time(self, now: datetime | None = None) -> datetime:
        """Calculate the next wake time from now."""
        local = self._local_now(now)
        wake = local.replace(
            hour=self._wake_time.hour,
            minute=self._wake_time.minute,
            second=0,
            microsecond=0,
        )
        if wake <= local:
            wake += timedelta(days=1)
        return wake

    def signal_pressure_cap(self, base_threshold: float) -> float:
        """Calculate the signal pressure cap (safety valve).

        If accumulated signals exceed this, a nap becomes urgent
        (not mid-session, but as soon as session ends).
        """
        return base_threshold * self._signal_pressure_multiplier

    def max_nightly_cycles(self) -> int:
        return self._max_nightly_cycles

    def wake_on_user_message(self) -> bool:
        return self.config.wake_on_user_message

    def __repr__(self) -> str:
        return (
            f"CircadianClock(bed={self._bedtime}, wake={self._wake_time}, "
            f"tz={self._tz}, naps={len(self._nap_windows)}, "
            f"enabled={self.config.enabled})"
        )


def load_circadian_config(autonomic_config: dict) -> CircadianConfig:
    """Extract CircadianConfig from autonomic config dict."""
    circ = autonomic_config.get("circadian", {})
    if not circ:
        return CircadianConfig(enabled=False)

    return CircadianConfig(
        enabled=circ.get("enabled", True),
        timezone=circ.get("timezone", "UTC"),
        bedtime=circ.get("bedtime", "00:00"),
        wake_time=circ.get("wake_time", "08:00"),
        nap_windows=circ.get("nap_windows", []),
        wake_on_user_message=circ.get("wake_on_user_message", True),
        max_nightly_cycles=circ.get("max_nightly_cycles", 5),
        signal_pressure_cap_multiplier=circ.get(
            "signal_pressure_cap_multiplier", 3.0,
        ),
    )
