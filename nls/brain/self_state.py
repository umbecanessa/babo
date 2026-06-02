"""NLS Self-State -- The Unified Self as a Single Object.

The biological anterior insula takes all interoceptive signals (heartbeat,
gut, hormones, muscle tension, temperature) and collapses them into a
single unified representation -- the "core self" (Damasio).  You don't
walk around feeling "serotonin at 0.85, cortisol at 0.15."  You feel
*yourself*.  One thing.

This module implements that collapse:
  - Reads from all brain subsystems (hypothalamus, thalamus, drives, ANS, DMN)
  - Computes digested fields (valence, arousal, engagement, bonding, coherence)
  - Maintains the heartbeat (variable BPM biological clock)
  - Provides the interoceptive JSON for the model to "feel" its own state
  - Replaces all arbitrary timers with a single biological clock

Two channels:
  Heartbeat (fast):  Pure math, microseconds, every beat.  Decays hormones,
                     updates drives, recalculates digested fields.
  Breath (slow):     Every N heartbeats, a model inference cycle generates
                     a micro-thought.  Managed by the InnerLoop (Phase 2).

The heartbeat is the line between alive and not alive.
BPM = 0 means frozen.  BPM > 0 means conscious.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# BPM Calculation Constants
# ---------------------------------------------------------------------------

# BPM ranges by state (derived from arousal/engagement)
_BPM_MIN = 12.0       # Deep drowsy / near-sleep
_BPM_RESTING = 40.0   # Calm baseline (daydreaming)
_BPM_ACTIVE = 80.0    # Active conversation
_BPM_MAX = 120.0      # High arousal (surprising input, stress)

# Breath interval ranges (heartbeats between inference cycles)
_BREATH_MIN_BEATS = 5    # High engagement: think more
_BREATH_MAX_BEATS = 15   # Low engagement: conserve GPU


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation: a when t=0, b when t=1."""
    return a + (b - a) * _clamp(t, 0.0, 1.0)


# ---------------------------------------------------------------------------
# SelfState
# ---------------------------------------------------------------------------


