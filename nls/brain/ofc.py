"""NLS Orbitofrontal Cortex -- The Somatic Value Computer.

Damasio's somatic marker hypothesis: before any significant decision,
the OFC queries the body: "How does this choice *feel*?"  High cortisol
makes risky options feel bad.  High dopamine makes novel options feel
exciting.  Low energy makes costly options feel draining.

Three functions:

  1. **Somatic Evaluation**: Before a drive action, evaluate the option
     against the current body state.  Returns a bias score that the
     drive engine's effort gate can use.

  2. **Outcome Prediction**: Track past action→outcome associations.
     "Last time I searched this domain while stressed, cortisol spiked
     further."  Builds an experiential prior that biases future choices.

  3. **Social Value**: Track per-user interaction quality over time.
     Users who engage deeply with technical content have higher expected
     social reward.  Users who were curt after long answers have lower.

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
class OFCConfig:
    """Tunable parameters for the OFC module."""

    # Somatic evaluation weights
    cortisol_risk_penalty: float = -0.30
    dopamine_novelty_bonus: float = 0.20
    energy_cost_penalty: float = -0.25
    oxytocin_social_bonus: float = 0.15
    serotonin_stability_bonus: float = 0.10

    # Outcome tracking
    outcome_history_max: int = 500
    outcome_ema_alpha: float = 0.15

    # Social value tracking
    social_value_alpha: float = 0.10
    max_users: int = 100

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OFCConfig:
        cfg = cls()
        somatic = data.get("somatic_weights", {})
        for k, v in somatic.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        for k in ("outcome_history_max", "outcome_ema_alpha",
                   "social_value_alpha", "max_users"):
            if k in data:
                setattr(cfg, k, data[k])
        return cfg


# -----------------------------------------------------------------------
# Outcome Record
# -----------------------------------------------------------------------

@dataclass
class OutcomeRecord:
    """A past decision and its hormonal consequences."""

    action_type: str
    domain: str
    success: bool
    hormones_before: dict[str, float]
    hormones_after: dict[str, float]
    energy_before: float
    energy_after: float
    timestamp: float = field(default_factory=time.time)

    def hormonal_delta(self) -> dict[str, float]:
        """Compute per-hormone change (after - before)."""
        return {
            k: self.hormones_after.get(k, 0.0) - v
            for k, v in self.hormones_before.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "domain": self.domain,
            "success": self.success,
            "hormones_before": self.hormones_before,
            "hormones_after": self.hormones_after,
            "energy_before": self.energy_before,
            "energy_after": self.energy_after,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OutcomeRecord:
        return cls(
            action_type=d.get("action_type", ""),
            domain=d.get("domain", ""),
            success=d.get("success", False),
            hormones_before=d.get("hormones_before", {}),
            hormones_after=d.get("hormones_after", {}),
            energy_before=d.get("energy_before", 1.0),
            energy_after=d.get("energy_after", 1.0),
            timestamp=d.get("timestamp", 0.0),
        )


# -----------------------------------------------------------------------
# OrbitofrontalCortex
# -----------------------------------------------------------------------

class OrbitofrontalCortex:
    """Somatic value computer for drive action decisions.

    Evaluates options against felt body state, predicts outcomes from
    experience, and tracks social value of interactions.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.cfg = OFCConfig.from_dict(config or {})
        self._outcome_history: deque[OutcomeRecord] = deque(
            maxlen=self.cfg.outcome_history_max,
        )
        self._social_value: dict[str, float] = {}

        # EMA of success rate per (action_type, domain_prefix)
        self._success_ema: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 1. Somatic Evaluation
    # ------------------------------------------------------------------

    def somatic_evaluate(
        self,
        action_type: str,
        domain: str,
        hormones: dict[str, float],
        energy: float,
    ) -> float:
        """Evaluate an action option against the current body state.

        Returns a bias score from -1.0 (body says "don't") to +1.0
        (body says "go for it").  This is the gut feeling.
        """
        cortisol = hormones.get("cortisol", 0.2)
        dopamine = hormones.get("dopamine", 0.5)
        oxytocin = hormones.get("oxytocin", 0.2)
        serotonin = hormones.get("serotonin", 0.5)

        bias = 0.0

        # High cortisol -> risky/effortful options feel bad
        stress_excess = max(0.0, cortisol - 0.3)
        if action_type in ("web_search", "deep_browse", "disconfirm"):
            bias += self.cfg.cortisol_risk_penalty * stress_excess * 3.0

        # High dopamine -> novel options feel exciting
        dopamine_excess = max(0.0, dopamine - 0.4)
        if action_type in ("web_search", "deep_browse"):
            bias += self.cfg.dopamine_novelty_bonus * dopamine_excess * 3.0

        # Low energy -> costly options feel draining
        energy_deficit = max(0.0, 0.5 - energy)
        if action_type in ("web_search", "deep_browse", "self_test"):
            bias += self.cfg.energy_cost_penalty * energy_deficit * 2.0

        # High oxytocin -> social options feel rewarding
        oxytocin_excess = max(0.0, oxytocin - 0.2)
        if action_type == "reach_out":
            bias += self.cfg.oxytocin_social_bonus * oxytocin_excess * 3.0

        # High serotonin -> reflective/stable options feel good
        serotonin_excess = max(0.0, serotonin - 0.4)
        if action_type == "reflect":
            bias += self.cfg.serotonin_stability_bonus * serotonin_excess * 2.0

        # Factor in past experience with this domain
        predicted = self.predict_outcome(action_type, domain)
        if predicted is not None:
            # Past success boosts confidence, past failure dampens it
            bias += (predicted - 0.5) * 0.3

        return _clamp(bias, -1.0, 1.0)

    # ------------------------------------------------------------------
    # 2. Outcome Prediction
    # ------------------------------------------------------------------

    def predict_outcome(
        self, action_type: str, domain: str,
    ) -> float | None:
        """Predict success probability based on past outcomes.

        Returns the EMA success rate for this action+domain combo,
        or None if no history exists.
        """
        key = self._outcome_key(action_type, domain)
        return self._success_ema.get(key)

    def record_outcome(
        self,
        action_type: str,
        domain: str,
        success: bool,
        hormones_before: dict[str, float],
        hormones_after: dict[str, float],
        energy_before: float = 1.0,
        energy_after: float = 1.0,
    ) -> None:
        """Record a decision outcome for future prediction."""
        record = OutcomeRecord(
            action_type=action_type,
            domain=domain,
            success=success,
            hormones_before=hormones_before,
            hormones_after=hormones_after,
            energy_before=energy_before,
            energy_after=energy_after,
        )
        self._outcome_history.append(record)

        # Update EMA success rate
        key = self._outcome_key(action_type, domain)
        alpha = self.cfg.outcome_ema_alpha
        current = self._success_ema.get(key, 0.5)
        self._success_ema[key] = current + alpha * (
            (1.0 if success else 0.0) - current
        )

    def _outcome_key(self, action_type: str, domain: str) -> str:
        """Build a lookup key from action type and domain prefix."""
        parts = domain.split(".")
        prefix = ".".join(parts[:2]) if len(parts) >= 2 else domain
        return f"{action_type}:{prefix}"

    # ------------------------------------------------------------------
    # 3. Social Value
    # ------------------------------------------------------------------

    def social_value(self, user_id: str) -> float:
        """Return expected social reward for interacting with a user.

        Higher values mean historically richer interactions.
        Default 0.5 for unknown users (neutral prior).
        """
        return self._social_value.get(user_id, 0.5)

    def update_social_value(
        self, user_id: str, interaction_quality: float,
    ) -> None:
        """Update social value EMA after an interaction.

        Parameters
        ----------
        interaction_quality : float
            0.0 = poor interaction, 1.0 = excellent interaction.
            Computed from engagement, bonding, conversation length, etc.
        """
        alpha = self.cfg.social_value_alpha
        current = self._social_value.get(user_id, 0.5)
        self._social_value[user_id] = current + alpha * (
            interaction_quality - current
        )

        # Evict oldest user if we exceed max
        if len(self._social_value) > self.cfg.max_users:
            # Remove user with lowest social value
            worst = min(self._social_value, key=self._social_value.get)  # type: ignore[arg-type]
            del self._social_value[worst]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist OFC state to disk."""
        path = Path(path)
        state = {
            "version": "1.0",
            "timestamp": time.time(),
            "outcome_history": [r.to_dict() for r in self._outcome_history],
            "success_ema": self._success_ema,
            "social_value": self._social_value,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load(self, path: str | Path) -> bool:
        """Load OFC state from disk.

        Returns True if loaded successfully.
        """
        path = Path(path)
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            raw_history = state.get("outcome_history", [])
            self._outcome_history = deque(
                (OutcomeRecord.from_dict(r) for r in raw_history),
                maxlen=self.cfg.outcome_history_max,
            )
            self._success_ema = state.get("success_ema", {})
            self._social_value = state.get("social_value", {})
            return True
        except (json.JSONDecodeError, OSError):
            return False
