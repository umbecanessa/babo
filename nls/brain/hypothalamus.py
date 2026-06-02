"""NLS Hypothalamus -- System-Wide Hormonal State Engine.

The biological hypothalamus bridges the nervous system and the endocrine
system. It reads neural signals and produces hormones that affect the
ENTIRE body simultaneously. Hormones operate on a different timescale
than neurons: not millisecond per-query routing (thalamus), but
minute-to-hour system-wide state shifts.

This module implements a pharmacokinetic hormone engine:
  - Exponential half-life decay toward baseline
  - Sigmoid dose-response curves (non-linear activation)
  - Hormone interaction matrix (homeostasis feedback loops)
  - Config-driven: all hormone definitions loaded from JSON
  - Generic: the engine processes any hormone without code changes
  - Serializable: state persists across sessions

Three timescales in NLS:
  Thalamus:      milliseconds (per-query routing)
  Hypothalamus:  minutes      (session-level mood)
  Sleep cycle:   hours        (long-term consolidation)
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Default config path
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _MODULE_DIR / "config" / "hormones.json"


# ---------------------------------------------------------------------------
# Pydantic config models (loaded from JSON)
# ---------------------------------------------------------------------------


class DoseResponseConfig(BaseModel):
    """Configuration for the dose-response curve mapping raw hormone
    levels to effective behavioral impact."""

    curve: str = "sigmoid"
    steepness: float = 10.0
    midpoint: float = 0.5


class TriggerConfig(BaseModel):
    """A single trigger that produces a hormone."""

    magnitude: float = 1.0
    description: str = ""


class ThalamusEffect(BaseModel):
    """A single thalamus modifier produced by a hormone."""

    weight: float = 0.0
    description: str = ""


class HormoneDefinition(BaseModel):
    """Full declarative definition of a single hormone."""

    description: str = ""
    brain_analog: str = ""
    baseline: float = 0.5
    half_life_seconds: float = 3600.0
    production_rate: float = 0.1
    ceiling: float = 1.0
    floor: float = 0.0
    autoreceptor_gain: float = 0.0
    triggers: dict[str, TriggerConfig] = Field(default_factory=dict)
    thalamus_effects: dict[str, ThalamusEffect] = Field(default_factory=dict)


class InteractionDefinition(BaseModel):
    """Interaction between two hormones (how one affects another)."""

    source: str
    target: str
    strength: float = 0.0
    rectified: bool = True
    description: str = ""


class HypothalamusConfig(BaseModel):
    """Complete hypothalamus configuration loaded from JSON."""

    version: str = "1.0"
    description: str = ""
    dose_response: DoseResponseConfig = Field(default_factory=DoseResponseConfig)
    hormones: dict[str, HormoneDefinition] = Field(default_factory=dict)
    interactions: list[InteractionDefinition] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------


class HormoneState:
    """Runtime state for a single hormone with pharmacokinetic dynamics.

    Attributes:
        name:       Hormone identifier (matches config key).
        definition: The static config for this hormone.
        level:      Current hormone level (0.0 -- ceiling).
        last_update: Timestamp of last decay calculation.
    """

    __slots__ = ("name", "definition", "level", "last_update")

    def __init__(self, name: str, definition: HormoneDefinition) -> None:
        self.name = name
        self.definition = definition
        self.level: float = definition.baseline
        self.last_update: float = time.time()

    def produce(self, magnitude: float = 1.0) -> float:
        """Produce hormone in response to a trigger signal.

        Autoreceptor negative feedback: when the hormone is already above
        baseline, production rate scales down.  Models 5-HT1A (serotonin),
        D2 (dopamine), alpha-2 (NE), and GR (cortisol) autoreceptors that
        detect elevated levels and reduce further release.

        Negative magnitudes are supported for relief/recovery triggers
        (e.g. tool_success reducing cortisol).  The result is clamped to
        [floor, ceiling].

        Args:
            magnitude: Trigger-specific scaling factor (from config).
                       Positive = increase, negative = decrease.

        Returns:
            The new hormone level after production.
        """
        gain = self.definition.autoreceptor_gain
        if gain > 0:
            distance_above = max(0.0, self.level - self.definition.baseline)
            autoreceptor_scale = 1.0 / (1.0 + distance_above * gain)
        else:
            autoreceptor_scale = 1.0
        dose = self.definition.production_rate * magnitude * autoreceptor_scale
        self.level = max(
            self.definition.floor,
            min(self.definition.ceiling, self.level + dose),
        )
        self.last_update = time.time()
        return self.level

    def decay(self, elapsed_seconds: float) -> float:
        """Exponential decay toward baseline (pharmacokinetic model).

        Uses the half-life formula:
            level(t) = baseline + (level_0 - baseline) * 0.5^(t / half_life)

        The hormone level approaches its baseline exponentially. A hormone
        with a 1-hour half-life at level 1.0 (baseline 0.2) will be at
        0.6 after 1 hour and 0.4 after 2 hours.

        Args:
            elapsed_seconds: Time since last decay computation.

        Returns:
            The new hormone level after decay.
        """
        if elapsed_seconds <= 0 or self.definition.half_life_seconds <= 0:
            return self.level

        decay_factor = 0.5 ** (elapsed_seconds / self.definition.half_life_seconds)
        baseline = self.definition.baseline
        self.level = baseline + (self.level - baseline) * decay_factor
        # Clamp to valid range
        self.level = max(self.definition.floor, min(self.definition.ceiling, self.level))
        self.last_update = time.time()
        return self.level

    def dose_response(self, config: DoseResponseConfig) -> float:
        """Compute the effective behavioral impact via a non-linear curve.

        Uses a sigmoid (logistic) function that maps the raw hormone level
        to an effective activation between 0.0 and 1.0. This mirrors
        real pharmacological dose-response curves where:
          - Low levels have minimal effect
          - Mid-range has maximum sensitivity
          - High levels saturate

        Args:
            config: The dose-response configuration (steepness, midpoint).

        Returns:
            Effective activation (0.0 -- 1.0).
        """
        if config.curve == "sigmoid":
            x = (self.level - config.midpoint) * config.steepness
            # Clamp to prevent overflow in math.exp
            x = max(-20.0, min(20.0, x))
            return 1.0 / (1.0 + math.exp(-x))
        # Fallback: linear mapping (clamped)
        return max(0.0, min(1.0, self.level))

    def to_dict(self) -> dict[str, Any]:
        """Serialize hormone state for persistence."""
        return {
            "name": self.name,
            "level": self.level,
            "last_update": self.last_update,
        }

    def load_dict(self, data: dict[str, Any]) -> None:
        """Restore hormone state from serialized data."""
        self.level = data.get("level", self.definition.baseline)
        self.last_update = data.get("last_update", time.time())


# ---------------------------------------------------------------------------
# Hypothalamus engine
# ---------------------------------------------------------------------------


class HypothalamusEngine:
    """Generic pharmacokinetic hormone engine.

    Reads hormone definitions from a JSON config file and processes any
    hormone defined there without code changes. Adding a new hormone is
    adding a JSON entry -- zero code modifications.

    The engine provides three core operations:
      on_signal(signal_type)       -- route a behavioral tag to hormones
      tick(elapsed_seconds)        -- decay + apply interactions
      get_thalamus_modifiers()     -- aggregate hormonal effects for routing

    Example usage::

        engine = HypothalamusEngine.from_config("path/to/hormones.json")

        # During interaction
        engine.on_signal("EVALUATE:incorrect")   # stress rises
        engine.on_signal("LEARN")                # curiosity rises

        # Before thalamus routing
        engine.tick(elapsed_seconds=30.0)         # decay + interactions
        modifiers = engine.get_thalamus_modifiers()
        # modifiers["meta_weight_shift"] = +0.05 (cortisol boosting inner voice)

        # Persist across sessions
        engine.save_state("path/to/state.json")
        engine.load_state("path/to/state.json")
    """

    def __init__(self, config: HypothalamusConfig) -> None:
        self.config = config
        self.hormones: dict[str, HormoneState] = {
            name: HormoneState(name, defn)
            for name, defn in config.hormones.items()
        }
        # Pre-index: signal_type -> list of (hormone_name, trigger_config)
        self._trigger_index: dict[str, list[tuple[str, TriggerConfig]]] = {}
        for h_name, h_def in config.hormones.items():
            for trigger_key, trigger_cfg in h_def.triggers.items():
                self._trigger_index.setdefault(trigger_key, []).append(
                    (h_name, trigger_cfg)
                )
        self._last_tick: float = time.time()
        self._event_logger = None  # Set by runtime for research logging

        # --- Front-brain integration (IR-1) ---
        self._energy_level: float = 1.0
        self._mood_valence: float = 0.0
        self._mood_chronicity: float = 0.0
        self._mood_negative_beats: int = 0

    # --- Factory ---

    @classmethod
    def from_config(cls, config_path: str | Path | None = None) -> HypothalamusEngine:
        """Load hypothalamus from a JSON config file.

        Args:
            config_path: Path to hormones.json. If None, uses the default
                         config shipped with NLS.

        Returns:
            A configured HypothalamusEngine instance.
        """
        path = Path(config_path) if config_path else _DEFAULT_CONFIG
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        config = HypothalamusConfig(**raw)
        return cls(config)

    # --- Front-brain context (IR-1) ---

    def set_energy_level(self, energy: float) -> None:
        """Set the current body-budget energy from TemporalSelf.

        Low energy scales down all hormone production -- the body cannot
        produce hormones when exhausted.  Floor of 0.3 prevents total
        shutdown (even exhausted bodies still produce some cortisol).
        """
        self._energy_level = max(0.3, min(1.0, energy))

    def set_mood_context(self, mood_valence: float, beat_count: int = 1) -> None:
        """Update mood chronicity tracking from TemporalSelf.

        Chronic negative mood amplifies cortisol sensitivity and
        dampens dopamine sensitivity (anhedonia model).  Chronic
        positive mood does the reverse (resilience).
        """
        self._mood_valence = mood_valence
        if mood_valence < -0.3:
            self._mood_negative_beats += beat_count
        elif mood_valence > 0.1:
            self._mood_negative_beats = max(0, self._mood_negative_beats - beat_count * 2)
        # Chronicity ramps from 0→1 over ~50 sustained negative beats
        self._mood_chronicity = min(1.0, self._mood_negative_beats / 50.0)

    def anticipate(self, predicted_valence: float) -> dict[str, float]:
        """OFC anticipatory micro-doses before an action.

        Positive predicted outcome → dopamine micro-dose.
        Negative predicted outcome → cortisol micro-dose.
        Magnitude capped at 15% of normal production to prevent
        over-anticipation.
        """
        fired: dict[str, float] = {}
        if predicted_valence > 0.1:
            h = self.hormones.get("dopamine")
            if h:
                dose = h.definition.production_rate * 0.15 * min(predicted_valence, 1.0)
                h.level = min(h.definition.ceiling, h.level + dose)
                fired["dopamine"] = h.level
        elif predicted_valence < -0.1:
            h = self.hormones.get("cortisol")
            if h:
                dose = h.definition.production_rate * 0.10 * min(abs(predicted_valence), 1.0)
                h.level = min(h.definition.ceiling, h.level + dose)
                fired["cortisol"] = h.level
        return fired

    def gentle_reset(self) -> None:
        """Gentle hormone adjustment after a nap (low energy restoration).

        Moves hormones 50% toward baseline rather than snapping there.
        """
        for h in self.hormones.values():
            h.level = h.level + (h.definition.baseline - h.level) * 0.5
            h.level = max(h.definition.floor, min(h.definition.ceiling, h.level))

    def full_reset(self) -> None:
        """Aggressive hormone reset after a full sleep cycle.

        Snaps cortisol to baseline, boosts serotonin and dopamine above
        baseline (the "well-rested" bump).
        """
        for h in self.hormones.values():
            h.level = h.definition.baseline
        serotonin = self.hormones.get("serotonin")
        if serotonin:
            serotonin.level = min(serotonin.definition.ceiling,
                                  serotonin.definition.baseline + 0.15)
        dopamine = self.hormones.get("dopamine")
        if dopamine:
            dopamine.level = min(dopamine.definition.ceiling,
                                 dopamine.definition.baseline + 0.08)

    # --- Core operations ---

    def on_signal(self, signal_type: str) -> dict[str, float]:
        """Route a behavioral signal to all hormones that respond to it.

        The signal_type matches against trigger keys in the config. A
        signal like "EVALUATE:incorrect" triggers cortisol production.
        A signal like "LEARN" triggers norepinephrine.

        Partial matching is supported: "LEARN:User.Preferences.Editor"
        will match a trigger defined as "LEARN" (prefix match).

        Args:
            signal_type: The behavioral signal (e.g., "EVALUATE:correct",
                        "LEARN", "user_correction").

        Returns:
            Dict of {hormone_name: new_level} for all hormones that fired.
        """
        fired: dict[str, float] = {}
        energy_scale = self._energy_level

        # Direct match
        if signal_type in self._trigger_index:
            for h_name, trigger_cfg in self._trigger_index[signal_type]:
                new_level = self.hormones[h_name].produce(
                    trigger_cfg.magnitude * energy_scale,
                )
                fired[h_name] = new_level

        # Prefix match (e.g., "LEARN:domain.path" matches "LEARN" trigger)
        prefix = signal_type.split(":")[0] if ":" in signal_type else None
        if prefix and prefix != signal_type and prefix in self._trigger_index:
            for h_name, trigger_cfg in self._trigger_index[prefix]:
                if h_name not in fired:  # don't double-fire
                    new_level = self.hormones[h_name].produce(
                        trigger_cfg.magnitude * energy_scale,
                    )
                    fired[h_name] = new_level

        # Research logging
        if fired and self._event_logger is not None:
            self._event_logger.log_hormone_trigger(signal_type, fired)

        return fired

    def on_probe_signals(self, signal_vector: dict[str, float]) -> dict[str, float]:
        """Route V5 probe activations to hormone production.

        Unlike ``on_signal`` which takes a discrete signal type string,
        this accepts continuous probe activations (0.0–1.0) and maps
        them to hormone production using the configurable mappings from
        ``signal_probes.json``.

        Probe activations are scaled by their value — a LEARN activation
        of 0.9 produces more norepinephrine than 0.6.  Only activations
        above a minimum floor (0.3) trigger any hormone response to
        avoid constant low-level noise.

        Parameters
        ----------
        signal_vector : dict[str, float]
            Probe category -> activation (0.0–1.0).

        Returns
        -------
        dict[str, float]
            All hormones that changed, with new levels.
        """
        from .signal_probes import load_probe_config

        config = load_probe_config()
        hormone_mappings = config.get("hormone_mappings", {})
        activation_floor = 0.3

        all_fired: dict[str, float] = {}
        energy_scale = self._energy_level

        for probe_name, activation in signal_vector.items():
            if activation < activation_floor:
                continue

            mappings = hormone_mappings.get(probe_name, {})
            for hormone_name, base_magnitude in mappings.items():
                if hormone_name not in self.hormones:
                    continue

                # Scale magnitude by activation strength and energy
                magnitude = base_magnitude * activation * energy_scale
                new_level = self.hormones[hormone_name].produce(magnitude)
                all_fired[hormone_name] = new_level

        if all_fired and self._event_logger is not None:
            try:
                self._event_logger.log(
                    "probe_hormone_trigger",
                    signal_vector={
                        k: round(v, 3) for k, v in signal_vector.items()
                        if v > activation_floor
                    },
                    hormones_fired=all_fired,
                )
            except Exception:
                pass

        return all_fired

    def tick(self, elapsed_seconds: float | None = None) -> None:
        """Advance the hormonal system by elapsed time.

        Performs two operations:
          1. Decay all hormones toward their baselines (half-life model)
          2. Apply the interaction matrix (homeostasis feedback loops)

        If elapsed_seconds is None, uses wall-clock time since last tick.

        Args:
            elapsed_seconds: Time elapsed since last tick. None = auto.
        """
        now = time.time()
        if elapsed_seconds is None:
            elapsed_seconds = now - self._last_tick
        self._last_tick = now

        if elapsed_seconds <= 0:
            return

        # Snapshot before for logging
        levels_before = None
        if self._event_logger is not None:
            levels_before = {n: round(h.level, 4) for n, h in self.hormones.items()}

        # Phase 1: Decay all hormones
        for h in self.hormones.values():
            h.decay(elapsed_seconds)

        # Phase 2: Apply interaction matrix
        self._apply_interactions(elapsed_seconds)

        # Research logging
        if self._event_logger is not None and levels_before is not None:
            levels_after = {n: round(h.level, 4) for n, h in self.hormones.items()}
            self._event_logger.log_hormone_decay(
                elapsed_seconds, levels_before, levels_after,
            )

    def _apply_interactions(self, elapsed_seconds: float) -> None:
        """Apply hormone-hormone interactions (homeostasis feedback loops).

        For each interaction (source -> target):
          - Compute source hormone's effective activation (dose-response)
          - If activation > 0.5 (above midpoint), the interaction fires
          - The target hormone is nudged by the interaction strength

        Rectified interactions (default) only fire when the source hormone
        is above midpoint.  This prevents the inverse artifact where an
        inactive suppressor becomes an active booster (e.g. low cortisol
        pushing serotonin upward).  Set ``rectified=False`` in the config
        to allow bidirectional effects for specific interactions.

        Interactions are time-proportional: longer elapsed time means
        stronger cumulative effect. Normalized per minute.

        Mood-gated sensitivity (IR-1): chronic negative mood amplifies
        cortisol interactions and dampens dopamine interactions.
        """
        dr_config = self.config.dose_response
        dt_minutes = elapsed_seconds / 60.0
        chronicity = self._mood_chronicity

        for interaction in self.config.interactions:
            source = self.hormones.get(interaction.source)
            target = self.hormones.get(interaction.target)
            if not source or not target:
                continue

            # Source activation relative to midpoint (-0.5 to +0.5)
            source_activation = source.dose_response(dr_config) - 0.5

            # Rectified interactions: only fire when source is above midpoint.
            # In biology, absence of a suppressor (e.g. low cortisol) is not
            # the same as presence of a booster.  5-HT1A, D2, alpha-2
            # autoreceptors all follow this principle -- the downstream effect
            # requires active neurotransmitter presence, not its absence.
            if interaction.rectified and source_activation < 0:
                continue

            # Interaction force: strength * activation * time
            force = interaction.strength * source_activation * dt_minutes

            # Mood-gated sensitivity modulation
            if chronicity > 0.3:
                mood_mod = chronicity * 0.4
                if self._mood_valence < -0.3:
                    # Chronic stress: cortisol-targeted forces amplified,
                    # dopamine-targeted forces dampened
                    if target.name == "cortisol" and force > 0:
                        force *= (1.0 + mood_mod)
                    elif target.name == "dopamine" and force > 0:
                        force *= (1.0 - mood_mod)
                elif self._mood_valence > 0.3:
                    # Chronic positive: resilience (opposite)
                    if target.name == "cortisol" and force > 0:
                        force *= (1.0 - mood_mod * 0.5)
                    elif target.name == "dopamine" and force > 0:
                        force *= (1.0 + mood_mod * 0.5)

            target.level += force
            target.level = max(
                target.definition.floor,
                min(target.definition.ceiling, target.level),
            )

    def get_thalamus_modifiers(self) -> dict[str, float]:
        """Aggregate all hormone effects into thalamus routing modifiers.

        Each hormone contributes to modifier channels (e.g., "meta_weight_shift",
        "suppression_shift") weighted by its dose-response activation **relative
        to resting activation**. This ensures that at baseline levels, every
        hormone contributes exactly zero -- only deviations from the resting
        state produce thalamus modifiers.

        Returns:
            Dict of {modifier_name: total_value} for the thalamus to read.
            Typical channels:
              meta_weight_shift:  positive = boost inner voice, negative = reduce
              suppression_shift:  positive = tighter filtering, negative = looser
              exploration_bonus:  positive = lower routing thresholds
              confidence_boost:   positive = sharper generation
              trust_boost:        positive = less defensive behavior
        """
        dr_config = self.config.dose_response
        modifiers: dict[str, float] = {}

        for h in self.hormones.values():
            activation = h.dose_response(dr_config)
            # Compute the resting activation (dose-response at baseline)
            baseline_activation = self._baseline_activation(h.name, dr_config)
            # Only deviations from resting state produce modifiers
            effective = activation - baseline_activation

            for effect_name, effect_cfg in h.definition.thalamus_effects.items():
                contribution = effect_cfg.weight * effective
                modifiers[effect_name] = modifiers.get(effect_name, 0.0) + contribution

        return modifiers

    def _baseline_activation(self, hormone_name: str,
                             dr_config: DoseResponseConfig) -> float:
        """Compute dose-response activation at the hormone's resting baseline.

        Cached internally since baselines don't change at runtime.
        """
        if not hasattr(self, "_baseline_cache"):
            self._baseline_cache: dict[str, float] = {}
        if hormone_name not in self._baseline_cache:
            defn = self.config.hormones[hormone_name]
            # Temporarily compute sigmoid at baseline level
            if dr_config.curve == "sigmoid":
                x = (defn.baseline - dr_config.midpoint) * dr_config.steepness
                x = max(-20.0, min(20.0, x))
                self._baseline_cache[hormone_name] = 1.0 / (1.0 + math.exp(-x))
            else:
                self._baseline_cache[hormone_name] = max(0.0, min(1.0, defn.baseline))
        return self._baseline_cache[hormone_name]

    # --- State inspection ---

    def contribute_to_state(self, self_state: Any) -> None:
        """Write current hormone levels into the unified SelfState.

        Part of the SelfState collection protocol -- each brain component
        contributes its readings to the unified self-representation.
        """
        self_state.hormones = self.get_levels()

    def get_levels(self) -> dict[str, float]:
        """Return current hormone levels as a simple dict."""
        return {name: h.level for name, h in self.hormones.items()}

    def get_activations(self) -> dict[str, float]:
        """Return dose-response activations (effective behavioral impact)."""
        dr_config = self.config.dose_response
        return {name: h.dose_response(dr_config) for name, h in self.hormones.items()}

    def get_report(self) -> dict[str, dict[str, Any]]:
        """Return a full status report for all hormones.

        Includes raw level, effective activation, distance from baseline,
        and current thalamus contribution per hormone.
        """
        dr_config = self.config.dose_response
        report: dict[str, dict[str, Any]] = {}

        for name, h in self.hormones.items():
            activation = h.dose_response(dr_config)
            report[name] = {
                "level": round(h.level, 4),
                "baseline": h.definition.baseline,
                "delta_from_baseline": round(h.level - h.definition.baseline, 4),
                "activation": round(activation, 4),
                "half_life_seconds": h.definition.half_life_seconds,
                "thalamus_contributions": {
                    eff_name: round(eff_cfg.weight * (activation - 0.5), 4)
                    for eff_name, eff_cfg in h.definition.thalamus_effects.items()
                },
            }

        return report

    # --- Persistence ---

    def save_state(self, path: str | Path) -> None:
        """Serialize current hormonal state to JSON file.

        Saves hormone levels and timestamps so the agent's mood persists
        across sessions. The config is NOT saved -- only runtime state.

        Args:
            path: File path for the state JSON.
        """
        state = {
            "timestamp": time.time(),
            "hormones": {name: h.to_dict() for name, h in self.hormones.items()},
            "energy_level": self._energy_level,
            "mood_valence": self._mood_valence,
            "mood_chronicity": self._mood_chronicity,
            "mood_negative_beats": self._mood_negative_beats,
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load_state(self, path: str | Path) -> bool:
        """Restore hormonal state from a saved JSON file.

        After loading, applies decay for the time elapsed since the state
        was saved (the agent was "asleep" -- hormones still decay).

        Args:
            path: File path to the saved state JSON.

        Returns:
            True if state was loaded successfully, False if file doesn't exist.
        """
        p = Path(path)
        if not p.exists():
            return False

        with open(p, "r", encoding="utf-8") as f:
            state = json.load(f)

        saved_time = state.get("timestamp", time.time())
        elapsed_since_save = time.time() - saved_time

        for name, h_data in state.get("hormones", {}).items():
            if name in self.hormones:
                self.hormones[name].load_dict(h_data)

        # Restore front-brain context
        self._energy_level = state.get("energy_level", 1.0)
        self._mood_valence = state.get("mood_valence", 0.0)
        self._mood_chronicity = state.get("mood_chronicity", 0.0)
        self._mood_negative_beats = state.get("mood_negative_beats", 0)

        # Apply decay for time elapsed while the agent was offline
        if elapsed_since_save > 0:
            for h in self.hormones.values():
                h.decay(elapsed_since_save)

        self._last_tick = time.time()
        return True

    def reset(self) -> None:
        """Reset all hormones to their baseline levels."""
        for h in self.hormones.values():
            h.level = h.definition.baseline
            h.last_update = time.time()
        self._last_tick = time.time()

    # --- String representation ---

    def __repr__(self) -> str:
        levels = ", ".join(f"{n}={h.level:.2f}" for n, h in self.hormones.items())
        return f"HypothalamusEngine({levels})"