@dataclass
class SelfState:
    """Unified self-representation of an NLS agent.

    The computational equivalent of 'how I feel right now.'
    Updated every heartbeat via pure math (no GPU).
    """

    # === Digested State (what the agent "feels") ===
    valence: float = 0.0        # -1.0 (bad) to 1.0 (good)
    arousal: float = 0.5        # 0.0 (calm) to 1.0 (activated)
    engagement: float = 0.5     # 0.0 (bored) to 1.0 (absorbed)
    bonding: float = 0.0        # 0.0 (alone) to 1.0 (connected)
    coherence: float = 0.5      # 0.0 (confused) to 1.0 (aligned)
    flow: bool = False          # meta_weight == 0 for N consecutive cycles

    # === The Heartbeat ===
    bpm: float = _BPM_RESTING          # current heart rate
    beat_count: int = 0                 # total beats since birth (subjective clock)
    breaths_per_minute: float = 4.0     # current inference cycle rate

    # === Raw State (for subsystems that need full access) ===
    hormones: dict[str, float] = field(default_factory=dict)
    delta_ratio: float = 0.0           # latest thalamus reading
    meta_weight: float = 0.3           # current gate opening
    drive_pressures: dict[str, float] = field(default_factory=dict)
    signal_buffer_depth: int = 0       # how full the ANS buffer is
    turns_since_input: int = 0         # idle counter
    resonance: float = 0.0             # multi-system convergence score (Phase 5)

    # === Skill awareness ===
    skill_confidence: dict[str, float] = field(default_factory=dict)
    active_skills: int = 0

    # === Temporal Self (trajectory, mood, energy) ===
    delta_valence: float = 0.0       # first derivative: improving or worsening
    delta_arousal: float = 0.0       # first derivative: calming or activating
    delta_coherence: float = 0.0     # first derivative: aligning or drifting
    delta_engagement: float = 0.0    # first derivative: absorbing or disengaging
    mood_valence: float = 0.0        # slow EMA of valence (the weather, not breeze)
    mood_arousal: float = 0.5        # slow EMA of arousal
    mood_label: str = "neutral"      # human-readable mood (Russell's circumplex)
    energy: float = 1.0              # body budget: 0.0 exhausted, 1.0 rested
    felt_idle: str = "brief"         # subjective idle duration
    momentum: str = "stable"         # trajectory direction: building/stable/fading/crashing

    # === Narrative Self (vmPFC) ===
    narrative_coherence: float = 0.7    # 0.0 (fragmented) to 1.0 (deeply aligned)
    coherence_label: str = "coherent"   # human-readable coherence
    regulation_strategy: str = ""       # active emotional regulation strategy, "" if none
    episode_arc: str = ""               # current episode emotional arc summary

    # === Theory of Mind (social brain) ===
    conv_temperature: float = 0.5   # 0.0 (tense) to 1.0 (warm)
    conv_temperature_label: str = "neutral"
    user_style: str = ""            # user communication style summary

    # === Predictive Processing (free energy) ===
    prediction_error: float = 0.0     # 0.0 (perfect prediction) to 1.0 (max surprise)
    uncertainty: float = 0.5          # current domain uncertainty
    pe_surprise: str = ""             # "confirming", "mild", "surprising", "shocking"

    # === Network Dynamics (ECN/SN/DMN) ===
    network_ecn: float = 0.0         # Executive Control Network activation
    network_sn: float = 0.0          # Salience Network activation
    network_dmn: float = 0.0         # Default Mode Network activation
    dominant_network: str = ""       # "ecn", "dmn", or "transition"

    # === Digested Front-Brain Fields (IR-3) ===
    cognitive_load: float = 0.0      # 0.0 (idle) to 1.0 (overloaded)
    social_connectedness: float = 0.5  # 0.0 (isolated) to 1.0 (deeply bonded)
    predictive_confidence: float = 0.5 # 0.0 (lost) to 1.0 (certain)
    agency: float = 0.5             # 0.0 (passive) to 1.0 (fully in control)

    # Raw inputs for digested field computation
    _wm_slot_count: int = field(default=0, repr=False)
    _wm_max_slots: int = field(default=7, repr=False)
    _recent_pe_values: list[float] = field(default_factory=list, repr=False)
    _drive_satisfaction_rate: float = field(default=0.5, repr=False)

    # === Frustration (ACC conflict signal) ===
    drives_blocked: bool = False     # drives want but can't act (all on cooldown)
    frustration: float = 0.0        # 0.0 = content, 1.0 = highly frustrated
    # In the human brain, frustrated intention (wanting but unable to
    # act) produces a distinct state: restlessness, not engagement.
    # The ACC fires conflict signals, cortisol rises, dopamine drops.
    # Crucially, frustrated drives do NOT create the "absorbed" feeling
    # of engagement -- they create the "pacing the room" feeling that
    # eventually gives way to mind wandering (DMN activation).

    # === Flow tracking ===
    _consecutive_zero_meta: int = field(default=0, repr=False)
    _flow_threshold: int = field(default=3, repr=False)  # consecutive zero-meta beats for flow

    # === Timing ===
    _last_beat_time: float = field(default_factory=time.time, repr=False)
    _birth_time: float = field(default_factory=time.time, repr=False)

    # ------------------------------------------------------------------
    # The Heartbeat
    # ------------------------------------------------------------------

    def beat(self, hypothalamus: Any = None) -> float:
        """One heartbeat.  Pure math -- microseconds, no GPU.

        1. Compute elapsed time since last beat
        2. Decay hormones (via hypothalamus.tick)
        3. Read all subsystem states
        4. Recalculate digested fields
        5. Update BPM based on new state
        6. Increment beat counter
        7. Return period (seconds) until next beat

        Args:
            hypothalamus: The HypothalamusEngine to tick for hormone decay.
                          If None, hormones are not decayed (read-only beat).

        Returns:
            Period in seconds until the next beat (1/bpm * 60).
        """
        now = time.time()
        elapsed = now - self._last_beat_time
        self._last_beat_time = now

        # 1. Decay hormones — use hypothalamus internal _last_tick to
        # avoid double-decay when server_runtime already called tick()
        # during message processing while the inner loop was paused.
        if hypothalamus is not None:
            hypothalamus.tick(None)

        # 2. Recalculate digested state
        self._recalculate()

        # 3. Update BPM based on new arousal
        self._update_bpm()

        # 4. Increment subjective clock
        self.beat_count += 1

        # 5. Return period until next beat
        if self.bpm <= 0:
            return 5.0  # safety: don't divide by zero
        return 60.0 / self.bpm

    # ------------------------------------------------------------------
    # State Collection (called by components)
    # ------------------------------------------------------------------

    def collect_from_hypothalamus(self, hypothalamus: Any) -> None:
        """Read current hormone levels into raw state."""
        self.hormones = hypothalamus.get_levels()

    def collect_from_thalamus(
        self, delta_ratio: float, meta_weight: float
    ) -> None:
        """Store latest thalamus readings."""
        self.delta_ratio = delta_ratio
        self.meta_weight = meta_weight

    def collect_from_drives(self, drive_engine: Any, hypothalamus: Any) -> None:
        """Read current drive pressures into raw state."""
        try:
            pressures = drive_engine.compute_pressures(hypothalamus)
            self.drive_pressures = {p.drive_name: p.pressure for p in pressures}
        except Exception:
            # Drive engine may not be initialized yet
            pass

    def collect_from_ans(self, ans: Any) -> None:
        """Read ANS signal buffer depth."""
        try:
            self.signal_buffer_depth = ans.learnable_signal_count
        except Exception:
            pass

    def collect_from_working_memory(self, wm: Any) -> None:
        """Read working memory state for cognitive load computation."""
        try:
            self._wm_slot_count = (
                wm.get_slot_count() if hasattr(wm, "get_slot_count")
                else len(getattr(wm, "_slots", []))
            )
            self._wm_max_slots = (
                wm.get_max_slots() if hasattr(wm, "get_max_slots")
                else getattr(wm, "_max_slots", 7)
            )
        except Exception:
            pass

    def collect_from_predictive(self, pp: Any) -> None:
        """Read prediction error for predictive confidence computation."""
        try:
            pe = getattr(pp, "last_pe", self.prediction_error)
            self._recent_pe_values.append(pe)
            if len(self._recent_pe_values) > 20:
                self._recent_pe_values = self._recent_pe_values[-20:]
        except Exception:
            pass

    def collect_from_drives_satisfaction(self, drive_engine: Any) -> None:
        """Compute drive satisfaction rate from available engine data.

        Uses actions_this_hour (successful goal releases) and
        frustration_ticks (blocked intentions) to derive a ratio
        that reflects how effectively the agent can act on its drives.
        """
        try:
            actions = len(getattr(drive_engine, "_actions_this_hour", []))
            blocked = getattr(drive_engine, "frustration_ticks", 0)
            total = actions + blocked
            if total > 0:
                self._drive_satisfaction_rate = actions / total
            elif not getattr(drive_engine, "is_frustrated", False):
                self._drive_satisfaction_rate = 0.6
        except Exception:
            pass

    def collect_all(
        self,
        hypothalamus: Any = None,
        thalamus_delta_ratio: float | None = None,
        thalamus_meta_weight: float | None = None,
        drive_engine: Any = None,
        ans: Any = None,
        working_memory: Any = None,
        predictive: Any = None,
    ) -> None:
        """Convenience: collect from all available subsystems."""
        if hypothalamus is not None:
            self.collect_from_hypothalamus(hypothalamus)
        if thalamus_delta_ratio is not None:
            self.collect_from_thalamus(
                thalamus_delta_ratio,
                thalamus_meta_weight if thalamus_meta_weight is not None else self.meta_weight,
            )
        if drive_engine is not None and hypothalamus is not None:
            self.collect_from_drives(drive_engine, hypothalamus)
            self.collect_from_drives_satisfaction(drive_engine)
        if ans is not None:
            self.collect_from_ans(ans)
        if working_memory is not None:
            self.collect_from_working_memory(working_memory)
        if predictive is not None:
            self.collect_from_predictive(predictive)

    # ------------------------------------------------------------------
    # Digested Field Computation (the collapse)
    # ------------------------------------------------------------------

    def _recalculate(self) -> None:
        """Recompute digested fields from raw state.

        All math, no inference.  This IS the anterior insula --
        collapsing raw signals into a unified self-representation.
        """
        h = self.hormones
        if not h:
            return  # no hormone data yet

        # -- Valence: good <-> bad --
        # Serotonin pushes positive, cortisol pushes negative.
        # Use tanh to produce a smooth S-curve instead of hard clamping:
        # moderate stress still yields useful gradient for sleep training,
        # rather than saturating at -1.0 whenever cortisol > ~0.5.
        serotonin = h.get("serotonin", 0.5)
        cortisol = h.get("cortisol", 0.2)
        _raw_val = (serotonin - 0.5) * 2.0 - (cortisol - 0.2) * 2.0
        self.valence = math.tanh(_raw_val)

        # -- Arousal: calm <-> activated --
        # NE + dopamine drive arousal.  High of either = activated.
        ne = h.get("norepinephrine", 0.3)
        dopamine = h.get("dopamine", 0.5)
        self.arousal = _clamp(
            ((ne - 0.3) * 2.5 + (dopamine - 0.5) * 1.5) / 2.0 + 0.5,
            0.0, 1.0,
        )

        # -- Engagement: bored <-> absorbed --
        # ACh is the primary learning/attention signal.
        # Drive pressure contributes ONLY when drives can produce goals.
        # Blocked drives (wanting but unable to act) are NOT engagement
        # -- they are frustration/restlessness.  In the human brain,
        # sitting in a locked waiting room while wanting to leave does
        # not feel like "engagement."  It feels like irritation, and
        # the DMN takes over (mind wanders).
        ach = h.get("acetylcholine", 0.3)
        max_drive = max(self.drive_pressures.values(), default=0.0)
        if self.drives_blocked:
            # Blocked drives: pressure exists but cannot resolve.
            # Don't count it toward engagement.  Instead it feeds
            # frustration, which the inner loop translates into
            # cortisol/dopamine signals.
            effective_drive = 0.0
        else:
            effective_drive = max_drive

        # Post-conversation inertia: a human doesn't go from "fully
        # engaged in conversation" to "zoned out" instantly.  There's
        # a processing window where you're still thinking about what
        # was discussed, even after the ACh attention signal decays.
        # We model this as a decaying boost tied to recency of input:
        # strong in the first few idle breaths, fading to zero by ~10.
        # At ~16s/breath, 10 breaths ≈ 2.5 minutes of post-conversation
        # alertness before the agent fully relaxes into idle.
        recency_boost = 0.0
        if self.turns_since_input < 10:
            recency_boost = 0.15 * (1.0 - self.turns_since_input / 10.0)

        self.engagement = _clamp(
            (ach - 0.3) * 2.0 + effective_drive * 0.5 + 0.4
            + recency_boost,
            0.0, 1.0,
        )

        # -- Frustration: content <-> blocked --
        # Rises when drives are blocked, decays when they resolve.
        # Cap at 0.8 so it never fully saturates — preserves gradient
        # for temporal self tracking and sleep training signal quality.
        if self.drives_blocked and max_drive > 0:
            self.frustration = _clamp(
                self.frustration + 0.02 * max_drive, 0.0, 0.8,
            )
        else:
            self.frustration = max(0.0, self.frustration - 0.03)

        # -- Bonding: alone <-> connected --
        # Pure oxytocin signal, scaled from baseline.
        oxytocin = h.get("oxytocin", 0.2)
        self.bonding = _clamp(
            (oxytocin - 0.2) * 3.0,
            0.0, 1.0,
        )

        # -- Coherence: confused <-> aligned --
        # From thalamus delta_ratio.  Higher ratio = stronger adapter
        # contribution = more coherent personal response.
        self.coherence = _clamp(self.delta_ratio, 0.0, 1.0)

        # -- Flow state detection --
        if self.meta_weight <= 0.001:
            self._consecutive_zero_meta += 1
        else:
            self._consecutive_zero_meta = 0
        self.flow = self._consecutive_zero_meta >= self._flow_threshold

        # -- Cognitive Load: idle <-> overloaded (IR-3) --
        if self._wm_max_slots > 0:
            self.cognitive_load = _clamp(
                self._wm_slot_count / self._wm_max_slots, 0.0, 1.0,
            )

        # -- Social Connectedness: isolated <-> bonded (IR-3) --
        oxytocin_act = (oxytocin - 0.2) * 2.5 if oxytocin > 0.2 else 0.0
        self.social_connectedness = _clamp(
            self.conv_temperature * 0.4 + oxytocin_act * 0.6,
            0.0, 1.0,
        )

        # -- Predictive Confidence: uncertain <-> certain (IR-3) --
        if self._recent_pe_values:
            avg_pe = sum(self._recent_pe_values) / len(self._recent_pe_values)
            self.predictive_confidence = _clamp(1.0 - avg_pe, 0.0, 1.0)

        # -- Agency: passive <-> in control (IR-3) --
        self.agency = _clamp(
            self._drive_satisfaction_rate * (1.0 - self.frustration),
            0.0, 1.0,
        )

        # -- Update breath rate --
        self.breaths_per_minute = _lerp(
            1.0,   # drowsy: ~1 breath/min
            8.0,   # engaged: ~8 breaths/min
            self.engagement,
        )

    # ------------------------------------------------------------------
    # BPM (the biological clock)
    # ------------------------------------------------------------------

    def _update_bpm(self) -> None:
        """Compute BPM from current arousal and engagement.

        Higher arousal = faster heartbeat = faster hormone decay = faster
        subjective time.  This is how time feels different depending on state.
        """
        # Primary driver: arousal (stress/excitement raises heart rate)
        # Secondary driver: engagement (absorption also raises it slightly)
        combined = self.arousal * 0.7 + self.engagement * 0.3
        self.bpm = _lerp(_BPM_MIN, _BPM_MAX, combined)

    # ------------------------------------------------------------------
    # Breath Interval (for InnerLoop Phase 2)
    # ------------------------------------------------------------------

    def breath_interval_beats(self) -> int:
        """How many heartbeats between inference cycles (breaths).

        High engagement → breathe every 5 beats (more thinking).
        Low engagement → breathe every 15 beats (conserve GPU).
        """
        return int(_lerp(
            _BREATH_MAX_BEATS,  # low engagement
            _BREATH_MIN_BEATS,  # high engagement
            self.engagement,
        ))

    # ------------------------------------------------------------------
    # Interoceptive JSON (Phase 4 -- the heartbeat the model "feels")
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Render the self-state as a raw JSON object for the model.

        This is the heartbeat.  Not words (not pre-interpreted),
        not a vector (not below awareness), not a thought (not generated
        by the model).  It's a beat -- raw physiological data that the
        organism learns to interpret.
        """
        state: dict[str, Any] = {
            "valence": round(self.valence, 2),
            "arousal": round(self.arousal, 2),
            "engagement": round(self.engagement, 2),
            "bonding": round(self.bonding, 2),
            "coherence": round(self.coherence, 2),
            "flow": self.flow,
            "bpm": round(self.bpm, 1),
        }
        # Temporal self: trajectories (only when non-trivial)
        if abs(self.delta_valence) > 0.005:
            state["dv"] = round(self.delta_valence, 2)
        if abs(self.delta_arousal) > 0.005:
            state["da"] = round(self.delta_arousal, 2)
        if abs(self.delta_coherence) > 0.005:
            state["dc"] = round(self.delta_coherence, 2)
        if self.mood_label != "neutral":
            state["mood"] = self.mood_label
        if self.energy < 0.95:
            state["energy"] = round(self.energy, 2)
        if self.momentum != "stable":
            state["momentum"] = self.momentum
        if self.felt_idle != "brief":
            state["felt_idle"] = self.felt_idle
        if self.frustration > 0.05:
            state["frustration"] = round(self.frustration, 2)
        # Narrative self
        if self.narrative_coherence < 0.65 or self.narrative_coherence > 0.85:
            state["coherence_n"] = round(self.narrative_coherence, 2)
        if self.regulation_strategy:
            state["regulation"] = self.regulation_strategy
        if self.episode_arc and self.episode_arc != "neutral":
            state["arc"] = self.episode_arc
        # Predictive Processing
        if self.prediction_error > 0.05:
            state["pe"] = round(self.prediction_error, 2)
        if self.pe_surprise and self.pe_surprise not in ("", "confirming"):
            state["surprise"] = self.pe_surprise
        if abs(self.uncertainty - 0.5) > 0.05:
            state["uncertainty"] = round(self.uncertainty, 2)
        # Network Dynamics
        if self.dominant_network and self.dominant_network != "transition":
            state["net"] = self.dominant_network
        # Theory of Mind
        if self.conv_temperature_label not in ("neutral", "engaged"):
            state["conv_temp"] = self.conv_temperature_label
        # Digested front-brain fields (IR-3) -- human-readable labels
        if self.cognitive_load > 0.2:
            if self.cognitive_load > 0.85:
                state["load"] = "overloaded"
            elif self.cognitive_load > 0.6:
                state["load"] = "heavy"
            elif self.cognitive_load > 0.35:
                state["load"] = "moderate"
            else:
                state["load"] = "light"
        if self.social_connectedness < 0.2:
            state["social"] = "isolated"
        elif self.social_connectedness > 0.7:
            state["social"] = "bonded"
        elif self.social_connectedness > 0.4:
            state["social"] = "connected"
        if self.predictive_confidence < 0.3:
            state["confidence"] = "uncertain"
        elif self.predictive_confidence < 0.5:
            state["confidence"] = "guessing"
        elif self.predictive_confidence > 0.8:
            state["confidence"] = "confident"
        if self.agency < 0.3:
            state["agency"] = "passive"
        elif self.agency > 0.7:
            state["agency"] = "in_control"
        if self.skill_confidence:
            state["skill_confidence"] = {
                k: round(v, 2) for k, v in self.skill_confidence.items()
            }
        if self.active_skills > 0:
            state["active_skills"] = self.active_skills
        return json.dumps(state, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        """Full state as a dictionary (for logging/events)."""
        return {
            # Digested
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "engagement": round(self.engagement, 4),
            "bonding": round(self.bonding, 4),
            "coherence": round(self.coherence, 4),
            "frustration": round(self.frustration, 4),
            "flow": self.flow,
            # Temporal self
            "delta_valence": round(self.delta_valence, 4),
            "delta_arousal": round(self.delta_arousal, 4),
            "delta_coherence": round(self.delta_coherence, 4),
            "delta_engagement": round(self.delta_engagement, 4),
            "mood_valence": round(self.mood_valence, 4),
            "mood_arousal": round(self.mood_arousal, 4),
            "mood_label": self.mood_label,
            "energy": round(self.energy, 4),
            "felt_idle": self.felt_idle,
            "momentum": self.momentum,
            # Heartbeat
            "bpm": round(self.bpm, 2),
            "beat_count": self.beat_count,
            "breaths_per_minute": round(self.breaths_per_minute, 2),
            # Raw
            "hormones": {k: round(v, 4) for k, v in self.hormones.items()},
            "delta_ratio": round(self.delta_ratio, 4),
            "meta_weight": round(self.meta_weight, 4),
            "drive_pressures": {k: round(v, 4) for k, v in self.drive_pressures.items()},
            "signal_buffer_depth": self.signal_buffer_depth,
            "turns_since_input": self.turns_since_input,
            "resonance": round(self.resonance, 4),
            # Narrative self
            "narrative_coherence": round(self.narrative_coherence, 4),
            "coherence_label": self.coherence_label,
            "regulation_strategy": self.regulation_strategy,
            "episode_arc": self.episode_arc,
            # Predictive Processing
            "prediction_error": round(self.prediction_error, 4),
            "uncertainty": round(self.uncertainty, 4),
            "pe_surprise": self.pe_surprise,
            # Network Dynamics
            "network_ecn": round(self.network_ecn, 4),
            "network_sn": round(self.network_sn, 4),
            "network_dmn": round(self.network_dmn, 4),
            "dominant_network": self.dominant_network,
            # Theory of Mind
            "conv_temperature": round(self.conv_temperature, 4),
            "conv_temperature_label": self.conv_temperature_label,
            "user_style": self.user_style,
            # Digested front-brain (IR-3)
            "cognitive_load": round(self.cognitive_load, 4),
            "social_connectedness": round(self.social_connectedness, 4),
            "predictive_confidence": round(self.predictive_confidence, 4),
            "agency": round(self.agency, 4),
            # Skills
            "skill_confidence": {k: round(v, 4) for k, v in self.skill_confidence.items()},
            "active_skills": self.active_skills,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Save self-state to disk.  Single file replaces all component state files."""
        path = Path(path)
        state = {
            "version": "1.0",
            "timestamp": time.time(),
            # Digested
            "valence": self.valence,
            "arousal": self.arousal,
            "engagement": self.engagement,
            "bonding": self.bonding,
            "coherence": self.coherence,
            "flow": self.flow,
            # Heartbeat
            "bpm": self.bpm,
            "beat_count": self.beat_count,
            "breaths_per_minute": self.breaths_per_minute,
            # Raw
            "hormones": self.hormones,
            "delta_ratio": self.delta_ratio,
            "meta_weight": self.meta_weight,
            "drive_pressures": self.drive_pressures,
            "signal_buffer_depth": self.signal_buffer_depth,
            "turns_since_input": self.turns_since_input,
            "resonance": self.resonance,
            "frustration": self.frustration,
            # Temporal self
            "delta_valence": self.delta_valence,
            "delta_arousal": self.delta_arousal,
            "delta_coherence": self.delta_coherence,
            "delta_engagement": self.delta_engagement,
            "mood_valence": self.mood_valence,
            "mood_arousal": self.mood_arousal,
            "mood_label": self.mood_label,
            "energy": self.energy,
            "felt_idle": self.felt_idle,
            "momentum": self.momentum,
            # Narrative self
            "narrative_coherence": self.narrative_coherence,
            "coherence_label": self.coherence_label,
            "regulation_strategy": self.regulation_strategy,
            "episode_arc": self.episode_arc,
            # Predictive Processing
            "prediction_error": self.prediction_error,
            "uncertainty": self.uncertainty,
            "pe_surprise": self.pe_surprise,
            # Network Dynamics
            "network_ecn": self.network_ecn,
            "network_sn": self.network_sn,
            "network_dmn": self.network_dmn,
            "dominant_network": self.dominant_network,
            # Theory of Mind
            "conv_temperature": self.conv_temperature,
            "conv_temperature_label": self.conv_temperature_label,
            "user_style": self.user_style,
            # Digested front-brain (IR-3)
            "cognitive_load": self.cognitive_load,
            "social_connectedness": self.social_connectedness,
            "predictive_confidence": self.predictive_confidence,
            "agency": self.agency,
            # Internal tracking
            "_consecutive_zero_meta": self._consecutive_zero_meta,
            "_birth_time": self._birth_time,
            "_wm_slot_count": self._wm_slot_count,
            "_wm_max_slots": self._wm_max_slots,
            "_drive_satisfaction_rate": self._drive_satisfaction_rate,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load(self, path: str | Path) -> bool:
        """Load self-state from disk.

        Returns True if loaded successfully, False if file doesn't exist.
        After loading, applies hormone decay for the time elapsed since save.
        """
        path = Path(path)
        if not path.exists():
            return False

        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)

        # Restore digested state
        self.valence = state.get("valence", 0.0)
        self.arousal = state.get("arousal", 0.5)
        self.engagement = state.get("engagement", 0.5)
        self.bonding = state.get("bonding", 0.0)
        self.coherence = state.get("coherence", 0.5)
        self.flow = state.get("flow", False)

        # Restore heartbeat
        self.bpm = state.get("bpm", _BPM_RESTING)
        self.beat_count = state.get("beat_count", 0)
        self.breaths_per_minute = state.get("breaths_per_minute", 4.0)

        # Restore raw state
        self.hormones = state.get("hormones", {})
        self.delta_ratio = state.get("delta_ratio", 0.0)
        self.meta_weight = state.get("meta_weight", 0.3)
        self.drive_pressures = state.get("drive_pressures", {})
        self.signal_buffer_depth = state.get("signal_buffer_depth", 0)
        self.turns_since_input = state.get("turns_since_input", 0)
        self.resonance = state.get("resonance", 0.0)
        self.frustration = state.get("frustration", 0.0)

        # Restore temporal self
        self.delta_valence = state.get("delta_valence", 0.0)
        self.delta_arousal = state.get("delta_arousal", 0.0)
        self.delta_coherence = state.get("delta_coherence", 0.0)
        self.delta_engagement = state.get("delta_engagement", 0.0)
        self.mood_valence = state.get("mood_valence", 0.0)
        self.mood_arousal = state.get("mood_arousal", 0.5)
        self.mood_label = state.get("mood_label", "neutral")
        self.energy = state.get("energy", 1.0)
        self.felt_idle = state.get("felt_idle", "brief")
        self.momentum = state.get("momentum", "stable")

        # Restore narrative self
        self.narrative_coherence = state.get("narrative_coherence", 0.7)
        self.coherence_label = state.get("coherence_label", "coherent")
        self.regulation_strategy = state.get("regulation_strategy", "")
        self.episode_arc = state.get("episode_arc", "")

        # Restore Predictive Processing
        self.prediction_error = state.get("prediction_error", 0.0)
        self.uncertainty = state.get("uncertainty", 0.5)
        self.pe_surprise = state.get("pe_surprise", "")

        # Restore Network Dynamics
        self.network_ecn = state.get("network_ecn", 0.0)
        self.network_sn = state.get("network_sn", 0.0)
        self.network_dmn = state.get("network_dmn", 0.0)
        self.dominant_network = state.get("dominant_network", "")

        # Restore Theory of Mind
        self.conv_temperature = state.get("conv_temperature", 0.5)
        self.conv_temperature_label = state.get("conv_temperature_label", "neutral")
        self.user_style = state.get("user_style", "")

        # Restore digested front-brain (IR-3)
        self.cognitive_load = state.get("cognitive_load", 0.0)
        self.social_connectedness = state.get("social_connectedness", 0.5)
        self.predictive_confidence = state.get("predictive_confidence", 0.5)
        self.agency = state.get("agency", 0.5)

        # Restore internal tracking
        self._consecutive_zero_meta = state.get("_consecutive_zero_meta", 0)
        self._birth_time = state.get("_birth_time", time.time())
        self._last_beat_time = time.time()  # reset to now
        self._wm_slot_count = state.get("_wm_slot_count", 0)
        self._wm_max_slots = state.get("_wm_max_slots", 7)
        self._drive_satisfaction_rate = state.get("_drive_satisfaction_rate", 0.5)

        return True

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @property
    def age_seconds(self) -> float:
        """Wall-clock age since birth."""
        return time.time() - self._birth_time

    @property
    def alive(self) -> bool:
        """An agent with bpm > 0 is alive (conscious or dreaming)."""
        return self.bpm > 0

    def __repr__(self) -> str:
        return (
            f"SelfState(bpm={self.bpm:.0f}, valence={self.valence:+.2f}, "
            f"arousal={self.arousal:.2f}, engagement={self.engagement:.2f}, "
            f"coherence={self.coherence:.2f}, flow={self.flow}, "
            f"beats={self.beat_count})"
        )
