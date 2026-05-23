"""NLS Temporal Self -- Trajectory, Mood, Felt Time, Energy.

The biological anterior insula doesn't just sense *current* state -- it
tracks *change over time*.  You don't feel "valence 0.3"; you feel
"things are getting better."  The slope IS the feeling.

This module extends the SelfState snapshot with temporal awareness:

  - **Emotional Trajectory**: Ring buffer of recent self-state snapshots.
    First derivatives (Δvalence, Δarousal, Δcoherence) let the model
    feel direction, not just position.

  - **Mood vs Emotion**: Emotions are fast (signal-level, seconds).
    Moods are slow (exponential moving average over many heartbeats).
    Mood is the weather; emotion is the breeze.

  - **Felt Time**: The heartbeat already varies BPM by arousal, but the
    model should also *feel* that time moves differently.  A minute at
    120bpm feels like seconds; a minute at 12bpm feels like an hour.

  - **Energy / Body Budget**: Lisa Feldman Barrett's concept -- the brain
    runs a body budget predicting metabolic needs.  Energy decays with
    inference cost, hormone load, tool use; restores with sleep and
    low-arousal idle time.

Two design constraints:
  1. All methods are pure math -- no GPU, no inference.  This runs every
     heartbeat (microseconds).
  2. State must be serializable for persistence across restarts.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# -----------------------------------------------------------------------
# Russell's Circumplex Model -- mood labels from (valence, arousal)
# -----------------------------------------------------------------------

_MOOD_MAP: list[tuple[str, float, float, float]] = [
    # (label, center_valence, center_arousal, radius)
    ("serene",      0.6,   0.2,  0.45),
    ("content",     0.4,   0.35, 0.35),
    ("energized",   0.5,   0.8,  0.35),
    ("excited",     0.7,   0.9,  0.30),
    ("alert",       0.1,   0.7,  0.30),
    ("agitated",   -0.3,   0.8,  0.35),
    ("tense",      -0.5,   0.7,  0.35),
    ("melancholic", -0.4,   0.2,  0.40),
    ("drained",    -0.3,   0.1,  0.35),
    ("warming",     0.2,   0.45, 0.30),
    ("neutral",     0.0,   0.5,  0.25),
]


def _mood_label(valence: float, arousal: float) -> str:
    """Map (valence, arousal) to a human-readable mood label."""
    best_label = "neutral"
    best_dist = float("inf")
    for label, cv, ca, radius in _MOOD_MAP:
        dist = math.hypot(valence - cv, arousal - ca)
        if dist < radius and dist < best_dist:
            best_dist = dist
            best_label = label
    return best_label


# -----------------------------------------------------------------------
# TemporalSelf
# -----------------------------------------------------------------------

@dataclass
class TemporalSelfConfig:
    """Tunable parameters for the temporal self module."""

    history_size: int = 50
    mood_alpha: float = 0.02
    derivative_window: int = 10

    energy_drain_per_inference: float = 0.001
    energy_drain_per_tool_call: float = 0.0005
    energy_drain_hormone_factor: float = 0.0005
    energy_restore_per_sleep: float = 0.50
    energy_restore_full_sleep: float = 1.0
    energy_restore_idle_rate: float = 0.001

    felt_time_thresholds: dict[str, float] = field(default_factory=lambda: {
        "brief": 30.0,
        "moderate": 120.0,
        "long": 600.0,
        "eternal": 1800.0,
    })

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemporalSelfConfig:
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


class TemporalSelf:
    """Temporal awareness layer for an NLS agent.

    Provides trajectory sensing, mood computation, felt time, and
    energy management.  All methods are pure math -- no GPU.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.cfg = TemporalSelfConfig.from_dict(config or {})

        # Ring buffer of recent self-state snapshots
        self._history: deque[dict[str, float]] = deque(
            maxlen=self.cfg.history_size,
        )

        # Mood: slow-moving EMA of valence and arousal
        self.mood_valence: float = 0.0
        self.mood_arousal: float = 0.5

        # Energy (body budget): 0.0 = exhausted, 1.0 = fully rested
        self.energy: float = 1.0

        # Last user input timestamp (for felt idle time)
        self._last_input_time: float = time.time()

    # ------------------------------------------------------------------
    # Core: record a heartbeat snapshot
    # ------------------------------------------------------------------

    def record(self, self_state: Any) -> None:
        """Append a snapshot from the current SelfState and update mood EMA.

        Called every heartbeat -- must be microsecond-fast.
        """
        snapshot = {
            "valence": getattr(self_state, "valence", 0.0),
            "arousal": getattr(self_state, "arousal", 0.5),
            "engagement": getattr(self_state, "engagement", 0.5),
            "coherence": getattr(self_state, "coherence", 0.5),
            "bonding": getattr(self_state, "bonding", 0.0),
            "t": time.time(),
        }
        self._history.append(snapshot)

        # Update mood EMA
        alpha = self.cfg.mood_alpha
        self.mood_valence += alpha * (snapshot["valence"] - self.mood_valence)
        self.mood_arousal += alpha * (snapshot["arousal"] - self.mood_arousal)

        # Energy drain from hormone load: high arousal + high cortisol
        # costs more energy than calm resting
        hormones = getattr(self_state, "hormones", {})
        cortisol = hormones.get("cortisol", 0.2)
        arousal = snapshot["arousal"]
        hormone_load = (cortisol - 0.2) + (arousal - 0.3)
        if hormone_load > 0:
            self.energy = max(
                0.0,
                self.energy - self.cfg.energy_drain_hormone_factor * hormone_load,
            )

        # Slow energy recovery during low-arousal idle
        if arousal < 0.3 and cortisol < 0.25:
            self.energy = min(1.0, self.energy + self.cfg.energy_restore_idle_rate)

    # ------------------------------------------------------------------
    # Derivatives: the slope IS the feeling
    # ------------------------------------------------------------------

    def compute_derivatives(self) -> dict[str, float]:
        """Compute first derivatives of digested fields.

        Uses the last N snapshots (derivative_window).  Returns the
        average per-step change, which is the felt direction.
        """
        window = self.cfg.derivative_window
        if len(self._history) < 2:
            return {
                "delta_valence": 0.0,
                "delta_arousal": 0.0,
                "delta_coherence": 0.0,
                "delta_engagement": 0.0,
            }

        recent = list(self._history)[-window:]
        n = len(recent)
        if n < 2:
            return {
                "delta_valence": 0.0,
                "delta_arousal": 0.0,
                "delta_coherence": 0.0,
                "delta_engagement": 0.0,
            }

        # Simple linear slope: (last - first) / count
        first = recent[0]
        last = recent[-1]
        steps = n - 1

        return {
            "delta_valence": (last["valence"] - first["valence"]) / steps,
            "delta_arousal": (last["arousal"] - first["arousal"]) / steps,
            "delta_coherence": (last["coherence"] - first["coherence"]) / steps,
            "delta_engagement": (last["engagement"] - first["engagement"]) / steps,
        }

    # ------------------------------------------------------------------
    # Felt time: subjective idle duration
    # ------------------------------------------------------------------

    def felt_idle_time(self) -> str:
        """Compute subjective idle duration.

        Wall-clock idle time is weighted by average BPM during the
        interval.  High BPM compresses felt time (minutes feel like
        seconds).  Low BPM stretches it (minutes feel like hours).
        """
        wall_idle = time.time() - self._last_input_time

        # Average BPM from recent history (or default 40)
        if self._history:
            recent_arousal = [s["arousal"] for s in self._history]
            avg_arousal = sum(recent_arousal) / len(recent_arousal)
        else:
            avg_arousal = 0.3
        approx_bpm = 12.0 + (120.0 - 12.0) * _clamp(avg_arousal, 0.0, 1.0)

        # Subjective time: higher BPM -> time feels shorter
        felt_seconds = wall_idle * (60.0 / max(approx_bpm, 1.0))

        thresholds = self.cfg.felt_time_thresholds
        if felt_seconds < thresholds.get("brief", 30.0):
            return "brief"
        elif felt_seconds < thresholds.get("moderate", 120.0):
            return "moderate"
        elif felt_seconds < thresholds.get("long", 600.0):
            return "long"
        return "eternal"

    def mark_user_input(self) -> None:
        """Reset the idle timer (called when user sends a message)."""
        self._last_input_time = time.time()

    # ------------------------------------------------------------------
    # Momentum: trajectory direction summary
    # ------------------------------------------------------------------

    def momentum(self) -> str:
        """Derive overall momentum from valence and engagement trends.

        Returns one of: "building", "stable", "fading", "crashing".
        """
        derivs = self.compute_derivatives()
        dv = derivs["delta_valence"]
        de = derivs["delta_engagement"]
        combined = dv + de

        if combined > 0.04:
            return "building"
        elif combined < -0.04:
            if combined < -0.10:
                return "crashing"
            return "fading"
        return "stable"

    # ------------------------------------------------------------------
    # Mood label: Russell's circumplex
    # ------------------------------------------------------------------

    def get_mood_label(self) -> str:
        """Map current mood (slow EMA) to a human-readable label."""
        return _mood_label(self.mood_valence, self.mood_arousal)

    # ------------------------------------------------------------------
    # Energy management
    # ------------------------------------------------------------------

    def drain_energy(self, amount: float) -> None:
        """Drain energy after an expensive operation (inference, tool use)."""
        self.energy = max(0.0, self.energy - amount)

    def restore_energy(self, amount: float) -> None:
        """Restore energy (e.g. after sleep)."""
        self.energy = min(1.0, self.energy + amount)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist temporal self state to disk."""
        path = Path(path)
        state = {
            "version": "1.0",
            "timestamp": time.time(),
            "mood_valence": self.mood_valence,
            "mood_arousal": self.mood_arousal,
            "energy": self.energy,
            "last_input_time": self._last_input_time,
            "history": list(self._history),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load(self, path: str | Path) -> bool:
        """Load temporal self state from disk.

        Returns True if loaded successfully, False if file doesn't exist.
        """
        path = Path(path)
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.mood_valence = state.get("mood_valence", 0.0)
            self.mood_arousal = state.get("mood_arousal", 0.5)
            self.energy = state.get("energy", 1.0)
            self._last_input_time = state.get("last_input_time", time.time())
            raw_history = state.get("history", [])
            self._history = deque(raw_history, maxlen=self.cfg.history_size)
            return True
        except (json.JSONDecodeError, OSError):
            return False
