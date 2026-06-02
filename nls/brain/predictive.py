"""NLS Predictive Processing -- The Free Energy Brain.

Brain analog: Karl Friston's free energy principle -- the brain is
fundamentally a prediction machine that minimizes surprise.

The current NLS architecture is reactive: input comes in, output goes out.
Predictive processing makes it anticipatory: before every turn, the agent
forms an expectation about what will happen.  After every turn, it
computes prediction error (PE).  The error signal is the primary learning
driver.

Components:

  - **Pre-Turn Prediction**: Before generation, form expectations about
    domain, emotional tone, response type, and relevant facts.  Pure
    heuristics from conversation history + user model + working memory.

  - **Prediction Error**: After generation + ANS extraction, compare
    actual outcome to prediction.  Large PE -> norepinephrine surge
    (surprise/novelty).  Small PE -> serotonin (confirmation, stability).

  - **Uncertainty Tracking**: Per-domain uncertainty estimates based on
    cumulative prediction errors.  High-uncertainty domains attract
    curiosity drive actions.

  - **Active Inference Hints**: Inject uncertainty-aware guidance into
    prompts so the agent selects responses that reduce its own
    uncertainty.

All methods are pure math -- no GPU, no inference.
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
# Prediction
# -----------------------------------------------------------------------

@dataclass
class Prediction:
    """A pre-turn expectation about what will happen."""

    timestamp: float = field(default_factory=time.time)
    turn_number: int = 0

    # Predicted properties
    expected_domain: str = ""
    expected_tone: str = "neutral"       # positive, neutral, negative, technical
    expected_response_type: str = "answer"  # answer, question, action, follow_up
    expected_length: str = "medium"      # short, medium, long
    confidence: float = 0.5              # 0.0 (pure guess) to 1.0 (very sure)

    # Actual outcome (filled after the turn)
    actual_domain: str = ""
    actual_tone: str = ""
    actual_response_type: str = ""
    actual_length: str = ""

    # Computed PE
    prediction_error: float = 0.0
    pe_components: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "turn_number": self.turn_number,
            "expected_domain": self.expected_domain,
            "expected_tone": self.expected_tone,
            "expected_response_type": self.expected_response_type,
            "expected_length": self.expected_length,
            "confidence": self.confidence,
            "actual_domain": self.actual_domain,
            "actual_tone": self.actual_tone,
            "actual_response_type": self.actual_response_type,
            "actual_length": self.actual_length,
            "prediction_error": self.prediction_error,
            "pe_components": self.pe_components,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Prediction:
        p = cls()
        for k, v in d.items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p


# -----------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------

@dataclass
class PredictiveConfig:
    history_size: int = 30
    pe_norepinephrine_scale: float = 0.08
    pe_serotonin_scale: float = 0.04
    pe_cortisol_scale: float = 0.03
    uncertainty_alpha: float = 0.12
    uncertainty_base: float = 0.5
    high_uncertainty_threshold: float = 0.7
    low_pe_threshold: float = 0.15
    high_pe_threshold: float = 0.50
    max_uncertainty_domains: int = 50

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PredictiveConfig:
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


# -----------------------------------------------------------------------
# Tone / response type inference (pure heuristics)
# -----------------------------------------------------------------------

_QUESTION_MARKERS = {"?", "how", "what", "why", "when", "where", "which", "who", "can", "could", "would", "should", "is", "are", "do", "does"}
_NEGATIVE_MARKERS = {"error", "bug", "wrong", "fail", "broken", "issue", "problem", "crash", "stuck", "help"}
_POSITIVE_MARKERS = {"thanks", "great", "perfect", "awesome", "nice", "love", "excellent", "good"}
_ACTION_MARKERS = {"run", "build", "create", "make", "write", "deploy", "install", "fix", "update", "delete", "remove"}


def _infer_tone(text: str) -> str:
    """Infer emotional tone from message text."""
    lower = text.lower()
    words = set(lower.split())
    neg_count = len(words & _NEGATIVE_MARKERS)
    pos_count = len(words & _POSITIVE_MARKERS)
    if neg_count > pos_count:
        return "negative"
    if pos_count > neg_count:
        return "positive"
    # Check for code/technical markers
    if any(c in text for c in "{}[]()=<>;`"):
        return "technical"
    return "neutral"


def _infer_response_type(text: str) -> str:
    """Infer what kind of response the user expects."""
    lower = text.lower()
    words = lower.split()
    first_words = set(words[:3]) if words else set()

    if "?" in text:
        return "question"
    if first_words & _ACTION_MARKERS:
        return "action"
    if len(words) < 5:
        return "follow_up"
    return "answer"


def _infer_length(text: str) -> str:
    """Infer expected response length from input."""
    words = text.split()
    if len(words) > 50:
        return "long"
    if len(words) < 10:
        return "short"
    return "medium"


# -----------------------------------------------------------------------
# PredictiveProcessor
# -----------------------------------------------------------------------

class PredictiveProcessor:
    """Predictive processing engine: predict, compare, learn from surprise.

    Forms expectations before each turn, computes prediction error after,
    and maintains per-domain uncertainty estimates.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.cfg = PredictiveConfig.from_dict(config or {})

        # Prediction history
        self._history: deque[Prediction] = deque(maxlen=self.cfg.history_size)
        self._current_prediction: Prediction | None = None

        # Per-domain uncertainty: domain -> uncertainty (0.0 = certain, 1.0 = clueless)
        self._uncertainty: dict[str, float] = {}

        # Cumulative PE stats
        self._total_pe: float = 0.0
        self._pe_count: int = 0
        self._surprise_count: int = 0  # turns with PE > high threshold

    # ------------------------------------------------------------------
    # Pre-Turn: Form Prediction
    # ------------------------------------------------------------------

    def predict(
        self,
        user_input: str,
        turn_number: int,
        recent_domains: list[str] | None = None,
        user_style: str = "",
        conv_temperature: float = 0.5,
    ) -> Prediction:
        """Form a prediction about the upcoming turn.

        Called BEFORE generation.  Uses pure heuristics from the message
        text, recent conversation domains, and user model.
        """
        pred = Prediction(
            turn_number=turn_number,
            expected_tone=_infer_tone(user_input),
            expected_response_type=_infer_response_type(user_input),
            expected_length=_infer_length(user_input),
        )

        # Domain prediction: most likely continues recent topic
        if recent_domains:
            pred.expected_domain = recent_domains[-1]
        else:
            # Infer from keywords (very rough)
            lower = user_input.lower()
            for kw, domain in [
                ("python", "technology.python"),
                ("javascript", "technology.javascript"),
                ("react", "technology.react"),
                ("api", "technology.api"),
                ("database", "technology.database"),
                ("deploy", "operations.deployment"),
                ("docker", "operations.docker"),
                ("test", "development.testing"),
            ]:
                if kw in lower:
                    pred.expected_domain = domain
                    break

        # Confidence: higher if we have history and consistent domains
        if self._history:
            recent_preds = list(self._history)[-5:]
            recent_pe = [p.prediction_error for p in recent_preds if p.prediction_error > 0]
            if recent_pe:
                avg_pe = sum(recent_pe) / len(recent_pe)
                # Low recent PE -> high confidence (we're tracking well)
                pred.confidence = _clamp(1.0 - avg_pe * 2.0, 0.2, 0.9)
            else:
                pred.confidence = 0.5
        else:
            pred.confidence = 0.3

        # Warm conversation -> more confident about continuation
        if conv_temperature > 0.6:
            pred.confidence = min(0.9, pred.confidence + 0.1)

        self._current_prediction = pred
        return pred

    # ------------------------------------------------------------------
    # Post-Turn: Compute Prediction Error
    # ------------------------------------------------------------------

    def compute_prediction_error(
        self,
        response: str,
        signals: list[dict[str, Any]] | None = None,
        actual_domain: str = "",
        valence: float = 0.0,
    ) -> float:
        """Compute prediction error after the turn completes.

        Called AFTER generation and ANS signal extraction.
        Returns the overall PE (0.0 = perfect prediction, 1.0 = max surprise).
        """
        if self._current_prediction is None:
            return 0.0

        pred = self._current_prediction

        # Infer actual properties from the response
        pred.actual_tone = _infer_tone(response) if response else "neutral"
        pred.actual_response_type = "answer"  # agent always answers
        pred.actual_length = _infer_length(response) if response else "short"
        pred.actual_domain = actual_domain or pred.expected_domain

        # Compute PE components
        pe_components: dict[str, float] = {}

        # Domain match: 0 if same, 0.5 if different prefix, 1.0 if completely different
        if pred.expected_domain and pred.actual_domain:
            exp_parts = pred.expected_domain.split(".")
            act_parts = pred.actual_domain.split(".")
            if exp_parts[0] == act_parts[0]:
                pe_components["domain"] = 0.0 if pred.expected_domain == pred.actual_domain else 0.3
            else:
                pe_components["domain"] = 0.8
        else:
            pe_components["domain"] = 0.2  # missing data = small PE

        # Tone match
        if pred.expected_tone == pred.actual_tone:
            pe_components["tone"] = 0.0
        elif (
            (pred.expected_tone == "negative" and pred.actual_tone == "positive")
            or (pred.expected_tone == "positive" and pred.actual_tone == "negative")
        ):
            pe_components["tone"] = 0.7  # opposite tone = big surprise
        else:
            pe_components["tone"] = 0.3

        # Length match
        length_map = {"short": 0, "medium": 1, "long": 2}
        exp_len = length_map.get(pred.expected_length, 1)
        act_len = length_map.get(pred.actual_length, 1)
        pe_components["length"] = abs(exp_len - act_len) * 0.3

        # Signal-based PE: unexpected signals increase PE
        if signals:
            unexpected_count = 0
            for sig in signals:
                sig_type = sig.get("type", "")
                if "UNKNOWN" in sig_type or "confus" in sig_type.lower():
                    unexpected_count += 1
            pe_components["signals"] = min(1.0, unexpected_count * 0.2)
        else:
            pe_components["signals"] = 0.0

        # Weighted combination
        weights = {"domain": 0.35, "tone": 0.25, "length": 0.10, "signals": 0.30}
        total_pe = sum(
            pe_components.get(k, 0.0) * w for k, w in weights.items()
        )
        total_pe = _clamp(total_pe, 0.0, 1.0)

        # Scale by confidence: high-confidence wrong predictions hurt more
        total_pe *= (0.5 + pred.confidence * 0.5)

        pred.prediction_error = round(total_pe, 4)
        pred.pe_components = {k: round(v, 3) for k, v in pe_components.items()}

        # Archive the prediction
        self._history.append(pred)
        self._current_prediction = None

        # Update stats
        self._total_pe += total_pe
        self._pe_count += 1
        if total_pe > self.cfg.high_pe_threshold:
            self._surprise_count += 1

        # Update domain uncertainty
        domain = pred.actual_domain or pred.expected_domain
        if domain:
            self._update_uncertainty(domain, total_pe)

        return total_pe

    # ------------------------------------------------------------------
    # Uncertainty Tracking
    # ------------------------------------------------------------------

    def _update_uncertainty(self, domain: str, pe: float) -> None:
        """Update per-domain uncertainty EMA from prediction error."""
        alpha = self.cfg.uncertainty_alpha
        current = self._uncertainty.get(domain, self.cfg.uncertainty_base)
        self._uncertainty[domain] = (1 - alpha) * current + alpha * pe

        # Trim to max domains
        if len(self._uncertainty) > self.cfg.max_uncertainty_domains:
            sorted_u = sorted(self._uncertainty.items(), key=lambda x: x[1])
            for k, _ in sorted_u[:len(self._uncertainty) - self.cfg.max_uncertainty_domains]:
                del self._uncertainty[k]

    def get_uncertainty(self, domain: str) -> float:
        """Return uncertainty for a domain (0.0 = certain, 1.0 = clueless)."""
        return self._uncertainty.get(domain, self.cfg.uncertainty_base)

    def get_high_uncertainty_domains(self) -> list[tuple[str, float]]:
        """Return domains where uncertainty is above threshold."""
        threshold = self.cfg.high_uncertainty_threshold
        return sorted(
            [(d, u) for d, u in self._uncertainty.items() if u >= threshold],
            key=lambda x: x[1], reverse=True,
        )

    # ------------------------------------------------------------------
    # Hormone Effects from Prediction Error
    # ------------------------------------------------------------------

    def compute_hormone_effects(self, pe: float) -> dict[str, float]:
        """Compute hormone adjustments based on prediction error.

        Large PE -> norepinephrine surge (surprise drives exploration).
        Small PE -> serotonin boost (prediction confirmed, stability).
        Very large PE -> cortisol bump (threat of model inadequacy).
        """
        effects: dict[str, float] = {}

        if pe > self.cfg.high_pe_threshold:
            # Big surprise: NE surge + slight cortisol
            effects["norepinephrine"] = pe * self.cfg.pe_norepinephrine_scale
            effects["cortisol"] = (pe - self.cfg.high_pe_threshold) * self.cfg.pe_cortisol_scale
        elif pe < self.cfg.low_pe_threshold:
            # Good prediction: serotonin boost
            effects["serotonin"] = (self.cfg.low_pe_threshold - pe) * self.cfg.pe_serotonin_scale
        else:
            # Moderate PE: mild NE
            effects["norepinephrine"] = pe * self.cfg.pe_norepinephrine_scale * 0.5

        return effects

    def apply_hormone_effects(
        self, hypothalamus: Any, effects: dict[str, float],
    ) -> None:
        """Apply PE-derived hormone adjustments to the hypothalamus."""
        if hypothalamus is None:
            return
        for hormone, delta in effects.items():
            try:
                h = hypothalamus.hormones.get(hormone)
                if h is not None:
                    h.level = _clamp(h.level + delta, 0.0, 1.0)
            except (AttributeError, KeyError):
                pass

    # ------------------------------------------------------------------
    # Active Inference Hints
    # ------------------------------------------------------------------

    def get_active_inference_hint(self) -> str:
        """Generate a prompt hint based on uncertainty landscape.

        Injected into the system prompt to guide the agent toward
        uncertainty-reducing responses.
        """
        parts: list[str] = []

        high_unc = self.get_high_uncertainty_domains()
        if high_unc:
            domains_str = ", ".join(d for d, _ in high_unc[:3])
            parts.append(
                f"You have high uncertainty in: {domains_str}. "
                "When relevant, seek clarification or explore these areas."
            )

        # Recent PE trend
        if self._history:
            recent = list(self._history)[-5:]
            recent_pe = [p.prediction_error for p in recent]
            avg_pe = sum(recent_pe) / len(recent_pe)
            if avg_pe > 0.4:
                parts.append(
                    "Recent prediction errors are high -- the conversation "
                    "is surprising you. Stay curious and adaptive."
                )
            elif avg_pe < 0.1 and len(recent) >= 3:
                parts.append(
                    "You're tracking well -- predictions are accurate. "
                    "Consider probing deeper or exploring adjacent topics."
                )

        if not parts:
            return ""
        return "[Predictive Awareness]\n" + "\n".join(f"• {p}" for p in parts)

    # ------------------------------------------------------------------
    # Context String for Prompt Injection
    # ------------------------------------------------------------------

    def get_context_string(self) -> str:
        """Render predictive context for prompt injection."""
        return self.get_active_inference_hint()

    # ------------------------------------------------------------------
    # Summary for Status API
    # ------------------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Return summary for get_status() / WebSocket."""
        avg_pe = (self._total_pe / self._pe_count) if self._pe_count > 0 else 0.0

        last_pred: dict[str, Any] | None = None
        if self._history:
            last = self._history[-1]
            last_pred = {
                "turn": last.turn_number,
                "pe": round(last.prediction_error, 3),
                "confidence": round(last.confidence, 2),
                "expected_domain": last.expected_domain,
                "actual_domain": last.actual_domain,
                "pe_components": last.pe_components,
            }

        return {
            "prediction_count": self._pe_count,
            "average_pe": round(avg_pe, 3),
            "surprise_count": self._surprise_count,
            "last_prediction": last_pred,
            "high_uncertainty_domains": [
                {"domain": d, "uncertainty": round(u, 3)}
                for d, u in self.get_high_uncertainty_domains()[:5]
            ],
        }

    # ------------------------------------------------------------------
    # Sleep Cycle
    # ------------------------------------------------------------------

    def on_sleep(self) -> None:
        """On sleep: uncertainty persists (it's learned experience)."""
        self._current_prediction = None

    def on_wake(self) -> None:
        """On wake: fresh prediction start, keep uncertainty."""
        pass

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist predictive processing state to disk."""
        path = Path(path)
        state = {
            "version": "1.0",
            "timestamp": time.time(),
            "total_pe": self._total_pe,
            "pe_count": self._pe_count,
            "surprise_count": self._surprise_count,
            "uncertainty": self._uncertainty,
            "history": [p.to_dict() for p in self._history],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load(self, path: str | Path) -> bool:
        """Load predictive processing state from disk."""
        path = Path(path)
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._total_pe = state.get("total_pe", 0.0)
            self._pe_count = state.get("pe_count", 0)
            self._surprise_count = state.get("surprise_count", 0)
            self._uncertainty = state.get("uncertainty", {})
            self._history = deque(
                (Prediction.from_dict(d) for d in state.get("history", [])),
                maxlen=self.cfg.history_size,
            )
            return True
        except (json.JSONDecodeError, OSError):
            return False
