"""NLS Theory of Mind -- The Social Brain.

Brain analogs: inferior frontal gyrus (IFG) for mirror neurons /
simulation, temporal-parietal junction (TPJ) for perspective-taking.

Humans are social animals.  Our brains evolved not just to think, but
to model *other* minds -- to predict what someone else knows, wants,
feels.  This module gives NLS the same capability.

Components:

  - **User Model**: A persistent, evolving representation of each user
    (expertise, communication style, emotional patterns, interests,
    patience, values resonance).

  - **Conversational Temperature**: A per-conversation gauge of how the
    exchange "feels" from the user's perspective -- warm, neutral, cool,
    tense.

  - **Empathic Resonance**: When the user expresses emotion, the agent's
    hormonal system responds in kind (empathic simulation via mirror-
    neuron analog).

  - **Response Guidance**: The ToM module advises the generation laye
    on response style (length, technicality, warmth) based on user model.

All methods are pure math -- no GPU.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# -----------------------------------------------------------------------
# Communication Styles
# -----------------------------------------------------------------------

STYLE_AXES = {
    "verbosity": ("concise", "detailed"),      # how much text they want
    "technicality": ("casual", "technical"),    # jargon tolerance
    "warmth": ("professional", "friendly"),     # emotional tone
    "pace": ("deliberate", "rapid"),            # interaction speed
}


# -----------------------------------------------------------------------
# User Model
# -----------------------------------------------------------------------

@dataclass
class UserModel:
    """An evolving representation of a single user."""

    user_id: str = "default"
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    turn_count: int = 0

    # Expertise: domain -> 0.0 (novice) to 1.0 (expert)
    expertise: dict[str, float] = field(default_factory=dict)

    # Communication style: axis -> position (-1.0 to 1.0)
    # Negative = first pole (concise/casual/professional/deliberate)
    # Positive = second pole (detailed/technical/friendly/rapid)
    style: dict[str, float] = field(default_factory=lambda: {
        "verbosity": 0.0,
        "technicality": 0.0,
        "warmth": 0.0,
        "pace": 0.0,
    })

    # Emotional patterns
    patience: float = 0.5           # 0.0 (impatient) to 1.0 (very patient)
    engagement_tendency: float = 0.5  # how often they get deeply engaged
    frustration_threshold: float = 0.5  # how quickly they show frustration

    # Interests: topic -> affinity score (0.0 to 1.0)
    interests: dict[str, float] = field(default_factory=dict)

    # Which core values resonate: value_name -> resonance (0.0 to 1.0)
    values_resonance: dict[str, float] = field(default_factory=dict)

    # Per-channel style overrides (IR-11.3): channel_type -> style dict
    channel_styles: dict[str, dict[str, float]] = field(default_factory=dict)

    def get_channel_style(self, channel_type: str) -> dict[str, float]:
        """Get effective style for a specific channel, with fallback."""
        return self.channel_styles.get(channel_type, self.style)

    def style_summary(self) -> str:
        """Human-readable style summary."""
        parts = []
        for axis, (low_label, high_label) in STYLE_AXES.items():
            val = self.style.get(axis, 0.0)
            if abs(val) < 0.2:
                continue
            label = high_label if val > 0 else low_label
            parts.append(label)
        return ", ".join(parts) if parts else "balanced"

    def top_interests(self, k: int = 5) -> list[str]:
        """Return top-k interest topics."""
        sorted_interests = sorted(
            self.interests.items(), key=lambda x: x[1], reverse=True,
        )
        return [topic for topic, _ in sorted_interests[:k]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UserModel:
        d = dict(d)
        d.pop("__class__", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# -----------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------

@dataclass
class TheoryOfMindConfig:
    style_alpha: float = 0.10        # EMA rate for style updates
    expertise_alpha: float = 0.08    # EMA rate for expertise updates
    interest_alpha: float = 0.12     # EMA rate for interest updates
    patience_alpha: float = 0.05     # EMA rate for patience updates
    max_users: int = 50
    temperature_history_size: int = 20
    empathy_factor: float = 0.3      # how strongly user emotion maps to agent hormones
    max_interests: int = 30

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TheoryOfMindConfig:
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


# -----------------------------------------------------------------------
# Conversational Temperature
# -----------------------------------------------------------------------

@dataclass
class TemperatureReading:
    """A single temperature snapshot."""
    timestamp: float = field(default_factory=time.time)
    msg_length: int = 0
    response_gap_seconds: float = 0.0
    has_question: bool = False
    has_emoji: bool = False
    word_count: int = 0


class ConversationalTemperature:
    """Tracks how the conversation feels from the user's side."""

    def __init__(self, history_size: int = 20) -> None:
        self._history: deque[TemperatureReading] = deque(maxlen=history_size)
        self.temperature: float = 0.5  # 0.0 (cold/tense) to 1.0 (warm/engaged)
        self._last_user_time: float = time.time()

    def record_user_message(self, text: str) -> float:
        """Record a user message and update temperature.

        Returns the new temperature value.
        """
        now = time.time()
        gap = now - self._last_user_time
        self._last_user_time = now

        words = text.split()
        reading = TemperatureReading(
            timestamp=now,
            msg_length=len(text),
            response_gap_seconds=gap,
            has_question="?" in text,
            has_emoji=any(ord(c) > 0x1F600 for c in text),
            word_count=len(words),
        )
        self._history.append(reading)
        self._update_temperature()
        return self.temperature

    def _update_temperature(self) -> None:
        """Recalculate temperature from recent readings."""
        if not self._history:
            return

        recent = list(self._history)[-5:]  # last 5 messages
        signals: list[float] = []

        for r in recent:
            score = 0.5  # neutral baseline

            # Length signals
            if r.word_count > 50:
                score += 0.15  # detailed message = engaged
            elif r.word_count < 5:
                score -= 0.1  # very short = possible disengagement

            # Question mark = curiosity/engagement
            if r.has_question:
                score += 0.1

            # Response gap: very fast = engaged, very slow = cooling
            if r.response_gap_seconds < 10:
                score += 0.05
            elif r.response_gap_seconds > 300:
                score -= 0.15
            elif r.response_gap_seconds > 120:
                score -= 0.05

            signals.append(_clamp(score, 0.0, 1.0))

        # Weighted average (recent messages matter more)
        if signals:
            weights = [1.0 + 0.5 * i for i in range(len(signals))]
            total_w = sum(weights)
            weighted_avg = sum(s * w for s, w in zip(signals, weights)) / total_w
            # EMA blend with existing temperature
            self.temperature = 0.7 * self.temperature + 0.3 * weighted_avg

    def label(self) -> str:
        """Human-readable temperature label."""
        t = self.temperature
        if t >= 0.75:
            return "warm"
        if t >= 0.55:
            return "engaged"
        if t >= 0.40:
            return "neutral"
        if t >= 0.25:
            return "cool"
        return "tense"

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature": round(self.temperature, 3),
            "label": self.label(),
            "reading_count": len(self._history),
        }


