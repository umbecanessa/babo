"""NLS Three-Network Dynamics -- ECN / SN / DMN Switching.

Brain analog: Neuroscience identifies three large-scale resting-state
networks that anti-correlate:

  - **ECN** (Executive Control Network): Task-focused attention, working
    memory engagement, goal-directed behaviour.  Active during
    conversation and agentic tasks.

  - **SN** (Salience Network): Novelty/importance detection.  The switch
    that governs transitions between ECN and DMN.  Fires on user input,
    high prediction error, or strong thalamic delta.

  - **DMN** (Default Mode Network): Mind-wandering, self-referential
    thought, social simulation.  Active during idle periods when the
    agent has no immediate task.

Before this module, ECN/SN/DMN lived implicitly:
  - ``engagement`` proxied ECN
  - ``arousal`` + ``delta_ratio`` proxied SN
  - ``turns_since_input`` + engagement thresholds gated DMN

This module makes those dynamics explicit with:
  1. Proper activation levels for all three networks (0.0-1.0)
  2. Anti-correlation: ECN + DMN <= 1.0 (when one is up, the other is down)
  3. SN as the switch: high salience -> ECN; low salience -> DMN
  4. Transition event logging for observability

All methods are pure math -- no GPU, no inference.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# -----------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------

@dataclass
class NetworkConfig:
    # SN switching
    sn_switch_threshold: float = 0.45
    sn_arousal_weight: float = 0.35
    sn_delta_ratio_weight: float = 0.25
    sn_prediction_error_weight: float = 0.25
    sn_recency_weight: float = 0.15
    sn_spike_amount: float = 0.30
    sn_spike_decay: float = 0.08

    # ECN computation
    ecn_engagement_weight: float = 0.60
    ecn_recency_weight: float = 0.25
    ecn_wm_salience_weight: float = 0.15

    # DMN computation
    dmn_idle_weight: float = 0.40
    dmn_low_engagement_weight: float = 0.30
    dmn_frustration_weight: float = 0.20
    dmn_low_arousal_weight: float = 0.10

    # Anti-correlation
    anti_correlation_strength: float = 0.85

    # DMN eligibility thresholds (replaces raw inner_loop checks)
    dmn_passive_threshold: float = 0.40
    dmn_active_threshold: float = 0.60
    dmn_min_idle_breaths: int = 3
    dmn_active_min_idle_breaths: int = 8

    # Transition tracking
    transition_history_size: int = 20

    # EMA smoothing (prevents jitter)
    activation_ema_alpha: float = 0.15

    # Sleep/wake
    wake_ecn: float = 0.3
    wake_sn: float = 0.3
    wake_dmn: float = 0.3

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NetworkConfig:
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


# -----------------------------------------------------------------------
# Transition Event
# -----------------------------------------------------------------------

@dataclass
class TransitionEvent:
    """Records a network dominance switch."""
    timestamp: float
    from_network: str
    to_network: str
    trigger: str  # "user_input", "idle_decay", "dmn_finding", "salience_spike"
    sn_level: float
    ecn_level: float
    dmn_level: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "from": self.from_network,
            "to": self.to_network,
            "trigger": self.trigger,
            "sn": round(self.sn_level, 3),
            "ecn": round(self.ecn_level, 3),
            "dmn": round(self.dmn_level, 3),
        }


# -----------------------------------------------------------------------
# NetworkDynamics
# -----------------------------------------------------------------------

class NetworkDynamics:
    """Three-network dynamics engine: ECN / SN / DMN with anti-correlation.

    Called every heartbeat to update activation levels.  Provides
    DMN eligibility gates that replace raw threshold checks in the
    inner loop.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.cfg = NetworkConfig.from_dict(config or {})

        # Activation levels (0.0-1.0)
        self.ecn: float = self.cfg.wake_ecn
        self.sn: float = self.cfg.wake_sn
        self.dmn: float = self.cfg.wake_dmn

        # Dominant network
        self.dominant: str = "transition"

        # SN spike accumulator (decays each heartbeat)
        self._sn_spike: float = 0.0

        # Transition history
        self._transitions: deque[TransitionEvent] = deque(
            maxlen=self.cfg.transition_history_size,
        )
        self._transition_count: int = 0

    # ------------------------------------------------------------------
    # Core Update (called every heartbeat)
    # ------------------------------------------------------------------

    def update(
        self,
        engagement: float = 0.5,
        arousal: float = 0.5,
        delta_ratio: float = 0.0,
        turns_since_input: int = 0,
        frustration: float = 0.0,
        prediction_error: float = 0.0,
        energy: float = 1.0,
        wm_avg_salience: float = 0.0,
    ) -> str:
        """Update all three network activation levels.

        Called every heartbeat in ``_collect_state()``.

        Returns the dominant network label.
        """
        cfg = self.cfg
        alpha = cfg.activation_ema_alpha

        # -- Raw SN activation --
        # Salience = f(arousal, delta_ratio, PE, recency, spike)
        recency_signal = 1.0 / (1.0 + turns_since_input * 0.3)
        raw_sn = (
            arousal * cfg.sn_arousal_weight
            + delta_ratio * cfg.sn_delta_ratio_weight
            + prediction_error * cfg.sn_prediction_error_weight
            + recency_signal * cfg.sn_recency_weight
            + self._sn_spike
        )
        raw_sn = _clamp(raw_sn, 0.0, 1.0)

        # Decay spike
        self._sn_spike = max(0.0, self._sn_spike - cfg.sn_spike_decay)

        # -- Raw ECN activation --
        # Executive control = f(engagement, recency, WM salience)
        recency_boost = max(0.0, 0.3 * (1.0 - turns_since_input / 10.0))
        raw_ecn = (
            engagement * cfg.ecn_engagement_weight
            + recency_boost * cfg.ecn_recency_weight
            + wm_avg_salience * cfg.ecn_wm_salience_weight
        )
        raw_ecn = _clamp(raw_ecn, 0.0, 1.0)

        # -- Raw DMN activation --
        # Default mode = f(idle_time, low_engagement, frustration, low_arousal)
        idle_signal = min(1.0, turns_since_input / 15.0)
        low_eng = max(0.0, 1.0 - engagement * 2.0)
        low_aro = max(0.0, 1.0 - arousal * 2.0)
        raw_dmn = (
            idle_signal * cfg.dmn_idle_weight
            + low_eng * cfg.dmn_low_engagement_weight
            + frustration * cfg.dmn_frustration_weight
            + low_aro * cfg.dmn_low_arousal_weight
        )
        raw_dmn = _clamp(raw_dmn, 0.0, 1.0)

        # -- SN switching influence --
        # High SN -> boost ECN, suppress DMN (something important detected)
        # Low SN -> release ECN, allow DMN (nothing novel happening)
        if raw_sn > cfg.sn_switch_threshold:
            overshoot = raw_sn - cfg.sn_switch_threshold
            raw_ecn += overshoot * cfg.anti_correlation_strength
            raw_dmn -= overshoot * cfg.anti_correlation_strength
        else:
            undershoot = cfg.sn_switch_threshold - raw_sn
            raw_dmn += undershoot * 0.3
            raw_ecn -= undershoot * 0.2

        raw_ecn = _clamp(raw_ecn, 0.0, 1.0)
        raw_dmn = _clamp(raw_dmn, 0.0, 1.0)

        # -- Anti-correlation constraint: ECN + DMN <= 1.0 --
        total = raw_ecn + raw_dmn
        if total > 1.0:
            scale = 1.0 / total
            raw_ecn *= scale
            raw_dmn *= scale

        # -- EMA smoothing --
        self.ecn = (1.0 - alpha) * self.ecn + alpha * raw_ecn
        self.sn = (1.0 - alpha) * self.sn + alpha * raw_sn
        self.dmn = (1.0 - alpha) * self.dmn + alpha * raw_dmn

        # -- Determine dominant network --
        old_dominant = self.dominant
        if self.ecn > self.dmn + 0.10:
            new_dominant = "ecn"
        elif self.dmn > self.ecn + 0.10:
            new_dominant = "dmn"
        else:
            new_dominant = "transition"

        self.dominant = new_dominant

        # -- Log transition if changed --
        if old_dominant != new_dominant and old_dominant != "":
            self._record_transition(old_dominant, new_dominant, "state_update")

        return self.dominant

    # ------------------------------------------------------------------
    # Event-Driven SN Spikes
    # ------------------------------------------------------------------

    def on_user_input(self) -> None:
        """SN spike on user message arrival.

        User input is the strongest salience signal -- something
        important is happening, switch to executive control.
        """
        self._sn_spike = min(1.0, self._sn_spike + self.cfg.sn_spike_amount)
        old = self.dominant
        self.ecn = min(1.0, self.ecn + 0.15)
        self.dmn = max(0.0, self.dmn - 0.15)
        if self.ecn > self.dmn + 0.10:
            self.dominant = "ecn"
        if old != self.dominant:
            self._record_transition(old, self.dominant, "user_input")

    def on_dmn_finding(self, relevance: float = 0.5) -> None:
        """SN spike when DMN produces a relevant finding.

        A good dream result is salient -- the SN notices and may
        switch back to ECN to process it.
        """
        if relevance > 0.5:
            spike = min(0.5, relevance * 0.3)
            self._sn_spike = min(1.0, self._sn_spike + spike)
            old = self.dominant
            self.ecn = min(1.0, self.ecn + spike * 0.5)
            self.dmn = max(0.0, self.dmn - spike * 0.5)
            if self.ecn > self.dmn + 0.10:
                self.dominant = "ecn"
            if old != self.dominant:
                self._record_transition(old, self.dominant, "dmn_finding")

    # ------------------------------------------------------------------
    # DMN Eligibility Gates
    # ------------------------------------------------------------------

    def is_dmn_eligible(self, turns_since_input: int = 0) -> bool:
        """Whether the DMN is active enough for passive dreams.

        Replaces the raw ``turns_since_input > 3 and engagement < 0.4``
        check in ``inner_loop._breath()``.
        """
        return (
            self.dmn >= self.cfg.dmn_passive_threshold
            and turns_since_input >= self.cfg.dmn_min_idle_breaths
        )

    def is_active_dream_eligible(self, turns_since_input: int = 0) -> bool:
        """Whether the DMN is active enough for tool-using active dreams.

        Active dreams require deeper DMN dominance (the agent is
        genuinely idle, not just between messages).
        """
        return (
            self.dmn >= self.cfg.dmn_active_threshold
            and turns_since_input >= self.cfg.dmn_active_min_idle_breaths
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_dominant(self) -> str:
        """Return the dominant network label."""
        return self.dominant

    def get_dominant_label(self) -> str:
        """Human-readable label for the dominant network."""
        labels = {
            "ecn": "executive (task-focused)",
            "dmn": "default mode (mind-wandering)",
            "transition": "transitioning",
        }
        return labels.get(self.dominant, self.dominant)

    # ------------------------------------------------------------------
    # Context String for Prompt Injection
    # ------------------------------------------------------------------

    def get_context_string(self) -> str:
        """Minimal prompt hint about cognitive mode.

        Only injected when a clear dominant network exists.
        """
        if self.dominant == "ecn" and self.ecn > 0.6:
            return (
                "[Cognitive Mode: Task-focused. "
                "Executive control is dominant -- stay on task.]"
            )
        if self.dominant == "dmn" and self.dmn > 0.6:
            return (
                "[Cognitive Mode: Reflective. "
                "Default mode is active -- mind is wandering freely.]"
            )
        return ""

    # ------------------------------------------------------------------
    # Summary for Status API
    # ------------------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Return summary for ``get_status()`` / WebSocket."""
        recent_transitions = [
            t.to_dict() for t in list(self._transitions)[-5:]
        ]
        return {
            "ecn": round(self.ecn, 3),
            "sn": round(self.sn, 3),
            "dmn": round(self.dmn, 3),
            "dominant": self.dominant,
            "dominant_label": self.get_dominant_label(),
            "transition_count": self._transition_count,
            "recent_transitions": recent_transitions,
        }

    # ------------------------------------------------------------------
    # Transition Logging
    # ------------------------------------------------------------------

    def _record_transition(
        self, from_net: str, to_net: str, trigger: str,
    ) -> None:
        """Record a network dominance switch."""
        event = TransitionEvent(
            timestamp=time.time(),
            from_network=from_net,
            to_network=to_net,
            trigger=trigger,
            sn_level=self.sn,
            ecn_level=self.ecn,
            dmn_level=self.dmn,
        )
        self._transitions.append(event)
        self._transition_count += 1

    # ------------------------------------------------------------------
    # Sleep / Wake
    # ------------------------------------------------------------------

    def on_sleep(self) -> None:
        """On sleep: nothing special (state will be reset on wake)."""
        pass

    def on_wake(self) -> None:
        """On wake: reset to neutral activation levels."""
        self.ecn = self.cfg.wake_ecn
        self.sn = self.cfg.wake_sn
        self.dmn = self.cfg.wake_dmn
        self.dominant = "transition"
        self._sn_spike = 0.0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist network dynamics state to disk."""
        path = Path(path)
        state = {
            "version": "1.0",
            "timestamp": time.time(),
            "ecn": self.ecn,
            "sn": self.sn,
            "dmn": self.dmn,
            "dominant": self.dominant,
            "sn_spike": self._sn_spike,
            "transition_count": self._transition_count,
            "transitions": [t.to_dict() for t in self._transitions],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load(self, path: str | Path) -> bool:
        """Load network dynamics state from disk."""
        path = Path(path)
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.ecn = state.get("ecn", self.cfg.wake_ecn)
            self.sn = state.get("sn", self.cfg.wake_sn)
            self.dmn = state.get("dmn", self.cfg.wake_dmn)
            self.dominant = state.get("dominant", "transition")
            self._sn_spike = state.get("sn_spike", 0.0)
            self._transition_count = state.get("transition_count", 0)
            raw_transitions = state.get("transitions", [])
            self._transitions = deque(maxlen=self.cfg.transition_history_size)
            for t in raw_transitions:
                self._transitions.append(TransitionEvent(
                    timestamp=t.get("timestamp", 0.0),
                    from_network=t.get("from", ""),
                    to_network=t.get("to", ""),
                    trigger=t.get("trigger", ""),
                    sn_level=t.get("sn", 0.0),
                    ecn_level=t.get("ecn", 0.0),
                    dmn_level=t.get("dmn", 0.0),
                ))
            return True
        except (json.JSONDecodeError, OSError):
            return False