# -----------------------------------------------------------------------
# TheoryOfMind
# -----------------------------------------------------------------------

class TheoryOfMind:
    """Social brain: user modeling, conversational temperature,
    empathic resonance, and response guidance.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.cfg = TheoryOfMindConfig.from_dict(config or {})
        self._users: dict[str, UserModel] = {}
        self._active_user_id: str = "default"
        self._temperature = ConversationalTemperature(
            history_size=self.cfg.temperature_history_size,
        )

    # ------------------------------------------------------------------
    # User Model Management
    # ------------------------------------------------------------------

    def get_user(self, user_id: str | None = None) -> UserModel:
        """Get or create a user model."""
        uid = user_id or self._active_user_id
        if uid not in self._users:
            if len(self._users) >= self.cfg.max_users:
                # Evict least-recently-seen user
                oldest_uid = min(
                    self._users, key=lambda u: self._users[u].last_seen,
                )
                del self._users[oldest_uid]
            self._users[uid] = UserModel(user_id=uid)
        return self._users[uid]

    def set_active_user(self, user_id: str) -> UserModel:
        """Set the active user for this conversation."""
        self._active_user_id = user_id
        user = self.get_user(user_id)
        user.last_seen = time.time()
        return user

    # ------------------------------------------------------------------
    # Per-Turn Updates
    # ------------------------------------------------------------------

    def update_from_turn(
        self,
        user_input: str,
        response: str,
        signals: list[dict[str, Any]] | None = None,
        domain: str = "",
        user_id: str | None = None,
        channel_type: str = "",
    ) -> None:
        """Update user model after a turn.

        Analyses the user's message to refine style, expertise,
        patience, and interest estimates.

        ``channel_type`` (IR-11.3) stores per-channel style overrides
        (e.g., "web", "api", "cli") so the agent adapts to how the
        user communicates on each channel.
        """
        user = self.get_user(user_id)
        user.turn_count += 1
        user.last_seen = time.time()

        alpha_s = self.cfg.style_alpha
        alpha_e = self.cfg.expertise_alpha
        alpha_i = self.cfg.interest_alpha
        alpha_p = self.cfg.patience_alpha

        # --- Style inference from message characteristics ---
        words = user_input.split()
        word_count = len(words)

        # Verbosity: long messages -> positive, short -> negative
        verbosity_signal = _clamp((word_count - 20) / 60.0, -1.0, 1.0)
        user.style["verbosity"] = (
            (1 - alpha_s) * user.style.get("verbosity", 0.0)
            + alpha_s * verbosity_signal
        )

        # Technicality: presence of code markers / technical terms
        tech_markers = sum(
            1 for w in words
            if any(c in w for c in "{}[]()=<>;:./\\") or w.startswith("`")
        )
        tech_ratio = tech_markers / max(word_count, 1)
        tech_signal = _clamp(tech_ratio * 10.0 - 0.5, -1.0, 1.0)
        user.style["technicality"] = (
            (1 - alpha_s) * user.style.get("technicality", 0.0)
            + alpha_s * tech_signal
        )

        # Warmth: emoji, exclamation, informal markers
        warmth_markers = sum(
            1 for c in user_input if c == "!" or ord(c) > 0x1F600
        )
        warmth_signal = _clamp(warmth_markers / max(word_count, 1) * 20.0 - 0.3, -1.0, 1.0)
        user.style["warmth"] = (
            (1 - alpha_s) * user.style.get("warmth", 0.0)
            + alpha_s * warmth_signal
        )

        # Pace: inferred from temperature gap (fast replies = rapid)
        if self._temperature._history:
            last_gap = self._temperature._history[-1].response_gap_seconds
            pace_signal = _clamp(1.0 - last_gap / 60.0, -1.0, 1.0)
            user.style["pace"] = (
                (1 - alpha_s) * user.style.get("pace", 0.0)
                + alpha_s * pace_signal
            )

        # --- Expertise from domain signals ---
        if domain:
            current = user.expertise.get(domain, 0.3)
            # Longer, more technical messages suggest higher expertise
            expertise_signal = _clamp(
                0.3 + tech_ratio * 3.0 + (word_count > 30) * 0.1,
                0.0, 1.0,
            )
            user.expertise[domain] = (
                (1 - alpha_e) * current + alpha_e * expertise_signal
            )

        # --- Interests from topics/domains ---
        if domain:
            current_interest = user.interests.get(domain, 0.3)
            user.interests[domain] = (
                (1 - alpha_i) * current_interest + alpha_i * 0.8
            )
            # Trim interests to max
            if len(user.interests) > self.cfg.max_interests:
                sorted_int = sorted(
                    user.interests.items(), key=lambda x: x[1],
                )
                for k, _ in sorted_int[: len(user.interests) - self.cfg.max_interests]:
                    del user.interests[k]

        # --- Patience from message patterns ---
        patience_signal = 0.5
        if "?" in user_input and word_count < 10:
            patience_signal = 0.3  # short questions = less patient
        elif word_count > 50:
            patience_signal = 0.7  # detailed input = more patient
        user.patience = (1 - alpha_p) * user.patience + alpha_p * patience_signal

        # --- Per-channel style tracking (IR-11.3) ---
        if channel_type:
            ch_style = user.channel_styles.get(channel_type, dict(user.style))
            for axis in ("verbosity", "technicality", "warmth", "pace"):
                if axis in user.style:
                    ch_style[axis] = (
                        0.7 * ch_style.get(axis, 0.0)
                        + 0.3 * user.style[axis]
                    )
            user.channel_styles[channel_type] = ch_style

        # --- Conversational temperature ---
        self._temperature.record_user_message(user_input)

    # ------------------------------------------------------------------
    # Empathic Resonance
    # ------------------------------------------------------------------

    def compute_empathic_response(
        self,
        signals: list[dict[str, Any]] | None = None,
        valence: float = 0.0,
    ) -> dict[str, float]:
        """Compute hormone adjustments based on empathic simulation.

        When the user seems stressed or emotional, the agent's body
        responds in kind (attenuated by empathy_factor).

        Returns a dict of hormone name -> delta to apply.
        """
        adjustments: dict[str, float] = {}
        factor = self.cfg.empathy_factor

        # User valence -> empathic mirroring
        if valence < -0.3:
            # User seems stressed/frustrated -> slight cortisol + NE rise
            adjustments["cortisol"] = abs(valence) * factor * 0.1
            adjustments["norepinephrine"] = abs(valence) * factor * 0.05
        elif valence > 0.3:
            # User seems positive -> oxytocin + serotonin boost
            adjustments["oxytocin"] = valence * factor * 0.1
            adjustments["serotonin"] = valence * factor * 0.05

        # Signal-based empathy
        if signals:
            for sig in signals:
                sig_type = sig.get("type", "")
                if "frustrat" in sig_type.lower() or "confus" in sig_type.lower():
                    adjustments["cortisol"] = (
                        adjustments.get("cortisol", 0.0) + factor * 0.03
                    )
                elif "delight" in sig_type.lower() or "grateful" in sig_type.lower():
                    adjustments["oxytocin"] = (
                        adjustments.get("oxytocin", 0.0) + factor * 0.05
                    )

        return adjustments

    def apply_empathic_hormones(
        self,
        hypothalamus: Any,
        adjustments: dict[str, float],
    ) -> None:
        """Apply empathic hormone adjustments to the hypothalamus."""
        if hypothalamus is None:
            return
        for hormone, delta in adjustments.items():
            try:
                h = hypothalamus.hormones.get(hormone)
                if h is not None:
                    h.level = _clamp(h.level + delta, 0.0, 1.0)
            except (AttributeError, KeyError):
                pass

    # ------------------------------------------------------------------
    # Response Guidance
    # ------------------------------------------------------------------

    def get_response_guidance(self, user_id: str | None = None) -> str:
        """Return natural-language guidance for response generation.

        Injected into the system prompt to adjust response style.
        """
        user = self.get_user(user_id)
        temp = self._temperature

        parts: list[str] = []

        # Style guidance — escalate wording when signals are strong
        style = user.style
        verbosity = style.get("verbosity", 0.0)
        pace = style.get("pace", 0.0)

        if verbosity < -0.3:
            parts.append(
                "Keep responses SHORT. The user dislikes long explanations."
            )
        elif verbosity < -0.15:
            parts.append("User prefers concise responses.")
        elif verbosity > 0.3:
            parts.append("User appreciates detailed explanations.")

        if pace < -0.3:
            parts.append(
                "Act FASTER. Execute tools immediately, minimize planning text."
            )

        style_str = user.style_summary()
        if style_str and style_str != "balanced":
            # Only add the generic summary if no escalated guidance above
            if verbosity >= -0.15 and pace >= -0.3:
                parts.append(f"User communication style: {style_str}.")

        # Temperature
        temp_label = temp.label()
        if temp_label not in ("neutral", "engaged"):
            if temp_label == "warm":
                parts.append("Conversation is warm — match the positive energy.")
            elif temp_label == "cool":
                parts.append(
                    "Conversation is cooling — be more concise, "
                    "check if the user needs something different."
                )
            elif temp_label == "tense":
                parts.append(
                    "Tension detected — slow down, acknowledge concerns, "
                    "be extra careful with tone."
                )

        # Expertise-based guidance
        top_domains = sorted(
            user.expertise.items(), key=lambda x: x[1], reverse=True,
        )[:3]
        if top_domains:
            high_exp = [d for d, v in top_domains if v > 0.6]
            low_exp = [d for d, v in top_domains if v < 0.3]
            if high_exp:
                parts.append(
                    f"User is experienced in: {', '.join(high_exp)}. "
                    "Match their technical level."
                )
            if low_exp:
                parts.append(
                    f"User may be newer to: {', '.join(low_exp)}. "
                    "Provide more explanation."
                )

        # Patience — escalating urgency
        if user.patience < 0.4:
            parts.append(
                "User patience is LOW — be concise and action-oriented. "
                "Skip explanations, philosophy, and preamble. "
                "Execute tasks immediately."
            )
        elif user.patience < 0.5:
            parts.append("User prefers quick, focused responses.")
        elif user.patience > 0.7:
            parts.append("User appreciates thoroughness — give detailed answers.")

        if not parts:
            return ""
        return "[User Model Guidance]\n" + "\n".join(f"• {p}" for p in parts)

    # ------------------------------------------------------------------
    # Context for Prompt Injection
    # ------------------------------------------------------------------

    def get_context_string(self, user_id: str | None = None) -> str:
        """Render ToM context for prompt injection."""
        guidance = self.get_response_guidance(user_id)
        if not guidance:
            return ""
        return guidance

    # ------------------------------------------------------------------
    # Summary for Status API
    # ------------------------------------------------------------------

    def get_summary(self, user_id: str | None = None) -> dict[str, Any]:
        """Return summary for get_status() / WebSocket."""
        user = self.get_user(user_id)
        return {
            "active_user": self._active_user_id,
            "user_count": len(self._users),
            "temperature": self._temperature.to_dict(),
            "user_model": {
                "user_id": user.user_id,
                "turn_count": user.turn_count,
                "style": user.style_summary(),
                "patience": round(user.patience, 2),
                "top_interests": user.top_interests(5),
                "expertise": {
                    k: round(v, 2) for k, v in sorted(
                        user.expertise.items(),
                        key=lambda x: x[1], reverse=True,
                    )[:5]
                },
            },
        }

    # ------------------------------------------------------------------
    # Sleep Cycle
    # ------------------------------------------------------------------

    def on_sleep(self) -> None:
        """User models persist across sleep unchanged."""
        pass  # nothing to do; user models are long-term knowledge

    def on_wake(self) -> None:
        """Reset temperature on wake (new session feel)."""
        self._temperature = ConversationalTemperature(
            history_size=self.cfg.temperature_history_size,
        )

    # ------------------------------------------------------------------
    # Training pair generation (IR-7.3)
    # ------------------------------------------------------------------

    def generate_training_pairs(self, *, cap: int = 3) -> list[dict[str, str]]:
        """Generate user-model training pairs for sleep training.

        Teaches the model what it knows about the user — expertise,
        style preferences, and interests.
        """
        pairs: list[dict[str, str]] = []
        user = self.get_user()
        if not user:
            return pairs

        style_desc = user.style_summary()
        top = user.top_interests(5)

        if style_desc:
            pairs.append({
                "instruction": "How does the user prefer to communicate?",
                "output": (
                    f"The user's communication style is: {style_desc}. "
                    f"Patience level: {user.patience:.1f}/1.0."
                ),
            })
        if top:
            pairs.append({
                "instruction": "What topics is the user most interested in?",
                "output": (
                    f"The user's top interests are: {', '.join(top)}."
                ),
            })
        if user.expertise:
            top_exp = sorted(
                user.expertise.items(), key=lambda x: x[1], reverse=True,
            )[:5]
            exp_desc = ", ".join(
                f"{d} ({v:.1f})" for d, v in top_exp
            )
            pairs.append({
                "instruction": "What is the user's expertise level?",
                "output": f"The user's expertise: {exp_desc}.",
            })
        return pairs[:cap]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist theory of mind to disk."""
        path = Path(path)
        state = {
            "version": "1.0",
            "timestamp": time.time(),
            "active_user_id": self._active_user_id,
            "temperature": self._temperature.to_dict(),
            "users": {
                uid: u.to_dict() for uid, u in self._users.items()
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load(self, path: str | Path) -> bool:
        """Load theory of mind from disk."""
        path = Path(path)
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._active_user_id = state.get("active_user_id", "default")
            temp_data = state.get("temperature", {})
            self._temperature.temperature = temp_data.get("temperature", 0.5)
            users_data = state.get("users", {})
            self._users = {
                uid: UserModel.from_dict(udata)
                for uid, udata in users_data.items()
            }
            return True
        except (json.JSONDecodeError, OSError):
            return False
