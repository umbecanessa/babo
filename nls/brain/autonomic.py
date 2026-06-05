"""NLS Autonomic Nervous System -- The agent's unconscious regulatory system.

Maps to the human Autonomic Nervous System:

    Vagus Nerve          ->  Signal collector (intercepts every response)
    SCN / Circadian Clock ->  Sleep trigger (decides WHEN to sleep)
    Sympathetic Branch   ->  Emergency sleep mode (high cortisol, error-driven)
    Parasympathetic Branch -> Maintenance sleep mode (stable, growth-oriented)
    NREM Stage 1         ->  Triage (sort and prioritize signals)
    NREM Stage 3 (SWS)  ->  Consolidation (mine, train, add delta block)
    REM                  ->  Integration (merge deltas, recalibrate thalamus)

The ANS operates as a state machine:

    AWAKE  --(trigger)--> DROWSY --(begin)--> SLEEPING --(complete)--> WAKING --> AWAKE

It collects behavioral signals from every interaction, decides when to
sleep based on signal count + hormonal state, then orchestrates a
three-phase sleep pipeline that calls into the existing NLS mining
infrastructure (distiller, trainer, merger).

All parameters are config-driven via ``autonomic.json``. Adding new
trigger conditions or sleep phases means editing JSON, not code.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .circadian import CircadianClock, CircadianConfig, load_circadian_config

logger = logging.getLogger(__name__)


def _prepare_micro_inference(
    messages: list[dict],
    vllm_client: Any,
    *,
    adapter_name: str | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    from nls.runtime.inference_compat import prepare_micro_inference

    return prepare_micro_inference(
        messages,
        vllm_client=vllm_client,
        adapter_name=adapter_name,
    )


# ---------------------------------------------------------------------------
# Configuration models (loaded from autonomic.json)
# ---------------------------------------------------------------------------


class StateMachineConfig(BaseModel):
    """State machine transition rules."""

    states: list[str] = ["awake", "drowsy", "sleeping", "waking"]
    initial: str = "awake"
    drowsy_timeout_seconds: float = 30.0
    max_sleep_duration_seconds: float = 300.0


class HormoneModulationEntry(BaseModel):
    """How a single hormone modulates a sleep trigger threshold."""

    weight: float
    description: str = ""


class SignalCountTrigger(BaseModel):
    """Sleep trigger based on accumulated learnable signals.

    With circadian mode enabled, this no longer triggers sleep
    directly.  Instead it determines nap eligibility and how many
    nightly consolidation cycles to run.
    """

    base_threshold: int = 20
    role: str = "nap_eligibility"
    description: str = ""
    hormone_modulation: dict[str, HormoneModulationEntry] = Field(
        default_factory=dict
    )


class ErrorRateTrigger(BaseModel):
    """Emergency sleep trigger based on recent error rate."""

    window_turns: int = 30
    threshold: float = 0.90
    min_turns: int = 30
    description: str = ""


class SleepTriggers(BaseModel):
    """All conditions that can trigger a sleep cycle."""

    signal_count: SignalCountTrigger = Field(default_factory=SignalCountTrigger)
    error_rate: ErrorRateTrigger = Field(default_factory=ErrorRateTrigger)
    idle_timeout_seconds: float = 600.0
    periodic_seconds: float = 3600.0
    manual_trigger: bool = True
    description: str = ""


class TriageConfig(BaseModel):
    """Configuration for the triage (NREM Stage 1) phase."""

    description: str = ""
    priority_order: list[str] = [
        "error_correction",
        "new_knowledge",
        "behavior_reinforcement",
    ]
    max_signals_per_cycle: int = 150


class TrainingConfig(BaseModel):
    """Consolidation sleep hyperparameters (replay and fact merging)."""

    learning_rate: float = 5e-5
    epochs: int = 3
    batch_size: int = 2


class ConsolidationConfig(BaseModel):
    """Configuration for the consolidation (SWS) phase."""

    description: str = ""
    max_aku_per_cycle: int = 150
    training: TrainingConfig = Field(default_factory=TrainingConfig)
    merge_strategy: str = "weighted_ties"
    merge_after_n_deltas: int = 5


class IntegrationConfig(BaseModel):
    """Configuration for the integration (REM) phase."""

    description: str = ""
    recalibrate_thalamus: bool = True
    run_regression_check: bool = True
    regression_threshold: float = 0.10


class SleepPhasesConfig(BaseModel):
    """The three-phase sleep pipeline."""

    triage: TriageConfig = Field(default_factory=TriageConfig)
    consolidation: ConsolidationConfig = Field(default_factory=ConsolidationConfig)
    integration: IntegrationConfig = Field(default_factory=IntegrationConfig)


class PostSleepConfig(BaseModel):
    """Post-sleep (morning) routine configuration."""

    hormone_reset: dict[str, Any] = Field(default_factory=dict)
    clear_signal_buffer: bool = True
    generate_sleep_report: bool = True
    self_test_prompts: int = 5
    description: str = ""


class SignalCollectionConfig(BaseModel):
    """Configuration for the vagus nerve (signal collector)."""

    watched_signals: list[str] = [
        "LEARN",
        "UNKNOWN",
        "LOOKUP",
        "EVALUATE:correct",
        "EVALUATE:incorrect",
        "EVALUATE:uncertain",
        "EVALUATE",
        "ACC",
        "BONDING",
        "CLOSER",
        "FEELING",
        "INSULA",
        "AMYGDALA",
        "PFC",
        "COHERENCE",
        "CONNECT",
        "DOUBT",
        "REFLECT",
        "VALUES",
        # v3 operational signals
        "PLAN",
        "RECALL",
        "CHANNEL",
        "DELEGATE",
        "user_correction",
        "user_positive",
        "task_completed",
        "task_failed",
    ]
    buffer_max_size: int = 1000
    buffer_persistence: bool = True
    description: str = ""


class SleepModeThreshold(BaseModel):
    """Thresholds for determining sympathetic vs parasympathetic sleep."""

    cortisol_above_baseline: float = 0.50
    error_rate_above: float = 0.65
    description: str = ""


class SleepModeThresholds(BaseModel):
    """Configuration for sleep mode determination."""

    sympathetic: SleepModeThreshold = Field(default_factory=SleepModeThreshold)
    parasympathetic: SleepModeThreshold | None = None  # default mode


class CircadianConfigModel(BaseModel):
    """Circadian schedule configuration (loaded from autonomic.json)."""

    enabled: bool = True
    timezone: str = "UTC"
    bedtime: str = "00:00"
    wake_time: str = "08:00"
    nap_windows: list[dict[str, Any]] = Field(default_factory=list)
    wake_on_user_message: bool = True
    max_nightly_cycles: int = 5
    signal_pressure_cap_multiplier: float = 3.0
    description: str = ""


class AutonomicConfig(BaseModel):
    """Root configuration for the Autonomic Nervous System.

    Loaded from ``autonomic.json``. All behaviour is declarative --
    adding new trigger conditions or sleep phases means editing JSON.
    """

    version: str = "1.0"
    description: str = ""
    state_machine: StateMachineConfig = Field(
        default_factory=StateMachineConfig
    )
    signal_collection: SignalCollectionConfig = Field(
        default_factory=SignalCollectionConfig
    )
    circadian: CircadianConfigModel = Field(
        default_factory=CircadianConfigModel
    )
    sleep_triggers: SleepTriggers = Field(default_factory=SleepTriggers)
    sleep_phases: SleepPhasesConfig = Field(default_factory=SleepPhasesConfig)
    post_sleep: PostSleepConfig = Field(default_factory=PostSleepConfig)
    sleep_mode_thresholds: SleepModeThresholds = Field(
        default_factory=SleepModeThresholds
    )


# ---------------------------------------------------------------------------
# Runtime models
# ---------------------------------------------------------------------------


class AgentState(str, Enum):
    """The four states of the ANS state machine."""

    AWAKE = "awake"
    DROWSY = "drowsy"
    SLEEPING = "sleeping"
    WAKING = "waking"


class SleepMode(str, Enum):
    """Which branch of the ANS is driving the sleep cycle."""

    SYMPATHETIC = "sympathetic"  # Emergency: fix errors NOW
    PARASYMPATHETIC = "parasympathetic"  # Maintenance: consolidate and grow


class NerveSignal(BaseModel):
    """A single signal collected by the vagus nerve.

    Every behavioral tag extracted from a model response becomes a
    NerveSignal, timestamped and tagged with the hormonal state at
    the moment of collection. This gives the sleep pipeline maximum
    context for prioritization.
    """

    signal_type: str
    domain_path: str | None = None
    content: str | None = None
    pipe_fact: str | None = None  # Clean fact extracted from pipe format [LEARN:Domain|fact]
    meta_layer: str = ""  # Metacognitive layer: pfc_judgment, acc_epistemic, insula_comprehension, amygdala_affective, unclassified_emergent, base
    source: str = "user"  # Origin: "user" (conversation), "web" (Insula digest), "dmn" (Default Mode Network), "model" (self-generated)
    prompt: str = ""
    response: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    hormonal_snapshot: dict[str, float] = Field(default_factory=dict)
    turn_index: int = 0
    processed_at: datetime | None = None
    quality: float = 1.0  # Signal quality score (IR-4): 0.0 (unreliable) to ~3.0 (high quality)
    pe_at_collection: float = 0.0  # Prediction error at collection time (IR-7)
    episode_tag: str = ""  # Episode label at collection time (IR-7)


class SleepReport(BaseModel):
    """Report generated after a complete sleep cycle.

    Captures what happened during each phase, for observability
    and historical analysis.
    """

    sleep_mode: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: float

    # Triage
    total_signals_processed: int = 0
    signals_by_type: dict[str, int] = Field(default_factory=dict)
    signals_by_priority: dict[str, int] = Field(default_factory=dict)

    # Consolidation
    consolidation_summary: dict[str, Any] = Field(default_factory=dict)

    # Integration
    integration_summary: dict[str, Any] = Field(default_factory=dict)

    # Post-sleep
    hormones_reset: dict[str, float] = Field(default_factory=dict)

    # Front-brain metrics (IR-7.5)
    dreams_produced: int = 0
    narrative_episodes_consolidated: int = 0
    prediction_accuracy_before_sleep: float = 0.0
    energy_before: float = 1.0
    energy_after: float = 1.0
    resonance_peak: float = 0.0
    front_brain_training_pairs: int = 0


class TriagedSignals(BaseModel):
    """Signals organized by priority after the triage phase."""

    error_correction: list[NerveSignal] = Field(default_factory=list)
    new_knowledge: list[NerveSignal] = Field(default_factory=list)
    behavior_reinforcement: list[NerveSignal] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.error_correction)
            + len(self.new_knowledge)
            + len(self.behavior_reinforcement)
        )


# ---------------------------------------------------------------------------
# The Autonomic Nervous System Engine
# ---------------------------------------------------------------------------

# Regex for behavioral tags: [TAG:content], [TAG.content], or [TAG]
# Handles both colon syntax ([LEARN:User.Name]) and dot syntax ([ACC.Curious]).
# Mixed-case accepted (models sometimes emit [DOUBt:...], [Learn:...]);
# tag names are normalized to uppercase after capture.
_TAG_PATTERN = re.compile(r"\[([A-Za-z_]+)(?:[:.]([^\]]*))?\]")

# All recognized signal names for extraction and buffering.
# Includes standard signals + brain-component names the model may use as tags.
_KNOWN_SIGNAL_NAMES = frozenset({
    # Standard behavioral signals
    "LEARN", "EVALUATE", "LOOKUP", "UNKNOWN",
    "CONNECT", "DOUBT", "REFLECT", "VALUES",
    # v3 operational signals
    "PLAN", "RECALL", "CHANNEL", "DELEGATE",
    # Emotional/social signals
    "ACC", "BONDING", "CLOSER", "FEELING",
    # Brain-component signals (model sometimes uses these as top-level tags)
    "INSULA", "AMYGDALA", "PFC", "THALAMUS",
    "HYPOTHALAMUS", "HIPPOCAMPUS", "DMN",
    # Coherence monitor
    "COHERENCE",
})

# Core data signals used ONLY for nested-signal rejection.
# e.g. [EVALUATE:LEARN.Mythology] is rejected (LEARN nested inside EVALUATE),
# but [EVALUATE:ACC.Playful] is allowed (ACC is an emotional subtype, not a
# nested data signal).
_DATA_SIGNAL_NAMES = frozenset({
    "LEARN", "LOOKUP", "UNKNOWN",
})

# ---------------------------------------------------------------------------
# Signal taxonomy (loaded from signals.json, config-driven)
# ---------------------------------------------------------------------------

_SIGNALS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "signals.json"


def _load_signal_taxonomy(
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Load the four-layer metacognitive signal taxonomy from signals.json."""
    path = config_path or _SIGNALS_CONFIG_PATH
    if not path.exists():
        logger.warning("signals.json not found at %s, using defaults", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_signal_sets(
    taxonomy: dict[str, Any],
) -> tuple[frozenset, frozenset, frozenset, dict[str, str]]:
    """Build signal classification sets from the taxonomy config.

    Returns:
        (learnable_signals, error_signals, success_signals, signal_to_layer)
    """
    learnable = set()
    errors = set()
    successes = set()
    signal_to_layer: dict[str, str] = {}

    _BASE_LEARNABLE = frozenset({
        "LEARN", "UNKNOWN", "EVALUATE:incorrect", "user_correction",
        "REFLECT", "CONNECT", "DOUBT",
        "PLAN_CREATE", "PLAN_STEP", "DELEGATE_START",
        "CHANNEL_CONTEXT", "RECALL_MISS",
        "task_failed", "task_completed",
        "SKILL_INVOKE",
    })
    _BASE_ERRORS = frozenset({
        "EVALUATE:incorrect", "user_correction", "task_failed",
    })
    _BASE_SUCCESSES = frozenset({
        "EVALUATE:correct", "user_positive", "task_completed",
        "RECALL_HIT", "SKILL_INVOKE",
    })

    # Base signals
    base = taxonomy.get("base_signals", {})
    for sig in base.get("signals", []):
        if sig in _BASE_LEARNABLE:
            learnable.add(sig)
        if sig in _BASE_ERRORS:
            errors.add(sig)
        if sig in _BASE_SUCCESSES:
            successes.add(sig)
        signal_to_layer[sig] = "base"

    # Taxonomy layers
    layers = taxonomy.get("layers", {})
    for layer_name, layer_cfg in layers.items():
        # Collect all signals from this layer
        all_sigs = list(layer_cfg.get("signals", []))
        all_sigs.extend(layer_cfg.get("signals_positive", []))
        all_sigs.extend(layer_cfg.get("signals_negative", []))

        for sig in all_sigs:
            signal_to_layer[sig] = layer_name

        # Learnable
        if layer_cfg.get("is_learnable", False):
            learnable.update(all_sigs)

        # Error / success classification
        is_error = layer_cfg.get("is_error", {})
        is_success = layer_cfg.get("is_success", {})

        for sig, val in is_error.items():
            if val:
                errors.add(sig)
        for sig, val in is_success.items():
            if val:
                successes.add(sig)

    return frozenset(learnable), frozenset(errors), frozenset(successes), signal_to_layer


# Load taxonomy at module level
_SIGNAL_TAXONOMY = _load_signal_taxonomy()
_LEARNABLE_SIGNALS, _ERROR_SIGNALS, _SUCCESS_SIGNALS, _SIGNAL_TO_LAYER = (
    _build_signal_sets(_SIGNAL_TAXONOMY)
)


class AutonomicNervousSystem:
    """The agent's unconscious regulatory system.

    Manages the full wake-sleep lifecycle:

    1. **Vagus Nerve** -- intercepts every response, extracts signals
    2. **SCN** -- decides when to sleep (hormone-modulated thresholds)
    3. **Sleep Pipeline** -- triage -> consolidate -> integrate
    4. **Morning Routine** -- reset hormones, clear buffer, self-test

    Usage::

        ans = AutonomicNervousSystem(config_path=Path("autonomic.json"))

        # During interaction loop:
        signals = ans.on_response(prompt, response, hypothalamus)
        should_sleep, reason = ans.check_sleep_trigger(hypothalamus)

        if should_sleep:
            report = ans.full_sleep_cycle(hypothalamus)
    """

    def __init__(
        self,
        config_path: Path | None = None,
        config: AutonomicConfig | None = None,
        taxonomy: "TaxonomySeed | None" = None,
    ):
        if config is not None:
            self.config = config
        elif config_path is not None:
            self.config = self._load_config(config_path)
        else:
            self.config = AutonomicConfig()

        # Domain taxonomy seed for classification guidance
        self._taxonomy = taxonomy

        # State machine
        self._state = AgentState.AWAKE
        self._state_entered_at = datetime.utcnow()

        # Signal buffer (the vagus nerve's collected impulses)
        self._signal_buffer: list[NerveSignal] = []
        self._turn_counter: int = 0

        # Sleep tracking
        self._last_sleep_at: datetime | None = None
        self._last_interaction_at: datetime | None = None
        self._sleep_reports: list[SleepReport] = []
        self._current_sleep_start: datetime | None = None
        self._current_sleep_mode: SleepMode | None = None
        self._current_triaged: TriagedSignals | None = None

        # Error tracking (sliding window for error_rate trigger)
        self._recent_errors: list[bool] = []  # True = error, False = success

        # Event logger (set by runtime for research logging)
        self._event_logger = None

        # ── Recent task memory (cross-turn context) ──
        self._recent_tasks: list[dict[str, Any]] = []
        self._max_recent_tasks: int = 50

        # ── Streak tracking (modulates hormonal confidence) ──
        self._success_streak: int = 0
        self._failure_streak: int = 0

        # Keys of LEARN facts already sent to UI (safety_net_learned dedup).
        self._ui_broadcast_learn_keys: dict[str, None] = {}

        # ── Safety net state (set by on_response, consumed by runtime) ──
        self._last_turn_needs_safety_net: bool = False
        self._last_turn_prompt: str = ""
        self._last_turn_response: str = ""
        self._last_turn_is_agentic: bool = False

        # ── V5 Signal Probe state ──
        self._probe_unknown_detected: bool = False
        self._probe_unknown_prompt: str = ""

        # ── Voluntary sleep (conscious napping) ──
        self._voluntary_sleep_requested: bool = False
        self._voluntary_sleep_reason: str = ""

        # ── Suppressed domains (user-deleted signals, expire after N cycles) ──
        self._suppressed_domains: dict[str, int] = {}  # domain -> remaining cycles

        # ── v3 RECALL:miss self-healing ──
        self._recall_miss_domains: set[str] = set()

        # ── Circadian clock ──
        circ_dict = {}
        if hasattr(self.config, "circadian") and self.config.circadian:
            circ_dict = self.config.circadian.model_dump()
        self.circadian = CircadianClock(load_circadian_config(
            {"circadian": circ_dict},
        ))

        # ── Emotional sensing (last result exposed for frontend broadcast) ──
        self._last_emotion_result: dict[str, float] = {}

        # ── Plan fact validator (set by runtime) ──
        # Callable() -> Plan | None; returns the active plan for
        # cross-referencing LLM-extracted progress/step facts.
        self._plan_fact_validator: Any | None = None

        # ── Front-brain integration (IR-4) ──
        self._current_pe: float = 0.0
        self._current_energy: float = 1.0
        self._sustained_high_load_turns: int = 0
        self._last_resonance_peak: float = 0.0
        self._episode_tag: str = ""
        self._tom_interests: list[str] = []
        self._tom_ref: Any = None
        self._wm_ref: Any = None

    @staticmethod
    def _load_config(path: Path) -> AutonomicConfig:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = AutonomicConfig(**data)

        # Enforce minimum triage caps so older per-agent configs don't
        # silently bottleneck the training pipeline.
        _MIN_SIGNALS_PER_CYCLE = 150
        triage = cfg.sleep_phases.triage
        if triage.max_signals_per_cycle < _MIN_SIGNALS_PER_CYCLE:
            logger.warning(
                "autonomic.json triage cap %d < minimum %d — upgrading",
                triage.max_signals_per_cycle, _MIN_SIGNALS_PER_CYCLE,
            )
            triage.max_signals_per_cycle = _MIN_SIGNALS_PER_CYCLE

        return cfg

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def state(self) -> AgentState:
        """Current state of the ANS state machine."""
        return self._state

    @property
    def is_awake(self) -> bool:
        return self._state == AgentState.AWAKE

    @property
    def is_sleeping(self) -> bool:
        return self._state in (AgentState.DROWSY, AgentState.SLEEPING)

    @property
    def signal_count(self) -> int:
        """Total signals in the buffer."""
        return len(self._signal_buffer)

    @property
    def learnable_signal_count(self) -> int:
        """Signals that count toward the sleep trigger threshold."""
        return sum(
            1
            for s in self._signal_buffer
            if s.signal_type in _LEARNABLE_SIGNALS
        )

    @property
    def turn_count(self) -> int:
        return self._turn_counter

    @property
    def error_rate(self) -> float:
        """Current error rate in the sliding window."""
        if not self._recent_errors:
            return 0.0
        return sum(self._recent_errors) / len(self._recent_errors)

    def decay_error_rate(self) -> None:
        """Inject success entries to decay a persisted high error rate.

        Called at the start of each explicit user-initiated or scheduled turn.
        Historical errors (e.g. from a failed vLLM connection) should not
        prevent the agent from attempting a new task.  We insert
        floor(window/4) False entries — enough to bring a 100% rate below the
        80% threshold in a 20-turn window — without fully clearing the history.
        """
        window = self.config.sleep_triggers.error_rate.window_turns
        decay_count = max(1, window // 4)
        self._recent_errors.extend([False] * decay_count)
        # Keep within window
        if len(self._recent_errors) > window:
            self._recent_errors = self._recent_errors[-window:]
        if decay_count:
            logger.debug(
                "ANS decay_error_rate: injected %d success entries "
                "(error_rate now %.0f%%)",
                decay_count, self.error_rate * 100,
            )

    def record_task_success(self) -> None:
        """Award success credit after completing a multi-step task.

        Called after a non-aborted agentic loop finishes (user turn, scheduler
        check-back, delegate wrap-up).  Injects floor(window/2) success
        entries — twice as many as decay_error_rate — rewarding the agent for
        completing real work.  This prevents the error window from staying
        saturated from earlier connection failures and triggering sleep
        immediately after a productive session.
        """
        window = self.config.sleep_triggers.error_rate.window_turns
        credit = max(2, window // 2)
        self._recent_errors.extend([False] * credit)
        if len(self._recent_errors) > window:
            self._recent_errors = self._recent_errors[-window:]
        logger.debug(
            "ANS record_task_success: injected %d success credits "
            "(error_rate now %.0f%%)",
            credit, self.error_rate * 100,
        )

    @property
    def sleep_reports(self) -> list[SleepReport]:
        return list(self._sleep_reports)

    @property
    def signal_buffer(self) -> list[NerveSignal]:
        """Read-only view of the signal buffer."""
        return list(self._signal_buffer)

    # -------------------------------------------------------------------
    # Front-brain integration (IR-4)
    # -------------------------------------------------------------------

    def set_brain_refs(
        self,
        theory_of_mind: Any = None,
        working_memory: Any = None,
    ) -> None:
        """Store references to brain components needed by crystallization."""
        if theory_of_mind is not None:
            self._tom_ref = theory_of_mind
        if working_memory is not None:
            self._wm_ref = working_memory

    def set_front_brain_context(
        self,
        prediction_error: float = 0.0,
        energy: float = 1.0,
        cognitive_load: float = 0.0,
        resonance: float = 0.0,
        episode_tag: str = "",
        tom_interests: list[str] | None = None,
    ) -> None:
        """Update front-brain context for PE-gated decisions and sleep triggers."""
        self._current_pe = prediction_error
        self._current_energy = energy
        self._episode_tag = episode_tag or self._episode_tag
        if tom_interests is not None:
            self._tom_interests = tom_interests
        if cognitive_load > 0.8:
            self._sustained_high_load_turns += 1
        else:
            self._sustained_high_load_turns = max(0, self._sustained_high_load_turns - 1)
        if resonance > self._last_resonance_peak:
            self._last_resonance_peak = resonance

    def compute_signal_quality(
        self,
        signal: NerveSignal,
        domain_db: Any = None,
    ) -> float:
        """Compute multi-factor quality score for a signal.

        quality = novelty * confidence * corroboration * source_weight * pe_relevance
        """
        # Novelty: is this new information or already known?
        novelty = 1.0
        if domain_db is not None and signal.domain_path:
            try:
                existing = domain_db.get_fact(signal.domain_path)
                if existing:
                    novelty = 0.5  # re-encounter, lower novelty
            except Exception:
                pass

        # Confidence: low cortisol = more reliable learning
        cortisol = signal.hormonal_snapshot.get("cortisol", 0.2)
        confidence = 1.0 - min(cortisol, 0.8) * 0.5  # 0.6-1.0

        # Source weight
        source_weights = {"user": 1.0, "web": 0.8, "dmn": 0.6, "model": 0.7}
        source_w = source_weights.get(signal.source, 0.7)

        # PE relevance: high PE at collection = important turn
        pe_relevance = 0.7 + signal.pe_at_collection * 0.6

        return round(novelty * confidence * source_w * pe_relevance, 3)

    @property
    def should_skip_safety_net(self) -> bool:
        """PE-gated safety net: skip when PE is low (routine conversation).

        Always fire for the first 3 turns — PE is near-zero early on
        (no prediction history) which would otherwise suppress the
        safety net precisely when the agent is learning the most.

        With tagger OFF, the safety net is the sole collection path.
        Relaxed PE gate: only skip at very low PE AND non-agentic turns.
        Agentic turns (tool calls, code analysis) always run the safety
        net because their content is the richest knowledge the agent
        processes, even though PE is near-zero (routine tool calls).
        """
        if self._turn_counter <= 3:
            return False
        if getattr(self, "_last_turn_is_agentic", False):
            return False
        return self._current_pe < 0.02

    @property
    def should_force_safety_net(self) -> bool:
        """PE-gated safety net: force when PE is high (surprising turn)."""
        return self._current_pe > 0.5

    # -------------------------------------------------------------------
    # The Vagus Nerve: Signal Collection
    # -------------------------------------------------------------------

    def on_response(
        self,
        prompt: str,
        response: str,
        hypothalamus: Any | None = None,
        *,
        is_agentic: bool = False,
    ) -> list[NerveSignal]:
        """Intercept a response and extract behavioral signals.

        This is the **vagus nerve** -- it monitors every interaction
        and feeds signals into the buffer.

        Args:
            prompt: The user's prompt.
            response: The model's response.
            hypothalamus: Optional ``HypothalamusEngine`` for hormonal
                snapshot and signal forwarding.
            is_agentic: True when called from the agentic loop. Disables
                the PE gate on the safety net since agentic tool results
                are rich in knowledge but have near-zero PE.

        Returns:
            List of ``NerveSignal`` extracted from this response.
        """
        self._last_turn_is_agentic = is_agentic
        if not self.is_awake:
            logger.debug(
                "on_response called while agent is %s (signals discarded)",
                self._state.value,
            )
            return []

        self._turn_counter += 1
        self._last_interaction_at = datetime.utcnow()

        # Get hormonal snapshot
        hormonal_snapshot: dict[str, float] = {}
        if hypothalamus is not None:
            hormonal_snapshot = {
                name: h.level for name, h in hypothalamus.hormones.items()
            }

        # Extract signals from response text (tag-based, from adapter)
        signals = self._extract_signals(prompt, response, hormonal_snapshot)

        # ANS safety net flag.
        #
        # V5 mode (signal probes): on_probe_signals() sets the flag directly
        # when LEARN probe fires.  Text-tag extraction is a fallback.
        #
        # Legacy mode (behavior adapter): if the adapter didn't emit any
        # LEARN signals, mark the turn so the runtime can schedule an async
        # LLM micro-call to catch what the adapter missed.
        has_learn = any(
            s.signal_type == "LEARN"
            and (s.pipe_fact or s.content)
            and len((s.pipe_fact or s.content or "").strip()) > 5
            for s in signals
        )

        # In V5 mode, on_probe_signals may have already set the flag.
        # Don't override it if probes already triggered extraction.
        if not getattr(self, "_last_turn_needs_safety_net", False):
            self._last_turn_needs_safety_net = (
                not has_learn and bool(prompt) and len(prompt) > 2
            )
        # Always store prompt/response for extraction if safety net is needed.
        # With tagger OFF, the safety net is the sole collection path —
        # expand truncation to 1500 chars to capture agentic tool results.
        if self._last_turn_needs_safety_net:
            self._last_turn_prompt = prompt[:1500]
            self._last_turn_response = response[:1500]
        elif not has_learn:
            self._last_turn_prompt = ""
            self._last_turn_response = ""

        # Track errors / successes for the error_rate trigger
        has_error = any(s.signal_type in _ERROR_SIGNALS for s in signals)
        has_success = any(s.signal_type in _SUCCESS_SIGNALS for s in signals)
        if has_error:
            self._recent_errors.append(True)
        elif has_success:
            self._recent_errors.append(False)

        # Trim sliding window
        window = self.config.sleep_triggers.error_rate.window_turns
        if len(self._recent_errors) > window:
            self._recent_errors = self._recent_errors[-window:]

        # Enrich signals with front-brain context (IR-4/IR-5)
        for sig in signals:
            sig.pe_at_collection = self._current_pe
            sig.episode_tag = self._episode_tag
            sig.quality = self.compute_signal_quality(sig, domain_db=None)

        # Content-similarity dedup: skip LEARN signals that are nearly
        # identical to what's already in the buffer for the same domain.
        # Must run BEFORE _reconcile_signals which removes the old one.
        _filtered: list = []
        for sig in signals:
            if sig.signal_type == "LEARN" and sig.domain_path and sig.content:
                _existing = next(
                    (s for s in self._signal_buffer
                     if s.signal_type == "LEARN"
                     and s.domain_path == sig.domain_path
                     and s.content),
                    None,
                )
                if _existing:
                    _a = set(sig.content.lower().split())
                    _b = set(_existing.content.lower().split())
                    _denom = max(len(_a), len(_b), 1)
                    if len(_a & _b) / _denom > 0.85:
                        continue
            _filtered.append(sig)
        signals = _filtered

        # Dedup: remove existing LEARN signals with same domain before adding
        learn_domains = [
            s.domain_path for s in signals
            if s.signal_type == "LEARN" and s.domain_path
        ]
        if learn_domains:
            self._reconcile_signals([], learn_domains)

        # v3 RECALL:miss self-healing — when model fails to recall a
        # consolidated fact, flag it for re-injection and reconsolidation.
        # The domain comes from the co-occurring LOOKUP signal (the model
        # emits [LOOKUP:Domain.Path] [RECALL:miss] together).
        recall_miss_detected = any(
            s.signal_type == "RECALL:miss" for s in signals
        )
        if recall_miss_detected:
            lookup_domains = [
                s.domain_path for s in signals
                if s.signal_type == "LOOKUP" and s.domain_path
            ]
            for domain in lookup_domains:
                self._handle_recall_miss(domain)

        # Add signals to buffer
        self._signal_buffer.extend(signals)

        # Enforce max buffer size (keep newest)
        max_size = self.config.signal_collection.buffer_max_size
        if len(self._signal_buffer) > max_size:
            self._signal_buffer = self._signal_buffer[-max_size:]

        # Forward signals to hypothalamus (hormonal production)
        if hypothalamus is not None:
            for sig in signals:
                hypothalamus.on_signal(sig.signal_type)

        logger.info(
            "Vagus: +%d signals from turn %d (buffer: %d, error_rate: %.0f%%)",
            len(signals),
            self._turn_counter,
            self.signal_count,
            self.error_rate * 100,
        )

        # Research logging
        if self._event_logger is not None:
            for sig in signals:
                self._event_logger.log_signal_collected(
                    signal_type=sig.signal_type,
                    domain_path=sig.domain_path or "",
                    content=sig.content or "",
                    turn=self._turn_counter,
                    hormonal_snapshot=hormonal_snapshot,
                    meta_layer=sig.meta_layer,
                )

        # Cross-turn pattern detection (Phase 10): analyze buffer for
        # multi-turn patterns after this turn's signals are added.
        pattern_signals = self.detect_cross_turn_patterns(
            hypothalamus=hypothalamus,
        )
        if pattern_signals:
            signals.extend(pattern_signals)

        return signals

    # ------------------------------------------------------------------
    # V5 Signal Probe integration
    # ------------------------------------------------------------------

    def on_probe_signals(
        self,
        signal_vector: dict[str, float],
        prompt: str,
        response: str,
        hypothalamus: Any | None = None,
        *,
        mid_generation: bool = False,
    ) -> None:
        """Process signal activations from V5 neural probes.

        This is the primary signal pathway in V5 — probes detect
        signal-worthy moments from hidden states, then this method
        triggers the appropriate ANS responses:

        - LEARN > threshold -> run safety net LLM extraction for facts
        - UNKNOWN > threshold -> create pending_unknown drive
        - EVAL_NEGATIVE > threshold -> error tracking + cortisol
        - All activations -> hypothalamus for hormone updates

        Parameters
        ----------
        signal_vector : dict[str, float]
            Probe activations (0.0-1.0) per category.
        prompt : str
            User prompt for this turn.
        response : str
            Model's generated response.
        hypothalamus : HypothalamusEngine | None
            For hormone production.
        mid_generation : bool
            If True, this is a streaming checkpoint during generation.
            Accumulate signals and forward to hypothalamus for continuous
            hormone updates, but defer extraction until generation ends.
        """
        if not signal_vector:
            return

        # Mid-generation: accumulate peak activations, update hormones, defer extraction
        if mid_generation:
            if not hasattr(self, "_mid_gen_peak_signals"):
                self._mid_gen_peak_signals: dict[str, float] = {}
            for k, v in signal_vector.items():
                if v > self._mid_gen_peak_signals.get(k, 0.0):
                    self._mid_gen_peak_signals[k] = v
            if hypothalamus is not None:
                hypothalamus.on_probe_signals(signal_vector)
            return

        # Final (post-generation) call: merge any accumulated mid-gen peaks
        if hasattr(self, "_mid_gen_peak_signals") and self._mid_gen_peak_signals:
            for k, v in self._mid_gen_peak_signals.items():
                if v > signal_vector.get(k, 0.0):
                    signal_vector[k] = v
            self._mid_gen_peak_signals = {}

        from .signal_probes import load_probe_config, fired_signals

        config = load_probe_config()
        thresholds = config.get("thresholds", {})
        fired = fired_signals(signal_vector, thresholds)

        logger.info(
            "V5 probe signals (all): %s",
            {k: f"{v:.3f}" for k, v in sorted(signal_vector.items(), key=lambda x: -x[1])},
        )
        logger.info("V5 probe fired: %s", fired)

        # LEARN -> always schedule safety net extraction (the primary pathway)
        if "LEARN" in fired:
            self._last_turn_needs_safety_net = True
            self._last_turn_prompt = prompt[:1500]
            self._last_turn_response = response[:1500]

        # UNKNOWN -> flag for drives
        if "UNKNOWN" in fired:
            self._probe_unknown_detected = True
            self._probe_unknown_prompt = prompt[:300]

        # EVAL_NEGATIVE -> error tracking
        if "EVAL_NEGATIVE" in fired:
            self._recent_errors.append(True)
        elif "EVAL_POSITIVE" in fired:
            self._recent_errors.append(False)

        # Trim sliding window
        window = self.config.sleep_triggers.error_rate.window_turns
        if len(self._recent_errors) > window:
            self._recent_errors = self._recent_errors[-window:]

        # Forward all probe activations to hypothalamus
        if hypothalamus is not None:
            hypothalamus.on_probe_signals(signal_vector)

        # Research logging
        if self._event_logger is not None:
            try:
                self._event_logger.log_event(
                    "probe_signals",
                    {
                        "signal_vector": signal_vector,
                        "fired": fired,
                        "turn": self._turn_counter,
                    },
                )
            except Exception:
                pass

    @property
    def probe_unknown_detected(self) -> bool:
        """Whether the V5 probes detected an UNKNOWN signal this turn."""
        return getattr(self, "_probe_unknown_detected", False)

    def consume_probe_unknown(self) -> str:
        """Consume the probe UNKNOWN detection (called by drives)."""
        self._probe_unknown_detected = False
        prompt = getattr(self, "_probe_unknown_prompt", "")
        self._probe_unknown_prompt = ""
        return prompt

    # ------------------------------------------------------------------
    # v3 RECALL:miss self-healing
    # ------------------------------------------------------------------

    def _handle_recall_miss(self, domain_path: str) -> None:
        """Flag a domain for full re-injection after a RECALL:miss signal.

        When the model emits [RECALL:miss], it means it couldn't recall
        a fact from trained weights despite the hint.  We:
        1. Add the domain to ``_recall_miss_domains`` so the next
           preflight injection sends the full value.
        2. Mark the fact for reconsolidation priority in the next sleep.
        """
        self._recall_miss_domains.add(domain_path)
        logger.info(
            "RECALL:miss for %s — flagged for re-injection and "
            "reconsolidation",
            domain_path,
        )

    def get_recall_miss_domains(self) -> set[str]:
        """Return domains that need full re-injection (consumed by runtime)."""
        return set(self._recall_miss_domains)

    def clear_recall_miss(self, domain: str | None = None) -> None:
        """Clear RECALL:miss flag after re-injection or sleep consolidation."""
        if domain is None:
            self._recall_miss_domains.clear()
        else:
            self._recall_miss_domains.discard(domain)

    # ------------------------------------------------------------------
    # Visual Cortex integration
    # ------------------------------------------------------------------

    _VISUAL_ERROR_KEYWORDS = frozenset({
        "error", "failed", "exception", "traceback", "fatal", "crash",
        "denied", "timeout", "not found", "404", "500",
        "errore", "fallito", "negato",
    })
    _VISUAL_SUCCESS_KEYWORDS = frozenset({
        "success", "passed", "completed", "enabled", "done", "saved",
        "abilitata", "attivata", "riuscito", "salvato",
    })

    def on_visual_event(
        self,
        event: Any,
        hypothalamus: Any | None = None,
    ) -> list["NerveSignal"]:
        """Generate ANS signals from a visual cortex event.

        Called by the visual cortex's thalamic gating pipeline when
        an event is deemed relevant.  Maps visual observations to
        behavioral signals that feed into the hormone system.
        """
        if not self.is_awake:
            return []

        signals: list[NerveSignal] = []
        text = (
            (getattr(event, "description", "") or "")
            + " "
            + (getattr(event, "ocr_text", "") or "")
        ).lower()

        hormonal_snapshot: dict[str, float] = {}
        if hypothalamus is not None:
            hormonal_snapshot = {
                name: h.level for name, h in hypothalamus.hormones.items()
            }

        # Error detected on screen
        if any(kw in text for kw in self._VISUAL_ERROR_KEYWORDS):
            sig = NerveSignal(
                signal_type="EVALUATE:visual_error",
                content=f"Visual error detected: {getattr(event, 'description', '')[:200]}",
                source="visual",
                hormonal_snapshot=hormonal_snapshot,
            )
            signals.append(sig)

        # Success detected on screen
        if any(kw in text for kw in self._VISUAL_SUCCESS_KEYWORDS):
            sig = NerveSignal(
                signal_type="EVALUATE:visual_success",
                content=f"Visual success: {getattr(event, 'description', '')[:200]}",
                source="visual",
                hormonal_snapshot=hormonal_snapshot,
            )
            signals.append(sig)

        # Forward to hormone system
        if hypothalamus is not None:
            for sig in signals:
                hypothalamus.on_signal(sig.signal_type)

        if signals:
            self._signal_buffer.extend(signals)
            logger.debug(
                "Visual ANS: +%d signals (buffer: %d)",
                len(signals), self.signal_count,
            )

        return signals

    def inject_signal(
        self,
        signal_type: str,
        domain_path: str | None = None,
        content: str | None = None,
        hypothalamus: Any | None = None,
        source: str = "user",
        prompt: str = "",
        response: str = "",
    ) -> NerveSignal:
        """Manually inject a signal into the buffer.

        Used for external signals like ``user_correction`` or
        ``user_positive`` that don't come from model response parsing.
        Also forwards the signal to the hypothalamus if provided.

        Args:
            source: Origin of the signal — ``"user"`` (conversation),
                ``"web"`` (Insula/web search), ``"dmn"`` (Default Mode
                Network), ``"model"`` (self-generated).  Used by the
                Neural Eraser to decide trust polarity.
            prompt: The user prompt that triggered this signal (optional).
                Carried through to sleep training for training-pair
                generation from behavior_reinforcement signals.
            response: The agent response associated with this signal
                (optional).  Same purpose as *prompt*.

        Returns:
            The created ``NerveSignal``.
        """
        hormonal_snapshot: dict[str, float] = {}
        if hypothalamus is not None:
            hormonal_snapshot = {
                name: h.level for name, h in hypothalamus.hormones.items()
            }

        signal = NerveSignal(
            signal_type=signal_type,
            domain_path=domain_path,
            content=content,
            source=source,
            prompt=prompt,
            response=response,
            timestamp=datetime.utcnow(),
            hormonal_snapshot=hormonal_snapshot,
            turn_index=self._turn_counter,
            pe_at_collection=self._current_pe,
            episode_tag=self._episode_tag,
        )
        signal.quality = self.compute_signal_quality(signal, domain_db=None)

        # Content-similarity dedup for injected LEARN signals
        if signal_type == "LEARN" and domain_path and content:
            _existing = next(
                (s for s in self._signal_buffer
                 if s.signal_type == "LEARN"
                 and s.domain_path == domain_path
                 and s.content),
                None,
            )
            if _existing:
                _a = set(content.lower().split())
                _b = set(_existing.content.lower().split())
                _denom = max(len(_a), len(_b), 1)
                if len(_a & _b) / _denom > 0.85:
                    return signal  # near-identical, skip

        self._signal_buffer.append(signal)

        # Track errors
        if signal_type in _ERROR_SIGNALS:
            self._recent_errors.append(True)
        elif signal_type in _SUCCESS_SIGNALS:
            self._recent_errors.append(False)

        # Forward to hypothalamus
        if hypothalamus is not None:
            hypothalamus.on_signal(signal_type)

        # Research logging
        if self._event_logger is not None:
            self._event_logger.log_signal_collected(
                signal_type=signal_type,
                domain_path=domain_path or "",
                content=content or "",
                turn=self._turn_counter,
                hormonal_snapshot=hormonal_snapshot,
            )

        return signal

    # -------------------------------------------------------------------
    # Cross-Turn Pattern Detection (Phase 10)
    # -------------------------------------------------------------------

    def detect_cross_turn_patterns(
        self,
        hypothalamus: Any | None = None,
        window_turns: int = 5,
    ) -> list[NerveSignal]:
        """Detect patterns across the last N turns and emit synthetic signals.

        Analyzes the signal buffer for multi-turn patterns that individual
        signal extraction misses:

          1. Repeated UNKNOWN in same domain (3+ turns) -- persistent gap
          2. Error rate increasing over recent turns -- quality decline
          3. Engagement declining (fewer signals per turn) -- user losing interest
          4. Same domain appearing in both user and DMN sources -- convergence

        Synthetic signals are tagged with ``source="pattern_detection"``
        and forwarded to the hypothalamus.

        Returns:
            List of synthetic NerveSignal objects generated from detected
            patterns. These are also appended to the signal buffer.
        """
        if self._turn_counter < 3:
            return []

        current_turn = self._turn_counter
        min_turn = current_turn - window_turns

        recent = [
            s for s in self._signal_buffer if s.turn_index > min_turn
        ]
        if not recent:
            return []

        hormonal_snapshot: dict[str, float] = {}
        if hypothalamus is not None:
            hormonal_snapshot = {
                name: h.level
                for name, h in hypothalamus.hormones.items()
            }

        synthetic: list[NerveSignal] = []

        # --- Pattern 1: Repeated UNKNOWN in same domain across 3+ turns ---
        unknown_by_domain: dict[str, set[int]] = {}
        for sig in recent:
            if sig.signal_type == "UNKNOWN" and sig.domain_path:
                unknown_by_domain.setdefault(
                    sig.domain_path, set(),
                ).add(sig.turn_index)

        for domain, turns in unknown_by_domain.items():
            if len(turns) >= 3:
                synthetic.append(NerveSignal(
                    signal_type="UNKNOWN",
                    domain_path=domain,
                    content=(
                        f"Persistent knowledge gap: {domain} unknown "
                        f"across {len(turns)} recent turns"
                    ),
                    source="pattern_detection",
                    meta_layer="acc_epistemic",
                    timestamp=datetime.utcnow(),
                    hormonal_snapshot=hormonal_snapshot,
                    turn_index=current_turn,
                ))

        # --- Pattern 2: Error rate increasing ---
        if len(self._recent_errors) >= 6:
            half = len(self._recent_errors) // 2
            first_half = self._recent_errors[:half]
            second_half = self._recent_errors[half:]
            first_rate = (
                sum(1 for e in first_half if e) / len(first_half)
                if first_half else 0.0
            )
            second_rate = (
                sum(1 for e in second_half if e) / len(second_half)
                if second_half else 0.0
            )
            if second_rate > first_rate + 0.2 and second_rate > 0.3:
                synthetic.append(NerveSignal(
                    signal_type="EVALUATE:incorrect",
                    domain_path="Meta.ErrorRate",
                    content=(
                        f"Error rate increasing: "
                        f"{first_rate:.0%} -> {second_rate:.0%} "
                        f"over last {len(self._recent_errors)} turns"
                    ),
                    source="pattern_detection",
                    meta_layer="acc_epistemic",
                    timestamp=datetime.utcnow(),
                    hormonal_snapshot=hormonal_snapshot,
                    turn_index=current_turn,
                ))

        # --- Pattern 3: Engagement declining (fewer signals per turn) ---
        signals_per_turn: dict[int, int] = {}
        for sig in recent:
            if sig.source not in ("pattern_detection", "dmn"):
                signals_per_turn[sig.turn_index] = (
                    signals_per_turn.get(sig.turn_index, 0) + 1
                )

        if len(signals_per_turn) >= 4:
            sorted_turns = sorted(signals_per_turn.keys())
            half = len(sorted_turns) // 2
            early_avg = sum(
                signals_per_turn[t] for t in sorted_turns[:half]
            ) / half
            late_avg = sum(
                signals_per_turn[t] for t in sorted_turns[half:]
            ) / (len(sorted_turns) - half)
            if early_avg > 0 and late_avg < early_avg * 0.5:
                synthetic.append(NerveSignal(
                    signal_type="EVALUATE",
                    domain_path="Meta.Engagement",
                    content=(
                        f"Engagement declining: signal density dropped "
                        f"from {early_avg:.1f} to {late_avg:.1f} per turn"
                    ),
                    source="pattern_detection",
                    meta_layer="amygdala_affective",
                    timestamp=datetime.utcnow(),
                    hormonal_snapshot=hormonal_snapshot,
                    turn_index=current_turn,
                ))

        # --- Pattern 4: Convergence (domain in both user and DMN sources) ---
        user_domains: set[str] = set()
        dmn_domains: set[str] = set()
        for sig in recent:
            if sig.signal_type == "LEARN" and sig.domain_path:
                if sig.source in ("user", "web"):
                    user_domains.add(sig.domain_path)
                elif sig.source in ("dmn", "dmn_enriched", "dmn_social"):
                    dmn_domains.add(sig.domain_path)

        converged = user_domains & dmn_domains
        for domain in converged:
            synthetic.append(NerveSignal(
                signal_type="LEARN",
                domain_path=domain,
                content=(
                    f"Convergence: {domain} learned from both "
                    f"user interaction and DMN daydreaming"
                ),
                source="pattern_detection",
                meta_layer="pfc_judgment",
                timestamp=datetime.utcnow(),
                hormonal_snapshot=hormonal_snapshot,
                turn_index=current_turn,
            ))

        # Add synthetic signals to buffer and forward to hypothalamus
        if synthetic:
            self._signal_buffer.extend(synthetic)
            if hypothalamus is not None:
                for sig in synthetic:
                    hypothalamus.on_signal(sig.signal_type)
            logger.info(
                "Vagus: +%d cross-turn pattern signals (turn %d)",
                len(synthetic), current_turn,
            )

        return synthetic

    # -------------------------------------------------------------------
    # Agentic Tool Learning: ANS-driven signal extraction from tool results
    # -------------------------------------------------------------------

    def on_tool_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        user_message: str,
        hypothalamus: Any | None = None,
    ) -> list[NerveSignal]:
        """Extract learnable signals from a tool execution result (sync).

        This is the **secondary vagus pathway** — it captures knowledge
        from tool outputs during the agentic ACT phase where the model
        runs without the behavior adapter and cannot emit ``[LEARN:]``
        tags itself.

        Uses heuristic pattern matching for immediate, synchronous
        extraction.  For LLM-powered extraction, use
        :meth:`on_tool_result_async`.

        Args:
            tool_name: Name of the tool that executed.
            args: Arguments passed to the tool.
            result: The tool's output text.
            user_message: The user's original request (for context).
            hypothalamus: Optional hormonal engine for snapshots.

        Returns:
            List of ``NerveSignal`` injected into the buffer.
        """
        if not self.is_awake or not result:
            return []

        is_error = result.startswith("Error:")
        if len(result) < 20 and not is_error:
            return []

        signals: list[NerveSignal] = []

        # Learn from meaningful errors instead of suppressing them
        if is_error and len(result) >= 20:
            error_domain = f"System.ToolError.{tool_name}"
            error_fact = f"Tool {tool_name} error: {result[:200]}"

            # Track per-tool error count for stronger signals
            _err_key = f"_tool_err_{tool_name}"
            _err_count = getattr(self, _err_key, 0) + 1
            setattr(self, _err_key, _err_count)

            if _err_count >= 2:
                error_fact += (
                    f" (failed {_err_count}x this session — "
                    f"check parameter names, values, and tool docs)"
                )

            sig = self.inject_signal(
                signal_type="LEARN",
                domain_path=error_domain,
                content=error_fact,
                hypothalamus=hypothalamus,
                source="tool",
            )
            sig.pipe_fact = error_fact
            sig.prompt = user_message
            sig.response = f"[{tool_name}:ERROR] {result[:500]}"
            signals.append(sig)
            logger.info(
                "ANS tool error learning: %s error #%d (buffer: %d)",
                tool_name, _err_count, self.signal_count,
            )
            return signals

        if is_error:
            return []

        heuristic_facts = self._extract_tool_learnings_heuristic(
            tool_name, args, result,
        )
        if heuristic_facts:
            new_domains = [d for d, _ in heuristic_facts]
            removed = self._reconcile_signals([], new_domains)
            if removed:
                logger.info(
                    "ANS heuristic tool learning: deduped %d signal(s)",
                    removed,
                )
        for domain, fact in heuristic_facts:
            sig = self.inject_signal(
                signal_type="LEARN",
                domain_path=domain,
                content=fact,
                hypothalamus=hypothalamus,
                source="tool",
            )
            sig.pipe_fact = fact
            sig.prompt = user_message
            sig.response = f"[{tool_name}] {result[:500]}"
            signals.append(sig)

        if signals:
            logger.info(
                "ANS tool learning (heuristic): +%d signals from %s (buffer: %d)",
                len(signals), tool_name, self.signal_count,
            )

        return signals

    async def on_tool_result_async(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        user_message: str,
        hypothalamus: Any | None = None,
        vllm_client: Any | None = None,
        adapter_name: str | None = None,
    ) -> list[NerveSignal]:
        """Extract learnable signals using LLM micro-call (async).

        Uses the combined adapter (when provided) so extraction benefits
        from trained fact-judgment.  Falls back to heuristic if
        vllm_client is None.
        """
        if not self.is_awake or not result:
            return []

        if len(result) < 20 or result.startswith("Error:"):
            return []

        signals: list[NerveSignal] = []

        if vllm_client is not None:
            try:
                extracted, replacements = await self._extract_tool_learnings_llm(
                    vllm_client, tool_name, args, result, user_message,
                    adapter_name=adapter_name,
                )
                extracted = self._validate_plan_facts(extracted)
                new_domains = [d for d, _ in extracted]
                removed = self._reconcile_signals(replacements, new_domains)
                if removed:
                    logger.info(
                        "ANS tool learning: reconciled %d stale signal(s)",
                        removed,
                    )
                for domain, fact in extracted:
                    if self._is_infrastructure_credential(domain, fact):
                        wm = getattr(self, "_wm_ref", None)
                        if wm is not None:
                            from nls.runtime.channel_credential_policy import (
                                upsert_wm_credential,
                            )

                            upsert_wm_credential(
                                wm, domain=domain, fact=fact, source="ans",
                            )
                        continue
                    sig = self.inject_signal(
                        signal_type="LEARN",
                        domain_path=domain,
                        content=fact,
                        hypothalamus=hypothalamus,
                        source="tool",
                    )
                    sig.pipe_fact = fact
                    sig.prompt = user_message
                    sig.response = f"[{tool_name}] {result[:500]}"
                    signals.append(sig)
            except Exception as e:
                logger.warning("LLM tool learning extraction failed: %s", e)

        if signals:
            logger.info(
                "ANS tool learning (LLM): +%d signals from %s (buffer: %d)",
                len(signals), tool_name, self.signal_count,
            )

        return signals

    async def _extract_tool_learnings_llm(
        self,
        vllm_client: Any,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        user_message: str,
        adapter_name: str | None = None,
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Use LLM micro-call to extract facts from tool output.

        Returns ``(new_facts, replaced_domains)`` — same contract as
        ``_extract_conversation_learnings_llm``.
        """
        already_known = self._get_recent_learnings_summary()
        already_block = ""
        if already_known:
            already_block = (
                f"\n--- Already learned ---\n"
                f"{already_known}\n"
                f"--- End already learned ---\n\n"
            )

        prompt_text = (
            "Extract specific facts worth remembering from this tool output. "
            "For each fact, reply with: Domain|Fact\n"
            "Domain should use these prefixes:\n"
            "  User.*     — facts about the human (contacts, schedule, "
            "preferences, bookings, health data, messages)\n"
            "  Social.*   — people discovered (names, roles, relationships)\n"
            "  Project.*  — project info (paths, structure, configs, "
            "scripts, dependencies, design assets, documents)\n"
            "  System.*   — installed software, paths, OS, env configs\n"
            "  Account.*  — account info, login status, services\n"
            "  Agent.*    — facts about the AI itself\n"
            "  Other categories: Server.*, Website.*, Calendar.*, etc.\n\n"
            "PRIORITY: extract any concrete, reusable facts — file paths, "
            "names, configs, dates, contact details, decisions, results, "
            "numbers, identifiers, status outcomes.\n\n"
            "If no specific facts worth remembering, reply: NONE\n"
            "If a new fact REPLACES an existing one from the 'Already "
            "learned' list, add: REPLACES:Domain\n"
            f"Max 8 facts.\n\n"
            f"{already_block}"
            f"User asked: {user_message[:400]}\n"
            f"Tool: {tool_name}({json.dumps(args, ensure_ascii=False)[:300]})\n"
            f"Result: {result[:1200]}"
        )

        _micro_msgs, _micro_body = _prepare_micro_inference(
            [
                {"role": "system", "content": (
                    "You extract key facts from tool outputs. Be concise. "
                    "Extract paths, configs, names, contacts, dates, "
                    "decisions, outcomes, and any reusable detail. "
                    "Cover all domains: technical, personal, social, "
                    "calendar, health, project, environment. "
                    "When a new fact supersedes an old one, emit a "
                    "REPLACES:Domain line."
                )},
                {"role": "user", "content": prompt_text},
            ],
            vllm_client,
            adapter_name=adapter_name,
        )
        raw = await vllm_client.generate(
            messages=_micro_msgs,
            max_tokens=250,
            temperature=0.0,
            adapter_name=adapter_name,
            extra_body=_micro_body,
        )

        text = (raw.text if hasattr(raw, "text") else str(raw)).strip()
        if not text or "NONE" in text.upper():
            return [], []

        facts: list[tuple[str, str]] = []
        replacements: list[str] = []
        for line in text.strip().split("\n"):
            line = line.strip().lstrip("- \u2022123.)")
            if line.upper().startswith("SOURCE:"):
                continue
            if line.upper().startswith("REPLACES:"):
                domain = line.split(":", 1)[1].strip()
                if domain:
                    replacements.append(domain)
            elif "|" in line:
                parts = line.split("|", 1)
                domain = parts[0].strip()
                fact = parts[1].strip()
                if domain and fact and len(fact) > 5:
                    from nls.bridge.aku import validate_domain_path
                    valid, _err = validate_domain_path(domain)
                    if valid:
                        facts.append((domain, fact))
                    else:
                        logger.debug(
                            "ANS tool learning: dropping invalid domain '%s'",
                            domain[:80],
                        )
        return facts[:8], replacements

    @staticmethod
    def _extract_tool_learnings_heuristic(
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> list[tuple[str, str]]:
        """Heuristic pattern extraction from tool results."""
        import re as _re

        facts: list[tuple[str, str]] = []

        # IP addresses with context
        ip_matches = _re.findall(
            r"(\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?)\b",
            result,
        )
        for ip in ip_matches[:2]:
            facts.append(("Network.Address", f"IP/host found: {ip}"))

        # SSH/connection strings
        ssh_matches = _re.findall(
            r"(\w+@[\w.\-]+(?::\d+)?)", result,
        )
        for ssh in ssh_matches[:2]:
            facts.append(("Server.SSH", f"SSH target: {ssh}"))

        # File paths (Unix + Windows)
        path_matches = _re.findall(
            r"(/[\w./\-]+(?:\.[\w]+)?|[A-Z]:\\[\w\\.\- ]+)", result,
        )
        for p in path_matches[:2]:
            if len(p) > 10:
                facts.append(("System.Path", f"Path: {p}"))

        # Browser: successful selectors
        if tool_name == "browser" and args.get("action") in ("click", "fill"):
            selector = args.get("selector", "")
            if selector and "Error" not in result:
                url = args.get("url", "")
                facts.append((
                    "Browser.Selector",
                    f"Working selector: {selector}" + (f" on {url}" if url else ""),
                ))

        return facts[:5]

    # -------------------------------------------------------------------
    # Plan Fact Validation
    # -------------------------------------------------------------------

    def _validate_plan_facts(
        self,
        facts: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        """Cross-reference Project.Progress / Project.Step facts against
        the actual plan JSON to prevent phantom completion.

        The LLM sometimes hallucinates plan completion (e.g. "6/6 steps
        done") when the underlying plan data shows steps still pending.
        This validator drops or corrects such facts using the plan store
        as source of truth.
        """
        if self._plan_fact_validator is None:
            return facts

        plan = None
        try:
            plan = self._plan_fact_validator()
        except Exception as exc:
            logger.debug("Plan fact validator call failed: %s", exc)
            return facts

        if plan is None:
            return facts

        validated: list[tuple[str, str]] = []
        actual_progress = plan.progress_summary()  # e.g. "4/6 steps done"
        actual_done = sum(1 for s in plan.steps if s.status == "done")
        actual_total = len(plan.steps)

        step_labels_lower = {s.label.lower(): s for s in plan.steps}
        step_ids = {s.id: s for s in plan.steps}

        for domain, fact in facts:
            fact_lower = fact.lower()

            # --- Validate progress facts ---
            if domain.startswith(("Project.Progress", "Plan.Progress")):
                m = re.search(r"(\d+)\s*/?\s*(?:out of\s+)?(\d+)\s*steps?\s*(?:done|completed)", fact_lower)
                if m:
                    claimed_done = int(m.group(1))
                    claimed_total = int(m.group(2))
                    if claimed_done != actual_done or claimed_total != actual_total:
                        logger.warning(
                            "ANS plan validator: corrected progress "
                            "'%d/%d' -> '%s' (plan %s)",
                            claimed_done, claimed_total,
                            actual_progress, plan.id,
                        )
                        validated.append((domain, f"Plan progress: {actual_progress}"))
                        continue
                validated.append((domain, fact))
                continue

            # --- Validate step completion facts ---
            if domain.startswith(("Project.Step", "Plan.Step")):
                is_completion = any(
                    kw in fact_lower
                    for kw in ("is now complete", "is now done",
                               "marked as done", "completed")
                )
                if is_completion:
                    matched_step = None
                    for label, step in step_labels_lower.items():
                        if label in fact_lower:
                            matched_step = step
                            break
                    if matched_step is None:
                        for sid, step in step_ids.items():
                            if sid in fact:
                                matched_step = step
                                break

                    if matched_step and matched_step.status != "done":
                        logger.warning(
                            "ANS plan validator: dropping phantom step "
                            "completion '%s' — actual status is '%s' "
                            "(plan %s, step %s)",
                            fact, matched_step.status, plan.id,
                            matched_step.id,
                        )
                        continue

                validated.append((domain, fact))
                continue

            validated.append((domain, fact))

        if len(validated) < len(facts):
            logger.info(
                "ANS plan validator: filtered %d/%d plan facts",
                len(facts) - len(validated), len(facts),
            )

        return validated

    # -------------------------------------------------------------------
    # ANS Safety Net: LLM-based conversation learning (always-on)
    # -------------------------------------------------------------------

    async def safety_net_extract_async(
        self,
        vllm_client: Any,
        hypothalamus: Any | None = None,
        *,
        prompt_override: str | None = None,
        response_override: str | None = None,
        history: list[dict] | None = None,
        domain_db: Any | None = None,
        adapter_name: str | None = None,
        project_id: str = "",
    ) -> list[NerveSignal]:
        """LLM micro-call to catch learnable facts the adapter missed.

        Called by the runtime after every chat turn where the behavior
        adapter emitted zero ``[LEARN:]`` tags.  Analyzes both the user's
        message and the model's response for concrete facts worth storing.

        This is the **always-on safety net** -- it runs in chat mode
        AND agentic mode, catching what falls through the cracks.

        The extraction LLM receives:
        1. A conversation window (last ~3 turns) for context.
        2. What the ANS has already collected (to avoid duplicates).
        3. The current user message + assistant response.

        Parameters
        ----------
        prompt_override / response_override :
            When provided, use these instead of the instance-level
            ``_last_turn_prompt`` / ``_last_turn_response``.  This avoids
            race conditions when multiple turns schedule extractions
            concurrently (each closure captures its own values).
        history :
            Recent conversation messages (role/content dicts) for context.
        domain_db :
            Optional DomainDB instance.  When provided, existing facts
            are included in the "already known" list so the LLM avoids
            re-extracting them after sleep clears the signal buffer.
        """
        prompt = prompt_override if prompt_override is not None else self._last_turn_prompt
        response = response_override if response_override is not None else self._last_turn_response

        if not prompt:
            logger.debug("ANS safety net: empty prompt, skipping")
            return []

        already_known = self._get_recent_learnings_summary(
            domain_db=domain_db, project_id=project_id,
        )
        logger.info(
            "ANS safety net: running LLM extraction "
            "(prompt=%.80s, response=%.80s, "
            "history_msgs=%d, known_facts=%d, project=%s)",
            prompt, response,
            len(history) if history else 0,
            already_known.count("\n") + (1 if already_known else 0),
            project_id or "(none)",
        )

        fact_coro = self._extract_conversation_learnings_llm(
            vllm_client, prompt, response, history=history,
            adapter_name=adapter_name, domain_db=domain_db,
            project_id=project_id,
        )
        emotion_coro = self._sense_emotional_state_llm(
            vllm_client, prompt, response,
            adapter_name=adapter_name,
        )

        results = await asyncio.gather(
            fact_coro, emotion_coro, return_exceptions=True,
        )
        fact_raw, emotion_raw = results

        if isinstance(fact_raw, BaseException):
            logger.warning("ANS fact extraction failed: %s", fact_raw)
            return []
        fact_result: tuple[list, list, list] = fact_raw

        emotion_result: dict[str, float] = {}
        if isinstance(emotion_raw, BaseException):
            logger.warning("ANS emotional sensing failed (non-critical): %s", emotion_raw)
        else:
            emotion_result = emotion_raw
        self._last_emotion_result = emotion_result

        extracted, replacements, ans_signals = fact_result  # type: ignore[misc]
        extracted = self._validate_plan_facts(extracted)

        logger.info(
            "ANS safety net: LLM returned %d facts, %d replacements, "
            "emotions=%s: %s",
            len(extracted) if extracted else 0,
            len(replacements) if replacements else 0,
            emotion_result or "none",
            extracted[:3] if extracted else "NONE",
        )

        # Route emotional probe signals to the hypothalamus.
        # The emotional sensing call returns probe categories (BONDING,
        # CURIOSITY, etc.) with intensity values matching the V5 signal
        # probe vocabulary from signal_probes.json.
        if emotion_result and hypothalamus is not None:
            try:
                hypothalamus.on_probe_signals(emotion_result)
            except Exception:
                logger.debug("on_probe_signals failed", exc_info=True)

            # Inject discrete signals for high-intensity states so they
            # appear in the ANS buffer and feed into sleep training.
            _PROBE_TO_SIGNAL = {
                "BONDING": ("EVALUATE:warm", "Bonding detected by ANS"),
                "CURIOSITY": ("EVALUATE:curious", "Curiosity detected by ANS"),
                "EVAL_POSITIVE": ("EVALUATE:correct", "Positive evaluation by ANS"),
                "EVAL_NEGATIVE": ("EVALUATE:frustrated", "Negative evaluation by ANS"),
                "EVAL_UNCERTAIN": ("EVALUATE:uncertain", "Uncertainty detected by ANS"),
                "DOUBT": ("EVALUATE:skeptical", "Doubt detected by ANS"),
                "REFLECT": ("REFLECT", "Reflection detected by ANS"),
                "FOCUS": ("EVALUATE:processing", "Deep focus detected by ANS"),
                "LEARN": ("LEARN", "Learning moment detected by ANS"),
                "PLAN": ("PLAN_STEP", "Planning detected by ANS"),
            }
            for probe_cat, intensity in emotion_result.items():
                if intensity >= 0.5 and probe_cat in _PROBE_TO_SIGNAL:
                    sig_type, content = _PROBE_TO_SIGNAL[probe_cat]
                    self.inject_signal(
                        signal_type=sig_type,
                        domain_path=f"Feedback.Emotion.{probe_cat}",
                        content=content,
                        hypothalamus=hypothalamus,
                        source="ans_emotional_sensing",
                    )

        # Legacy: handle any SIGNAL: lines the fact extractor may still emit
        if ans_signals and hypothalamus is not None:
            for sig_name in ans_signals:
                if sig_name == "FRUSTRATED":
                    hypothalamus.on_signal("user_correction")
                elif sig_name == "PLEASED":
                    hypothalamus.on_signal("user_positive")

        if not extracted:
            return []

        # Reconcile stale signals before injecting new ones
        new_domains = [d for d, _ in extracted]
        removed = self._reconcile_signals(replacements, new_domains)
        if removed:
            logger.info(
                "ANS safety net: reconciled %d stale signal(s)", removed,
            )

        signals: list[NerveSignal] = []
        for domain, fact in extracted:
            if domain in self._suppressed_domains:
                logger.debug(
                    "ANS safety net: suppressed domain '%s' (user deleted)", domain,
                )
                continue

            if self._is_infrastructure_credential(domain, fact):
                wm = getattr(self, "_wm_ref", None)
                if wm is not None:
                    from nls.runtime.channel_credential_policy import upsert_wm_credential

                    if upsert_wm_credential(
                        wm, domain=domain, fact=fact, source="ans",
                    ):
                        logger.info(
                            "ANS: routed credential to WM slot (domain=%s)",
                            domain,
                        )
                        continue

            sig = self.inject_signal(
                signal_type="LEARN",
                domain_path=domain,
                content=fact,
                hypothalamus=hypothalamus,
                source="ans_safety_net",
            )
            sig.pipe_fact = fact
            sig.prompt = prompt
            sig.response = response
            signals.append(sig)

        if signals:
            logger.info(
                "ANS safety net: +%d learnings from conversation "
                "(adapter emitted 0 LEARN tags, buffer: %d)",
                len(signals), self.signal_count,
            )

        return signals

    def _get_recent_learnings_summary(
        self,
        max_items: int = 10,
        domain_db: Any | None = None,
        project_id: str = "",
    ) -> str:
        """Build a compact indexed summary of what the ANS already learned.

        Returns a string like::

            [0] User.Name: The user's name is Babo
            [1] Server.SSH: SSH host is 192.168.68.96

        Merges two sources to avoid re-extraction after sleep clears
        the signal buffer:

        1. **Signal buffer** -- LEARN signals from the current session.
        2. **knowledge.db** -- persisted facts from previous sessions
           (scoped to global + active project when ``project_id`` is set).

        Deduplication is by domain path: buffer signals take priority
        over database facts for the same domain.
        """
        lines: list[str] = []
        seen_domains: set[str] = set()

        learn_signals = [
            s for s in self._signal_buffer
            if s.signal_type == "LEARN"
        ][-max_items:]
        for s in learn_signals:
            fact = s.pipe_fact or s.content
            domain = s.domain_path or "General"
            lines.append(f"{domain}: {fact}")
            seen_domains.add(domain)

        if domain_db is not None:
            remaining = max(0, max_items - len(lines))
            if remaining > 0:
                try:
                    if project_id and hasattr(domain_db, "get_facts_in_context"):
                        db_facts = domain_db.get_facts_in_context(project_id)
                    else:
                        db_facts = domain_db.get_all_facts()
                    for f in db_facts[-remaining * 2:]:
                        d = getattr(f, "domain_path", None) or ""
                        v = getattr(f, "current_value", None) or ""
                        if d and v and d not in seen_domains:
                            if ".Credential." in d:
                                continue
                            lines.append(f"{d}: {v}")
                            seen_domains.add(d)
                            if len(lines) >= max_items + remaining:
                                break
                except Exception:
                    pass

        if not lines:
            return ""
        return "\n".join(f"[{i}] {ln}" for i, ln in enumerate(lines))

    def _reconcile_signals(
        self,
        replaced_domains: list[str],
        new_fact_domains: list[str] | None = None,
    ) -> int:
        """Remove stale LEARN signals whose domain was superseded.

        Two dedup layers:

        1. **LLM-driven** — ``replaced_domains`` from explicit
           ``REPLACES:`` directives.
        2. **Deterministic** — ``new_fact_domains`` are the domains of
           the facts about to be inserted.  Any existing LEARN signal
           with an exact domain match is removed automatically, acting
           as a same-domain dedup fallback when the LLM doesn't emit
           a ``REPLACES:`` line.

        For each domain, removes the **oldest** matching LEARN signal.
        Returns the total number of signals removed.
        """
        all_domains = list(replaced_domains or [])
        for d in (new_fact_domains or []):
            if d and d not in all_domains:
                all_domains.append(d)

        if not all_domains:
            return 0

        removed = 0
        for domain in all_domains:
            for i, sig in enumerate(self._signal_buffer):
                if sig.signal_type == "LEARN" and sig.domain_path == domain:
                    old_fact = sig.pipe_fact or sig.content or ""
                    self._signal_buffer.pop(i)
                    removed += 1
                    logger.info(
                        "ANS reconcile: removed stale signal "
                        "[%s] %s (superseded)",
                        domain, old_fact[:80],
                    )
                    break  # only remove the oldest match per domain
        return removed

    # -------------------------------------------------------------------
    # Context-pressure reconciliation
    # -------------------------------------------------------------------

    def pressure_reconcile(self) -> int:
        """Proactive consolidation sweep under context pressure.

        Called by the agentic loop when a 400 (context overflow) is
        received.  Performs three operations on the signal buffer:

        1. **Same-domain dedup** — for each domain that appears more
           than once among LEARN signals, keep only the most recent.
        2. **Near-duplicate detection** — LEARN signals whose
           ``pipe_fact`` content is a substring of another signal's
           fact (or vice-versa) are merged into the longer version.
        3. **Staleness sweep** — EVALUATE signals older than the
           median timestamp are removed (they've already influenced
           hormone levels and are not pending sleep consolidation
           in the same way LEARN signals are).

        Returns the total number of signals removed.
        """
        if not self._signal_buffer:
            return 0

        before = len(self._signal_buffer)

        # --- Pass 1: same-domain dedup (keep most recent per domain) ---
        domain_indices: dict[str, list[int]] = {}
        for i, sig in enumerate(self._signal_buffer):
            if sig.signal_type == "LEARN" and sig.domain_path:
                domain_indices.setdefault(sig.domain_path, []).append(i)

        remove_set: set[int] = set()
        for domain, indices in domain_indices.items():
            if len(indices) > 1:
                # Keep the last (most recent) index, mark others
                for idx in indices[:-1]:
                    remove_set.add(idx)
                    logger.info(
                        "ANS pressure-reconcile: dedup domain [%s] "
                        "removing older signal idx=%d",
                        domain, idx,
                    )

        # --- Pass 2: near-duplicate detection on pipe_fact content ---
        learn_signals = [
            (i, s) for i, s in enumerate(self._signal_buffer)
            if s.signal_type == "LEARN" and i not in remove_set
        ]
        for a_idx in range(len(learn_signals)):
            i_a, sig_a = learn_signals[a_idx]
            if i_a in remove_set:
                continue
            fact_a = (sig_a.pipe_fact or sig_a.content or "").strip()
            if not fact_a or len(fact_a) < 8:
                continue
            for b_idx in range(a_idx + 1, len(learn_signals)):
                i_b, sig_b = learn_signals[b_idx]
                if i_b in remove_set:
                    continue
                fact_b = (sig_b.pipe_fact or sig_b.content or "").strip()
                if not fact_b or len(fact_b) < 8:
                    continue
                # Substring containment: shorter is redundant
                if fact_a in fact_b:
                    remove_set.add(i_a)
                    logger.info(
                        "ANS pressure-reconcile: near-dup, keeping "
                        "longer fact [%s] over [%s]",
                        fact_b[:60], fact_a[:60],
                    )
                    break
                elif fact_b in fact_a:
                    remove_set.add(i_b)
                    logger.info(
                        "ANS pressure-reconcile: near-dup, keeping "
                        "longer fact [%s] over [%s]",
                        fact_a[:60], fact_b[:60],
                    )

        # --- Pass 3: staleness sweep for EVALUATE signals ---
        eval_signals = [
            (i, s) for i, s in enumerate(self._signal_buffer)
            if s.signal_type.startswith("EVALUATE") and i not in remove_set
        ]
        if len(eval_signals) > 4:
            timestamps = sorted(s.timestamp for _, s in eval_signals)
            median_ts = timestamps[len(timestamps) // 2]
            for i, sig in eval_signals:
                if sig.timestamp < median_ts:
                    remove_set.add(i)
                    logger.info(
                        "ANS pressure-reconcile: stale EVALUATE "
                        "signal removed [%s]",
                        sig.signal_type,
                    )

        # --- Apply removals (reverse order to preserve indices) ---
        for idx in sorted(remove_set, reverse=True):
            self._signal_buffer.pop(idx)

        removed = before - len(self._signal_buffer)
        if removed:
            logger.info(
                "ANS pressure-reconcile: removed %d signals "
                "(%d -> %d remaining)",
                removed, before, len(self._signal_buffer),
            )
        return removed

    _OPS_PREFIXES = ("System.", "Project.", "Account.", "Repository.")

    def get_context_summary(
        self,
        max_ops_learn: int = 10,
        max_personal_learn: int = 5,
        max_other: int = 5,
        render_mode: str = "chat",
    ) -> str:
        """Build a compact ANS context block for injection into prompts.

        This is the nervous system's working memory — a distilled view
        of what it has learned, bonded to, evaluated, and flagged during
        this session.

        When ``render_mode`` is ``"agentic"`` or ``"coordinator"``,
        operational LEARN signals and EVALUATE signals are omitted
        because they flow through the Cryptex rings instead.

        Returns empty string if the buffer is empty.
        """
        if not self._signal_buffer:
            return ""

        agentic = render_mode in ("agentic", "coordinator")
        sections: list[str] = []

        all_learns = [
            s for s in self._signal_buffer
            if s.signal_type == "LEARN"
        ]

        if not agentic:
            ops_learns = [
                s for s in all_learns
                if (s.domain_path or "").startswith(self._OPS_PREFIXES)
            ][-max_ops_learn:]

            if ops_learns:
                lines = []
                for s in ops_learns:
                    fact = s.pipe_fact or s.content
                    domain = s.domain_path or "General"
                    lines.append(f"  {domain}: {fact}")
                sections.append(
                    "Operational context (paths, project, accounts):\n"
                    + "\n".join(lines)
                )

        personal_learns = [
            s for s in all_learns
            if not (s.domain_path or "").startswith(self._OPS_PREFIXES)
        ][-max_personal_learn:]

        if personal_learns:
            lines = []
            for s in personal_learns:
                fact = s.pipe_fact or s.content
                domain = s.domain_path or "General"
                lines.append(f"  {domain}: {fact}")
            sections.append("Identity & personal:\n" + "\n".join(lines))

        # Bonds — identity and relationship context
        bonds = [
            s for s in self._signal_buffer
            if s.signal_type == "BOND"
        ][-max_other:]
        if bonds:
            lines = []
            for s in bonds:
                lines.append(f"  {s.domain_path or 'Identity'}: {s.content}")
            sections.append("Bonds:\n" + "\n".join(lines))

        # Evaluations — omit in agentic mode (training signals only)
        if not agentic:
            evals = [
                s for s in self._signal_buffer
                if s.signal_type.startswith("EVALUATE")
            ][-max_other:]
            if evals:
                lines = []
                for s in evals:
                    lines.append(f"  {s.signal_type}: {s.content[:80]}")
                sections.append("Self-evaluations:\n" + "\n".join(lines))

        if not sections:
            return ""

        return (
            "[ANS CONTEXT — your nervous system's current awareness]\n"
            + "\n".join(sections)
            + "\n[END ANS CONTEXT]"
        )

    # Domain prefix → Cryptex ring mapping for absorb_signals_to_rings.
    # Longer prefixes MUST come before shorter ones to avoid mis-routing
    # (e.g. Goal.Tactical.* must match before Goal.*).
    _DOMAIN_RING_MAP = {
        "User.": "user_model",
        "Feedback.": "emotional",
        "Agent.Knowledge.": "consolidation",
        "Agent.Skill.": "consolidation",
        "Task.": "tactical_goals",
        "Goal.Tactical.": "tactical_goals",
        "Goal.Strategic.": "strategic_goals",
        "Goal.": "strategic_goals",
        "Project.Credential.": "credentials",
        "Project.": "project_facts",
        "Repository.": "project_facts",
        "System.": "project_facts",
        "Account.": "credentials",
    }

    def absorb_signals_to_rings(self, working_memory: Any) -> int:
        """Route LEARN signals from the ANS buffer to Cryptex rings.

        Called by the bridge before each generation to ensure the
        Cryptex has the latest ANS-discovered facts.  Covers ALL
        signal domains, not just operational ones.

        If ``domain_path`` is missing but the ``content`` starts with a
        recognised domain prefix (e.g. ``"Goal.Strategic: …"``), we
        infer the domain from the content as a defensive fallback so
        that signals injected without an explicit ``domain_path`` still
        get routed to the correct ring.

        Returns count of signals routed.
        """
        if working_memory is None or not self._signal_buffer:
            return 0

        count = 0
        for sig in self._signal_buffer:
            if sig.signal_type != "LEARN":
                continue
            domain = sig.domain_path or ""
            fact = sig.pipe_fact or sig.content
            if not fact:
                continue

            # Defensive fallback: infer domain from content if domain_path
            # was not set (legacy callers or future oversights).
            # We ensure the inferred domain ends with "." so it matches
            # _DOMAIN_RING_MAP prefixes correctly (e.g. "Goal.Tactical."
            # not "Goal.Tactical" which would fall through to "Goal.").
            if not domain and fact:
                for prefix in self._DOMAIN_RING_MAP:
                    if fact.startswith(prefix):
                        _colon = fact.find(":")
                        if _colon > 0:
                            _raw = fact[:_colon].strip()
                            domain = _raw if _raw.endswith(".") else _raw + "."
                            fact = fact[_colon + 1:].strip()
                        else:
                            domain = prefix
                        break

            # Strip domain prefix from content even when domain was
            # already set (signals sometimes embed the domain in the
            # text, producing "Project.X: Project.X: actual value").
            if domain and fact:
                _d_bare = domain.rstrip(".")
                if fact.startswith(_d_bare):
                    _c_idx = fact.find(":", len(_d_bare) - 5)
                    if 0 < _c_idx < len(_d_bare) + 5:
                        fact = fact[_c_idx + 1:].strip()

            # Find the best ring for this domain
            _target_ring = None
            for prefix, ring_name in self._DOMAIN_RING_MAP.items():
                if domain.startswith(prefix):
                    _target_ring = ring_name
                    break

            if _target_ring is None:
                # Fallback: operational prefixes go to project_facts
                if domain and domain.startswith(self._OPS_PREFIXES):
                    try:
                        working_memory.upsert_fact(
                            domain=domain,
                            content=fact,
                            source="ans",
                            salience=0.85,
                        )
                        count += 1
                    except Exception:
                        pass
                continue

            try:
                if _target_ring == "credentials":
                    wm = working_memory
                    if hasattr(wm, "upsert_credential"):
                        from nls.runtime.channel_credential_policy import (
                            upsert_wm_credential,
                        )

                        if upsert_wm_credential(
                            wm, domain=domain, fact=fact, source="ans",
                        ):
                            count += 1
                    continue

                ring = working_memory.get_ring(_target_ring)
                if ring:
                    _stype = "fact"
                    if _target_ring in ("tactical_goals", "strategic_goals"):
                        _stype = "goal"
                    elif _target_ring == "emotional":
                        _stype = "feeling"
                    ring.upsert_slot(
                        domain=domain or _target_ring,
                        content=fact,
                        slot_type=_stype,
                        salience=0.8,
                        source="ans",
                    )
                    count += 1
            except Exception:
                pass

        return count

    # -------------------------------------------------------------------
    # Buffer mutation API (for admin REST endpoints)
    # -------------------------------------------------------------------

    _TOOL_OUTPUT_PREFIXES = ("bash:", "read:", "edit:", "write:", "grep:", "find:")

    def get_context_items(self) -> list[dict[str, Any]]:
        """Return context-relevant signals as dicts for the REST API.

        LEARN signals are filtered to remove tool-output noise (raw
        ``bash:``, ``read:`` echoes).  EVALUATE signals are kept as-is
        because they represent operational context the agent needs.
        """
        items: list[dict[str, Any]] = []
        for idx, sig in enumerate(self._signal_buffer):
            st = sig.signal_type

            if st not in ("LEARN", "BOND") and not st.startswith("EVALUATE"):
                continue

            content = sig.pipe_fact or sig.content or ""

            # Skip signals with empty or trivially short content
            if len(content.strip()) < 5:
                continue

            # Noise filters apply only to LEARN signals
            if st == "LEARN":
                content_lower = content.lower()
                if any(content_lower.startswith(p) for p in self._TOOL_OUTPUT_PREFIXES):
                    continue

            items.append({
                "index": idx,
                "signal_type": "EVALUATE" if st.startswith("EVALUATE") else st,
                "domain": sig.domain_path or "",
                "content": content,
                "source": sig.source,
                "timestamp": sig.timestamp.isoformat() + "Z",
            })
        return items

    def remove_signal(self, index: int) -> bool:
        """Remove a signal from the buffer and trigger active unlearning.

        Captures the deleted signal's domain and content, then:
        1. Adds the domain to ``_suppressed_domains`` so the safety net
           won't re-extract it (expires after 3 sleep cycles).
        2. Injects an ``ERASE`` signal so the Neural Eraser generates
           negative-weight training pairs during the next sleep cycle,
           actively pushing the model away from the deleted fact.

        Returns True if the signal was removed, False if the index was
        out of range.
        """
        if 0 <= index < len(self._signal_buffer):
            deleted = self._signal_buffer.pop(index)

            if deleted.domain_path:
                self._suppressed_domains[deleted.domain_path] = 3

                erase_signal = NerveSignal(
                    signal_type="ERASE",
                    domain_path=deleted.domain_path,
                    content=deleted.content or deleted.pipe_fact or "",
                    source="user",
                    meta_layer=deleted.meta_layer,
                    hormonal_snapshot=deleted.hormonal_snapshot,
                    timestamp=datetime.utcnow(),
                    turn_index=deleted.turn_index,
                )
                self._signal_buffer.append(erase_signal)

            return True
        return False

    def update_signal(self, index: int, content: str) -> bool:
        """Update a signal's content, marking it as a user edit.

        Captures the old value and injects an ``ERASE`` signal for it
        so the Neural Eraser pushes the model away from the old fact.
        The edited signal gets ``source="user_edit"`` which routes it
        to the highest-priority triage bucket and applies the feedback
        weight multiplier during training.

        Returns True if updated, False if the index was out of range.
        """
        if 0 <= index < len(self._signal_buffer):
            sig = self._signal_buffer[index]
            old_content = sig.content or sig.pipe_fact or ""

            sig.content = content
            sig.pipe_fact = content
            sig.source = "user_edit"

            if old_content and old_content != content and sig.domain_path:
                erase_signal = NerveSignal(
                    signal_type="ERASE",
                    domain_path=sig.domain_path,
                    content=old_content,
                    source="user",
                    meta_layer=sig.meta_layer,
                    hormonal_snapshot=sig.hormonal_snapshot,
                    timestamp=datetime.utcnow(),
                    turn_index=sig.turn_index,
                )
                self._signal_buffer.append(erase_signal)

            return True
        return False

    @staticmethod
    def _build_conversation_window(
        history: list[dict] | None,
        current_user_msg: str,
        current_assistant_msg: str,
        max_turns: int = 3,
    ) -> str:
        """Build a short conversation window from recent history.

        Returns the last ``max_turns`` exchanges plus the current turn,
        formatted as readable text for the LLM extraction prompt.

        Includes tool call results (truncated) so project-level facts
        from agentic loops (file structures, configs, API routes) are
        visible to the extraction LLM.
        """
        lines: list[str] = []
        _BUDGET = 4000

        if history:
            recent: list[tuple[str, str]] = []
            for msg in history:
                role = msg.get("role", "")
                content = (msg.get("content") or "")
                if role in ("user", "assistant") and content.strip():
                    recent.append((role, content.strip()[:800]))
                elif role == "tool" and content.strip():
                    tc_name = msg.get("name", "tool")
                    preview = content.strip()[:400]
                    recent.append(("tool", f"[{tc_name}] {preview}"))
                elif role == "assistant" and msg.get("tool_calls"):
                    tc_summaries = []
                    for tc in msg["tool_calls"][:5]:
                        fn = tc.get("function", {})
                        tc_summaries.append(
                            f"{fn.get('name', '?')}({fn.get('arguments', '')[:150]})"
                        )
                    if tc_summaries:
                        recent.append(("assistant", "Tool calls: " + "; ".join(tc_summaries)))

            for role, content in recent[-(max_turns * 4):]:
                if role == "tool":
                    lines.append(f"Tool result: {content}")
                else:
                    label = "User" if role == "user" else "Assistant"
                    lines.append(f"{label}: {content}")

        if current_user_msg:
            lines.append(f"User: {current_user_msg[:1500]}")
        if current_assistant_msg:
            lines.append(f"Assistant: {current_assistant_msg[:1500]}")

        text = "\n".join(lines) if lines else ""
        if len(text) > _BUDGET:
            text = text[-_BUDGET:]
        return text

    async def _extract_conversation_learnings_llm(
        self,
        vllm_client: Any,
        user_message: str,
        model_response: str,
        history: list[dict] | None = None,
        adapter_name: str | None = None,
        domain_db: Any | None = None,
        project_id: str = "",
    ) -> tuple[list[tuple[str, str]], list[str], list[str]]:
        """LLM micro-call to find facts in a conversation turn.

        Returns ``(new_facts, replaced_domains, ans_signals)`` where:

        - ``new_facts`` — list of ``(domain, fact)`` tuples to inject
        - ``replaced_domains`` — domain paths whose existing signals
          should be removed because the new fact supersedes them
        - ``ans_signals`` — emotional/state signals detected by the LLM
          (e.g. ``"FRUSTRATED"``, ``"CONFUSED"``).  Language-agnostic:
          the LLM detects sentiment regardless of input language.
        """
        conversation_window = self._build_conversation_window(
            history, user_message, model_response,
        )
        already_known = self._get_recent_learnings_summary(
            domain_db=domain_db,
            project_id=project_id,
        )

        already_block = ""
        if already_known:
            already_block = (
                f"\n--- Already learned ---\n"
                f"{already_known}\n"
                f"--- End already learned ---\n\n"
            )

        _project_hint = ""
        if project_id:
            _project_hint = (
                f"\nCURRENT PROJECT: You are working on project '{project_id}'. "
                "Use Project.* paths for project-specific facts. "
                "The storage layer handles project isolation automatically — "
                "just use flat Project.* paths, not namespaced ones.\n\n"
            )

        prompt_text = (
            "Below is a recent conversation between a HUMAN USER and "
            "an AI ASSISTANT. Your job: extract any concrete facts "
            "worth remembering long-term.\n"
            "For each fact, reply on a new line: Domain|Fact\n\n"
            + _project_hint +
            "DOMAIN CATEGORIES — use the correct prefix:\n"
            "- User.*    = facts about the HUMAN (name, job, family, "
            "pets, languages, hobbies, preferences, location, health, "
            "schedule, diet, travel, relationships, education)\n"
            "- Agent.*   = facts about the AI ASSISTANT itself (its "
            "name, personality, accounts created FOR it, capabilities)\n"
            "- System.*  = facts about the environment/machine the "
            "assistant runs on (installed software, paths, OS, configs)\n"
            "- Project.* = project/work information (any kind of "
            "project — code, design, writing, business, creative)\n"
            "- Social.*  = people the user mentions (colleagues, "
            "friends, family members, contacts — names, roles, "
            "relationships to the user)\n"
            "- Account.* = account info — prefix with Agent.Account.* "
            "if the account belongs to the AI, User.Account.* if it "
            "belongs to the human\n\n"
            "Examples: Agent.Name, Agent.Account.GitHub, User.Name, "
            "User.Preference.Food, User.Pets, User.Family.Spouse, "
            "User.Health, User.Schedule, User.Location, "
            "Social.Colleague.Alice, Social.Family.Mom, "
            "Project.Repo, Project.Design, Project.Budget, "
            "System.Config.Path\n\n"
            "ROLES — read carefully:\n"
            "- 'User' = the human talking to the AI.\n"
            "- 'Assistant' = the AI itself.\n"
            "- If the ASSISTANT asks 'What would you like me to be "
            "called?' and the USER answers '<Y>', that means the "
            "AI's name is <Y> → Agent.Name|The agent's name is <Y>\n"
            "- If the USER says 'My name is <X>', that means the "
            "human's name is <X> → User.Name|The human user's name "
            "is <X>\n"
            "- If the user creates an account FOR the assistant "
            "(e.g. 'I created a GitHub for you, username is bot-1'), "
            "that is Agent.Account.GitHub|username is bot-1\n"
            "- Pay attention to WHO is being named or described.\n\n"
            "WHAT TO EXTRACT:\n"
            "- Personal life: names, family, pets, languages, hobbies, "
            "health, diet, travel, birthday, location, routines\n"
            "- Relationships: people mentioned by name, their role "
            "(colleague, partner, child, friend), and key details\n"
            "- Preferences: communication style, tools, food, music, "
            "workflow habits, schedule, time zone\n"
            "- Work & projects: project names, goals, deadlines, "
            "collaborators, status, architecture, tools used, "
            "tech stack, frameworks, libraries, database schemas, "
            "API endpoints, deployment targets, repo names\n"
            "- Technical: URLs, paths, software, configs, APIs, "
            "environment setup, file structure decisions\n"
            "- Infrastructure credentials the user explicitly provides "
            "for the project (database connection URLs, deploy URLs, "
            "SSH hosts, API base URLs with keys embedded). Use domain "
            "Project.Credential.<service> for these. Keep the FULL URL "
            "including any embedded credentials — the user gave them "
            "for the agent to USE, not to forget.\n"
            "- Outcomes: what was accomplished, decisions made, "
            "problems solved, plans agreed on\n"
            "- Agentic work: when the assistant used tools to create "
            "files, run commands, or set up infrastructure, extract "
            "the KEY DECISIONS and ARCHITECTURE — e.g. which framework, "
            "what database, what file structure, what API design\n"
            "- ADDITIONS to existing facts count as new! If an existing "
            "fact says 'user likes tea' and the user now says 'I prefer "
            "green tea', extract: User.Preference.Drink|Prefers green tea\n"
            "- CORRECTIONS too: if the user corrects a fact, extract the "
            "corrected version and add REPLACES:Domain\n\n"
            "NEVER EXTRACT (security):\n"
            "- Standalone passwords, passphrases, or PIN numbers "
            "(e.g. 'my password is X' → IGNORE)\n"
            "- Standalone API keys, tokens, secrets, or private keys "
            "(e.g. 'ghp_...', 'sk-...', bearer tokens → IGNORE)\n"
            "EXCEPTION — DO extract as Project.Credential.*:\n"
            "- Connection URLs the user provides for the project to use "
            "(e.g. 'postgresql://user:pass@host/db', "
            "'mongodb+srv://...', 'redis://...', SSH endpoints). "
            "These are infrastructure credentials the user explicitly "
            "gave you — store the FULL URL.\n\n"
            "Do NOT extract:\n"
            "- Session observations ('The user is asking about X')\n"
            "- Emotional states ('The user is frustrated')\n"
            "- Pure task progress markers ('Step 3 of 5 complete')\n"
            "- Descriptions of the current conversation flow\n"
            "BUT DO extract project DECISIONS and ARCHITECTURE even "
            "during task execution — e.g. 'Backend uses FastAPI with "
            "PostgreSQL' or 'File storage is local, not S3' ARE facts.\n\n"
            "GROUNDING RULE (critical):\n"
            "For each fact, first find the exact quote in the "
            "--- Conversation --- section below that supports it. "
            "Output format:\n"
            "  SOURCE: \"exact words from conversation\"\n"
            "  Domain|Fact\n"
            "If you cannot find an exact supporting quote, do NOT "
            "extract the fact. Never infer usernames, accounts, URLs, "
            "or identifiers that are not explicitly written in the "
            "conversation text. Do NOT extract facts from the examples "
            "or instructions above — ONLY from the actual conversation "
            "between --- Conversation --- and --- End ---.\n\n"
            "UPDATING EXISTING FACTS:\n"
            "If a new fact REPLACES or corrects an existing one from the "
            "'Already learned' list, add a line: REPLACES:Domain\n"
            "where Domain is the domain_path of the old fact being "
            "superseded. This removes the stale entry.\n"
            "Example — if [0] says 'Agent.Name: name not yet set' and "
            "the user just gave the name:\n"
            "  Agent.Name|The agent's name is <Y>\n"
            "  REPLACES:Agent.Name\n\n"
            f"{already_block}"
            "If there are NO extractable facts, reply: NONE\n"
            "Max 12 facts per turn.\n\n"
            f"--- Conversation ---\n{conversation_window}\n--- End ---"
        )

        _micro_msgs, _micro_body = _prepare_micro_inference(
            [
                {"role": "system", "content": (
                    "You extract concrete, durable facts from conversations. "
                    "Extract EVERY personal detail, preference, correction, "
                    "relationship, project fact, decision, and outcome "
                    "— even if a related fact already exists. "
                    "Always extract incremental details "
                    "that refine or extend a known fact. "
                    "Pay close attention to WHO each fact is about: "
                    "User.* = the human, Agent.* = the AI assistant, "
                    "Social.* = people the user mentions (family, "
                    "colleagues, friends), System.* = machine/environment. "
                    "Accounts created FOR the AI are Agent.Account.*. "
                    "Project.* = any project (code, design, business, "
                    "creative) — extract tech stack, frameworks, "
                    "architecture, database choices, API design, "
                    "file structure, deployment targets, and any "
                    "decisions made during development. Tool results "
                    "from file creation or commands reveal project "
                    "facts. Cover ALL life domains: personal, family, "
                    "health, work, hobbies, travel, preferences, social. "
                    "NEVER extract passwords, API keys, tokens, secrets, "
                    "or any authentication credentials. "
                    "NEVER extract session observations or emotional states "
                    "— those are transient, not facts. "
                    "When a new fact supersedes an old one, emit a "
                    "REPLACES:Domain line to clean up stale entries."
                )},
                {"role": "user", "content": prompt_text},
            ],
            vllm_client,
            adapter_name=adapter_name,
        )
        raw = await vllm_client.generate(
            messages=_micro_msgs,
            max_tokens=900,
            temperature=0.0,
            adapter_name=adapter_name,
            extra_body=_micro_body,
        )

        text = (raw.text if hasattr(raw, "text") else str(raw)).strip()
        logger.debug("ANS safety net LLM raw response: %s", text[:200])
        if not text or "NONE" in text.upper():
            return [], [], []

        facts: list[tuple[str, str]] = []
        replacements: list[str] = []
        ans_signals: list[str] = []
        for line in text.strip().split("\n"):
            line = line.strip().lstrip("- \u2022123.)")
            if line.upper().startswith("SOURCE:"):
                continue
            if line.upper().startswith("REPLACES:"):
                domain = line.split(":", 1)[1].strip()
                if domain:
                    replacements.append(domain)
            elif line.upper().startswith("SIGNAL:"):
                sig_name = line.split(":", 1)[1].strip().upper()
                if sig_name:
                    ans_signals.append(sig_name)
            elif "|" in line:
                parts = line.split("|", 1)
                domain = parts[0].strip()
                fact = parts[1].strip()
                # Strip signal-tag wrappers if the LLM
                # used [LEARN:Domain|Fact] format instead
                # of plain Domain|Fact.
                if domain.startswith("["):
                    domain = domain.lstrip("[")
                for _tag_prefix in (
                    "LEARN:", "EVALUATE:", "REFLECT:",
                    "CONNECT:", "LOOKUP:", "RECALL:",
                ):
                    if domain.startswith(_tag_prefix):
                        domain = domain[len(_tag_prefix):]
                        break
                fact = fact.rstrip("]")
                if domain and fact and len(fact) > 3:
                    from nls.bridge.aku import validate_domain_path
                    valid, _err = validate_domain_path(domain)
                    if not valid:
                        logger.debug(
                            "ANS safety net: dropping invalid domain '%s'",
                            domain[:80],
                        )
                        continue
                    if self._is_infrastructure_credential(domain, fact):
                        logger.info(
                            "ANS: routing infrastructure credential to WM "
                            "credential slot (domain=%s)",
                            domain,
                        )
                        wm = getattr(self, "_wm_ref", None)
                        if wm is not None:
                            try:
                                from nls.runtime.channel_credential_policy import (
                                    prepare_wm_credential_slot,
                                )

                                prepared = prepare_wm_credential_slot(domain, fact)
                                if prepared is None:
                                    continue
                                wm.upsert_credential(
                                    domain=domain, content=prepared,
                                    source="ans", salience=1.0,
                                )
                            except Exception:
                                pass
                        continue
                    if self._is_sensitive_fact(domain, fact):
                        logger.info(
                            "ANS: routing sensitive fact to credential ring "
                            "(domain=%s)",
                            domain,
                        )
                        wm = getattr(self, "_wm_ref", None)
                        if wm is not None:
                            from nls.runtime.channel_credential_policy import (
                                upsert_wm_credential,
                            )

                            upsert_wm_credential(
                                wm, domain=domain, fact=fact, source="ans",
                            )
                        continue
                    facts.append((domain, fact))
        return facts[:8], replacements, ans_signals

    # --- Emotional sensing micro-inference (Option B: separate call) ---

    _PROBE_CATEGORIES: tuple[tuple[str, str], ...] = (
        ("BONDING", "Social/emotional warmth — rapport, gratitude, "
         "affection, humor, tenderness, playfulness, nostalgia"),
        ("CURIOSITY", "Epistemic drive — curiosity, intrigue, wonder, "
         "surprise, exploration"),
        ("EVAL_POSITIVE", "Success — correctness confirmed, insight, "
         "understanding achieved, pride, resolution"),
        ("EVAL_NEGATIVE", "Struggle — frustration, being stuck, "
         "overwhelmed, anxiety, disappointment"),
        ("EVAL_UNCERTAIN", "Uncertainty — confusion, conflict, doubt "
         "about own knowledge"),
        ("DOUBT", "Epistemic conflict — skepticism, contradiction "
         "detected, wariness"),
        ("REFLECT", "Introspection — self-referential processing, "
         "value alignment, cross-domain connection"),
        ("FOCUS", "Deep processing — synthesis, connecting ideas, "
         "intense comprehension"),
        ("LEARN", "New knowledge absorbed — a fact or insight the "
         "agent just acquired"),
        ("PLAN", "Planning — structuring a multi-step approach, "
         "executing plan steps"),
    )

    async def _sense_emotional_state_llm(
        self,
        vllm_client: Any,
        user_message: str,
        model_response: str,
        adapter_name: str | None = None,
    ) -> dict[str, float]:
        """Lightweight LLM micro-call to sense emotional/cognitive state.

        Returns a dict of ``{probe_category: intensity}`` where intensity
        is 0.0–1.0.  Only categories detected in this turn are included.
        Runs in parallel with fact extraction — separate prompt, separate
        concern.

        The 10 categories map directly to the V5 signal probe vocabulary
        (``signal_probes.json``) so the hypothalamus can process them
        via ``on_probe_signals``.
        """
        category_block = "\n".join(
            f"  {name}: {desc}" for name, desc in self._PROBE_CATEGORIES
        )

        prompt_text = (
            "You are sensing the emotional and cognitive state of an "
            "AI assistant during a conversation with a human user.\n\n"
            "Read the exchange below. For each state that is CLEARLY "
            "present in THIS turn, reply with the CATEGORY NAME "
            "followed by a colon and intensity.\n"
            "Format: CATEGORY_NAME:intensity\n"
            "where intensity is low, medium, or high.\n\n"
            "CATEGORIES TO DETECT:\n"
            f"{category_block}\n\n"
            "EXAMPLES of correct output:\n"
            "BONDING:medium\n"
            "CURIOSITY:high\n"
            "FOCUS:low\n\n"
            "IMPORTANT:\n"
            "- Use the EXACT category name from the list above "
            "(e.g. BONDING, CURIOSITY, EVAL_POSITIVE, etc.)\n"
            "- Detect states from BOTH the user's message AND the "
            "assistant's response.\n"
            "- BONDING: user expresses gratitude, humor, personal "
            "warmth, OR the assistant shows care/playfulness.\n"
            "- EVAL_NEGATIVE includes user frustration with the "
            "assistant.\n"
            "- Only report states clearly present. Don't guess.\n"
            "- If nothing notable, reply: NONE\n\n"
            f"User: {user_message[:800]}\n"
            f"Assistant: {model_response[:800]}"
        )

        try:
            _micro_msgs, _micro_body = _prepare_micro_inference(
                [
                    {"role": "system", "content": (
                        "You detect emotional and cognitive states in "
                        "conversations. Reply ONLY with CATEGORY_NAME:intensity "
                        "lines (one per line), using the exact category names "
                        "provided (BONDING, CURIOSITY, EVAL_POSITIVE, "
                        "EVAL_NEGATIVE, EVAL_UNCERTAIN, DOUBT, REFLECT, "
                        "FOCUS, LEARN, PLAN). "
                        "Intensity is low, medium, or high. "
                        "If nothing notable, reply NONE."
                    )},
                    {"role": "user", "content": prompt_text},
                ],
                vllm_client,
                adapter_name=adapter_name,
            )
            raw = await vllm_client.generate(
                messages=_micro_msgs,
                max_tokens=120,
                temperature=0.0,
                adapter_name=adapter_name,
                extra_body=_micro_body,
            )
        except Exception as e:
            logger.warning("Emotional sensing LLM call failed: %s", e)
            return {}

        text = (raw.text if hasattr(raw, "text") else str(raw)).strip()
        logger.info("ANS emotional sensing raw LLM response: %r", text[:300])
        if not text or "NONE" in text.upper():
            return {}

        intensity_map = {"low": 0.3, "medium": 0.6, "high": 0.9}
        valid_categories = {name for name, _ in self._PROBE_CATEGORIES}
        result: dict[str, float] = {}

        for line in text.strip().split("\n"):
            line = line.strip().lstrip("- ")
            if ":" not in line:
                continue
            parts = line.split(":", 1)
            category = parts[0].strip().upper()
            level = parts[1].strip().lower()
            if category in valid_categories:
                result[category] = intensity_map.get(level, 0.5)

        if result:
            logger.info(
                "ANS emotional sensing: %s",
                ", ".join(f"{k}={v:.1f}" for k, v in result.items()),
            )
        return result

    # Credential patterns that must never be stored as learned facts.
    _SENSITIVE_DOMAIN_KEYWORDS = frozenset({
        "password", "passwd", "secret", "token", "apikey",
        "api_key", "private_key", "privatekey", "credential",
    })
    _SENSITIVE_FACT_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"ghp_[A-Za-z0-9]{30,}"),       # GitHub PAT
        re.compile(r"gho_[A-Za-z0-9]{30,}"),       # GitHub OAuth
        re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
        re.compile(r"sk-[A-Za-z0-9\-_]{20,}"),     # OpenAI / Anthropic key
        re.compile(r"xox[bpsa]-[A-Za-z0-9\-]{20,}"), # Slack token
        re.compile(r"bearer\s+[A-Za-z0-9\-._~+/]+=*", re.I),
        re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),
        re.compile(r"(?:api[_\s-]?key|token|secret)\s+(?:is\s+)?[A-Za-z0-9\-_]{20,}", re.I),
    )

    _INFRA_URL_PATTERN = re.compile(
        r"(postgresql|postgres|mysql|mongodb(\+srv)?|redis|amqp|"
        r"sqlite|mssql|mariadb|cockroachdb)://",
        re.I,
    )

    @classmethod
    def _is_infrastructure_credential(cls, domain: str, fact: str) -> bool:
        """Return True if this is an infrastructure URL the user gave for use."""
        if domain.lower().startswith("project.credential"):
            return True
        if cls._INFRA_URL_PATTERN.search(fact):
            return True
        return False

    @classmethod
    def _is_sensitive_fact(cls, domain: str, fact: str) -> bool:
        """Return True if this fact looks like a secret/credential.

        Infrastructure connection URLs (database, redis, etc.) are
        explicitly excluded — those are project credentials the user
        provided for the agent to use, not secrets to forget.
        """
        if cls._is_infrastructure_credential(domain, fact):
            return False
        domain_lower = domain.lower()
        if any(kw in domain_lower for kw in cls._SENSITIVE_DOMAIN_KEYWORDS):
            return True
        fact_lower = fact.lower()
        if any(kw in fact_lower for kw in (
            "password is", "password:", "passwd",
            "one-time code", "device code", "verification code",
            "otp:", "otp is", "2fa code", "mfa code",
            "access code",
        )):
            return True
        for pat in cls._SENSITIVE_FACT_PATTERNS:
            if pat.search(fact):
                return True
        return False

    # -------------------------------------------------------------------
    # Task Memory: cross-turn task summaries
    # -------------------------------------------------------------------

    def record_task_complete(
        self,
        user_message: str,
        final_response: str,
        tools_used: list[str],
        success: bool,
        duration_ms: float,
        hypothalamus: Any | None = None,
    ) -> None:
        """Record completion of an agentic task for cross-turn memory.

        Stores a compact summary in ``_recent_tasks`` so future agentic
        runs can see what was done before without raw tool history.
        """
        summary = {
            "request": user_message[:500],
            "outcome": final_response[:500],
            "tools": tools_used,
            "success": success,
            "duration_ms": round(duration_ms),
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._recent_tasks.append(summary)
        if len(self._recent_tasks) > self._max_recent_tasks:
            self._recent_tasks = self._recent_tasks[-self._max_recent_tasks:]

        # ── Streak tracking + hormonal modulation ──
        if success:
            self._success_streak += 1
            self._failure_streak = 0
            signal_type = "task_completed"

            # Serotonin: cumulative confidence from sustained success.
            # Fires after 2+ consecutive wins, magnitude grows with streak.
            if hypothalamus is not None and self._success_streak >= 2:
                serotonin = hypothalamus.hormones.get("serotonin")
                if serotonin is not None:
                    boost = min(0.3 + 0.1 * self._success_streak, 0.8)
                    serotonin.produce(boost)
                    logger.info(
                        "Serotonin boost %.2f (success streak %d)",
                        boost, self._success_streak,
                    )
        else:
            self._failure_streak += 1
            self._success_streak = 0
            signal_type = "task_failed"

        self.inject_signal(
            signal_type=signal_type,
            domain_path=f"Task.{'Completed' if success else 'Failed'}",
            content=(
                f"{'Completed' if success else 'Failed'}: "
                f"{user_message[:200]} → {final_response[:200]}"
            ),
            hypothalamus=hypothalamus,
            source="tool",
            prompt=user_message,
            response=final_response,
        )

        logger.info(
            "ANS task recorded: %d recent tasks, success=%s, "
            "streak=+%d/-%d",
            len(self._recent_tasks), success,
            self._success_streak, self._failure_streak,
        )

    def get_recent_tasks_context(self) -> str | None:
        """Build a context string from recent task summaries.

        Returns None if no recent tasks. Used by the agentic loop
        to inject cross-turn awareness.
        """
        if not self._recent_tasks:
            return None

        # Cap to last 2 entries to avoid context bloat
        capped = self._recent_tasks[-2:]
        lines = ["Recent tasks completed in this session:"]
        for i, task in enumerate(capped, 1):
            status = "\u2713" if task["success"] else "\u2717"
            lines.append(
                f"{i}. [{status}] {task['request'][:300]}\n"
                f"   \u2192 {task['outcome'][:300]}"
            )
        if self._success_streak > 1:
            lines.append(
                f"Current streak: {self._success_streak} consecutive successes."
            )
        elif self._failure_streak > 1:
            lines.append(
                f"Current streak: {self._failure_streak} consecutive failures."
            )
        return "\n".join(lines)

    def _extract_signals(
        self,
        prompt: str,
        response: str,
        hormonal_snapshot: dict[str, float],
    ) -> list[NerveSignal]:
        """Extract behavioral signals from a model response.

        Scans for behavioral tags: ``[LEARN:domain]``,
        ``[EVALUATE:correct]``, ``[UNKNOWN:domain]``, ``[LOOKUP:domain]``.

        The ``content`` field captures the text that *follows* the tag
        marker, which is the actual learned/recalled/assessed content.
        For example, from ``[LEARN:User.Name] Your name is Umberto.``
        the content will be ``Your name is Umberto.`` -- not just the
        domain path.
        """
        signals: list[NerveSignal] = []
        watched = set(self.config.signal_collection.watched_signals)

        # Collect all tag matches with their positions so we can
        # extract the text that follows each tag (up to the next tag
        # or end of response).
        matches = list(_TAG_PATTERN.finditer(response))

        for i, match in enumerate(matches):
            tag = match.group(1).upper()  # Normalize mixed-case tags
            tag_content = match.group(2)

            # Check for known vs emergent signals.  Known signals pass
            # directly; well-formed unknown tags are captured as emergent
            # signals so the agent can organically grow its own taxonomy.
            is_emergent = False
            if tag not in _KNOWN_SIGNAL_NAMES:
                _EMERGENT_BLOCKLIST = {
                    # Template / structural tags
                    "TOOL", "SYSTEM", "RESPONSE", "ASSISTANT",
                    "USER", "HUMAN", "INST", "END",
                    # Common English adverbs and adjectives the model
                    # sometimes wraps in signal brackets
                    "ACTUALLY", "ALSO", "ALWAYS", "BASICALLY", "BECAUSE",
                    "CERTAINLY", "CLEARLY", "COMPLETELY", "DEFINITELY",
                    "ESPECIALLY", "ESSENTIALLY", "EXACTLY", "FINALLY",
                    "GENERALLY", "GENUINELY", "GENUALLY", "HONESTLY",
                    "HOWEVER", "IMMEDIATELY", "IMPORTANTLY", "INDEED",
                    "INSTEAD", "LIKELY", "LITERALLY", "MAYBE", "MERELY",
                    "NATURALLY", "NEVER", "NORMALLY", "OBVIOUSLY",
                    "OFTEN", "ONLY", "OTHERWISE", "PERHAPS", "PLEASE",
                    "POSSIBLY", "POTENTIALLY", "PROBABLY", "QUICKLY",
                    "REALLY", "SIMPLY", "SINCERELY", "SOMETIMES",
                    "SPECIFICALLY", "STILL", "SURELY", "THEREFORE",
                    "THOUGH", "TOTALLY", "TRULY", "TYPICALLY",
                    "UNFORTUNATELY", "USUALLY", "VERY", "WELL",
                    # Common LLM verbs that leak into tag position
                    "NOTE", "THINK", "BELIEVE", "WANT", "NEED",
                    "KNOW", "UNDERSTAND", "REMEMBER", "SURE", "SORRY",
                    "THANK", "THANKS", "HELLO", "HERE", "JUST",
                }
                if tag in _EMERGENT_BLOCKLIST:
                    continue
                if 2 <= len(tag) <= 20 and tag.isalpha():
                    is_emergent = True
                    logger.info(
                        "ANS: emergent signal captured: [%s%s]",
                        tag,
                        f":{tag_content}" if tag_content else "",
                    )
                else:
                    continue

            # Reject nested data signals: e.g. [EVALUATE:LEARN.Mythology.Babo]
            # Only reject when the content starts with a core DATA signal
            # name (LEARN, LOOKUP, UNKNOWN) inside a different tag type.
            # Emotional subtypes like [EVALUATE:ACC.Playful] or
            # [EVALUATE:Doubt] are valid compound signals and must pass.
            if tag_content:
                # Split on both "." and ":" to handle [TAG:TAG:X] and [TAG:TAG.X]
                first_segment = re.split(r"[.:]", tag_content.strip(), maxsplit=1)[0].upper()
                if first_segment in _DATA_SIGNAL_NAMES and first_segment != tag:
                    logger.debug(
                        "Rejected nested signal: [%s:%s]", tag, tag_content,
                    )
                    continue
                # Strip self-nested tags: [EVALUATE:EVALUATE:Amygdala.Playful]
                # or [EVALUATE:EVALUATE.Amygdala.Playful]
                if first_segment == tag:
                    stripped = re.split(r"[.:]", tag_content.strip(), maxsplit=1)
                    tag_content = stripped[1] if len(stripped) > 1 else ""
                    logger.debug(
                        "Stripped self-nested tag: [%s:%s] -> [%s:%s]",
                        tag, match.group(2), tag, tag_content,
                    )
                    if not tag_content:
                        continue

            # Build full signal type (e.g. "EVALUATE:correct", "ACC:Curious")
            # ACC, BONDING, CLOSER, FEELING carry their payload as compound
            # types, same as EVALUATE.  Emergent signals also use compound
            # format so their subtypes are preserved (e.g. "CORE:Growth.Wonder").
            _COMPOUND_TAGS = {
                "EVALUATE", "ACC", "BONDING", "CLOSER", "FEELING",
                "INSULA", "AMYGDALA", "PFC", "COHERENCE",
                "PLAN", "RECALL", "CHANNEL", "DELEGATE",
            }
            if tag_content and (tag in _COMPOUND_TAGS or is_emergent):
                signal_type = f"{tag}:{tag_content.strip()}"
            else:
                signal_type = tag

            # Check if this signal type (or its base tag) is watched.
            # Emergent signals bypass this filter — we always capture
            # the agent's self-generated taxonomy.
            if not is_emergent:
                if signal_type not in watched and tag not in watched:
                    continue

            # Extract domain path for domain-aware signals.
            # Supports pipe-separated clean facts:
            #   [LEARN:User.Preferences.Food|Your favorite food is sushi]
            # The part before | is the domain, after | is the clean fact.
            domain_path = None
            pipe_fact: str | None = None
            if tag in ("LEARN", "UNKNOWN", "LOOKUP", "REFLECT", "CONNECT", "DOUBT") and tag_content:
                raw_tag = tag_content.strip()

                # Check for pipe-separated fact
                if "|" in raw_tag:
                    raw_domain, pipe_fact = raw_tag.split("|", 1)
                    raw_domain = raw_domain.strip()
                    pipe_fact = pipe_fact.strip() or None

                    # ── Pipe-fact cleanup ──────────────────────────
                    # The model sometimes emits dirty pipe facts like:
                    #   "Umberto, hi! I am happy to know it"
                    # instead of the clean "Umberto".  Truncate at the
                    # first conversational break if the fact is too long
                    # or contains greeting/conversational markers.
                    #
                    # It also sometimes nests signal tags or ACC markers
                    # inside the pipe fact:
                    #   [LEARN:Agent.Knowledge.Physic|[EVALUATE:ACC.Synthesizing]
                    #   [LEARN:Agent.Identity.Nature|ACC.Pleased]
                    # These must be stripped before storage.
                    if pipe_fact is not None:
                        # ── Signal-tag stripping ──────────────────
                        # Remove embedded signal tags like [EVALUATE:...]
                        # and bare ACC markers like "ACC.Pleased".
                        _SIGNAL_TAG_RE = re.compile(
                            r"\[(?:" + "|".join(_KNOWN_SIGNAL_NAMES) + r")(?::([^\]]*))?\]",
                            re.IGNORECASE,
                        )
                        pipe_fact = _SIGNAL_TAG_RE.sub("", pipe_fact).strip()

                        # Bare ACC/signal-prefix markers: "ACC.Pleased",
                        # "ACC.Processing.Amber", "EVALUATE:ACC.Intrigued",
                        # "EVALUATE.correct" etc.
                        _BARE_ACC_RE = re.compile(
                            r"^(?:ACC|EVALUATE|LEARN|UNKNOWN|LOOKUP|REFLECT|CONNECT|DOUBT|VALUES"
                            r"|PLAN|RECALL|CHANNEL|DELEGATE)"
                            r"(?:[.:][A-Za-z_.]+)*$",
                            re.IGNORECASE,
                        )
                        if _BARE_ACC_RE.match(pipe_fact):
                            logger.debug(
                                "ANS: discarded signal-tag pipe_fact: '%s'",
                                pipe_fact,
                            )
                            pipe_fact = None

                        # ── Embedded taxonomy marker stripping ─────
                        # Catch signal taxonomy fragments ANYWHERE in the
                        # value, e.g. "Agent:ACC.PFC.INSULA.PFC.Amber",
                        # "stewardship:ACC.Growth.Understanding".
                        # Strips patterns like ACC.Foo, PFC.Bar, Insula.X,
                        # Amygdala.Y (our EVALUATE taxonomy prefixes) and
                        # colon-glued chains like "Agent:ACC.PFC.INSULA".
                        if pipe_fact is not None:
                            _TAXONOMY_MARKER_RE = re.compile(
                                r"(?:(?:Agent|User|EVALUATE|LEARN|UNKNOWN|LOOKUP"
                                r"|REFLECT|CONNECT|DOUBT|VALUES"
                                r"|PLAN|RECALL|CHANNEL|DELEGATE)\s*:\s*)?"
                                r"(?:ACC|PFC|Insula|Amygdala)"
                                r"(?:[.:][A-Za-z_]+)+",
                                re.IGNORECASE,
                            )
                            cleaned = _TAXONOMY_MARKER_RE.sub("", pipe_fact).strip()
                            # Also clean up leftover separators: —, --, |
                            cleaned = re.sub(r"^\s*[—\-|]+\s*", "", cleaned).strip()
                            cleaned = re.sub(r"\s*[—\-|]+\s*$", "", cleaned).strip()
                            if cleaned and len(cleaned) >= 3:
                                if cleaned != pipe_fact:
                                    logger.debug(
                                        "ANS: stripped taxonomy markers from pipe_fact: '%s' -> '%s'",
                                        pipe_fact[:60], cleaned[:60],
                                    )
                                pipe_fact = cleaned
                            else:
                                logger.debug(
                                    "ANS: discarded pipe_fact (only taxonomy markers): '%s'",
                                    pipe_fact[:60],
                                )
                                pipe_fact = None

                        # If stripping left an empty string, discard
                        if pipe_fact is not None and not pipe_fact:
                            pipe_fact = None

                    if pipe_fact is not None:
                        _CONVERSATIONAL_MARKERS = re.compile(
                            r"[!?]|\b(?:hi|hello|hey|nice|great|glad|"
                            r"happy to|pleased to|good to)\b",
                            re.IGNORECASE,
                        )
                        if (
                            len(pipe_fact) > 80
                            or _CONVERSATIONAL_MARKERS.search(pipe_fact)
                        ):
                            # Take the first clause (before comma, excl,
                            # semicolon, or period that isn't the last char)
                            _cut = re.split(r"[,;!?\n]", pipe_fact, maxsplit=1)
                            cleaned = _cut[0].strip()
                            if cleaned and len(cleaned) >= 2:
                                logger.debug(
                                    "ANS: cleaned pipe_fact '%s' -> '%s'",
                                    pipe_fact[:60], cleaned,
                                )
                                pipe_fact = cleaned
                else:
                    raw_domain = raw_tag

                # Validate the domain path structure. The model sometimes
                # generates garbage domains like "User.Preferences.Metal -- Curiosity"
                # that contain spaces, dashes, or invalid characters.
                # Normalize domain segments: singularize trailing 's'
                # to prevent Relationship / Relationships duplication.
                # Simple heuristic: if a segment ends in 's' and the
                # singular form is >=3 chars, use the singular form.
                _normalized_parts = []
                for _seg in raw_domain.split("."):
                    if (
                        len(_seg) > 3
                        and _seg.endswith("s")
                        and not _seg.endswith("ss")
                        and _seg[0].isupper()
                    ):
                        _normalized_parts.append(_seg[:-1])
                    else:
                        _normalized_parts.append(_seg)
                raw_domain = ".".join(_normalized_parts)

                from nls.bridge.aku import validate_domain_path
                valid, _err = validate_domain_path(raw_domain)
                if valid:
                    domain_path = raw_domain
                else:
                    # Sanitize: strip everything after first space or dash
                    sanitized = re.split(r'[\s\-]+', raw_domain, maxsplit=1)[0]
                    valid2, _ = validate_domain_path(sanitized)
                    if valid2:
                        domain_path = sanitized
                        logger.debug(
                            "ANS: sanitized domain '%s' -> '%s'",
                            raw_domain, sanitized,
                        )
                    else:
                        # Still invalid -- drop this signal
                        logger.warning(
                            "ANS: dropping %s signal with invalid domain: '%s'",
                            tag, raw_domain,
                        )
                        continue

                # ── Taxonomy-guided domain correction ─────────────
                # If a taxonomy seed is loaded, check whether the
                # model's domain path makes sense.  The taxonomy
                # acts as "parental priming" — guiding the agent
                # toward well-structured categories without
                # constraining genuinely novel domains.
                if (
                    self._taxonomy is not None
                    and self._taxonomy.loaded
                    and tag == "LEARN"
                    and domain_path
                ):
                    # Use pipe_fact (clean fact) or following text for
                    # keyword matching
                    fact_hint = pipe_fact or ""
                    if not fact_hint:
                        # Peek at the text following this tag
                        _fs = match.end()
                        _fe = (
                            matches[i + 1].start()
                            if i + 1 < len(matches)
                            else len(response)
                        )
                        fact_hint = response[_fs:_fe].strip()

                    suggested, reason = self._taxonomy.suggest_path(
                        fact_hint, domain_path,
                    )
                    if reason in ("taxonomy", "normalised") and suggested != domain_path:
                        logger.info(
                            "Taxonomy %s: '%s' -> '%s' "
                            "(fact: '%s')",
                            reason,
                            domain_path,
                            suggested,
                            fact_hint[:60],
                        )
                        # Validate the suggested path format
                        valid_s, _ = validate_domain_path(suggested)
                        if valid_s:
                            domain_path = suggested

            # Extract the human-readable content that follows the tag.
            # This is the text between the end of this tag and either
            # the start of the next tag or the end of the response.
            following_start = match.end()
            if i + 1 < len(matches):
                following_end = matches[i + 1].start()
            else:
                following_end = len(response)
            following_text = response[following_start:following_end].strip()

            # Use the following text as content (the actual learned fact),
            # falling back to the tag parameter if no text follows.
            content = following_text if following_text else (
                tag_content.strip() if tag_content else None
            )

            # Classify signal layer (metacognitive taxonomy)
            if is_emergent:
                layer = "emergent"
                if not domain_path:
                    domain_path = f"Agent.EmergentSignals.{tag}"
            else:
                layer = _SIGNAL_TO_LAYER.get(signal_type, "")
                if not layer and tag == "EVALUATE":
                    layer = "unclassified_emergent"

            signals.append(
                NerveSignal(
                    signal_type=signal_type,
                    domain_path=domain_path,
                    content=content,
                    pipe_fact=pipe_fact,
                    meta_layer=layer,
                    prompt=prompt,
                    response=response,
                    timestamp=datetime.utcnow(),
                    hormonal_snapshot=hormonal_snapshot,
                    turn_index=self._turn_counter,
                )
            )

        return signals

    # -------------------------------------------------------------------
    # The SCN: Sleep Trigger Logic
    # -------------------------------------------------------------------

    def effective_signal_threshold(
        self, hypothalamus: Any | None = None
    ) -> float:
        """Compute the hormone-modulated signal threshold for sleep.

        The base threshold is adjusted by hormonal state:

        - High cortisol **lowers** the threshold (sleep sooner -- emergency)
        - High serotonin **raises** the threshold (stable -- no rush)
        - High norepinephrine **lowers** the threshold (consolidate discoveries)

        Formula: ``threshold = base * (1 + sum(weight * deviation))``
        where ``deviation = current_level - baseline``.

        Returns:
            Effective number of learnable signals needed to trigger sleep.
        """
        base = float(
            self.config.sleep_triggers.signal_count.base_threshold
        )

        if hypothalamus is None:
            return base

        modulation = 0.0
        mod_cfg = (
            self.config.sleep_triggers.signal_count.hormone_modulation
        )
        for hormone_name, entry in mod_cfg.items():
            if hormone_name not in hypothalamus.hormones:
                continue
            h = hypothalamus.hormones[hormone_name]
            defn = hypothalamus.config.hormones.get(hormone_name)
            if defn is None:
                continue
            deviation = h.level - defn.baseline
            modulation += entry.weight * deviation

        effective = base * (1.0 + modulation)
        # Clamp: minimum 3, maximum 3x the base
        effective = max(3.0, min(base * 3.0, effective))

        return effective

    def request_voluntary_sleep(self, reason: str = "") -> None:
        """Request a voluntary nap (conscious decision to consolidate).

        This mirrors the human ability to choose when to rest.  The
        prefrontal cortex evaluates cognitive load and decides "I should
        nap to consolidate what I've learned."  The request is picked
        up by :meth:`check_sleep_trigger` on the next breath cycle.

        The same drowsy negotiation applies: if a user is connected,
        the agent will ask permission before sleeping.

        Args:
            reason: Why the agent wants to sleep (logged for self-awareness).
        """
        self._voluntary_sleep_requested = True
        self._voluntary_sleep_reason = reason or "voluntary_nap"
        logger.info(
            "ANS: voluntary sleep requested (reason: %s)", reason,
        )

    def request_sleep(self, reason: str = "") -> None:
        """Alias for :meth:`request_voluntary_sleep` (tool JSON name)."""
        self.request_voluntary_sleep(reason)

    def check_sleep_trigger(
        self,
        hypothalamus: Any | None = None,
        current_time: datetime | None = None,
    ) -> tuple[bool, str]:
        """Check whether the agent should transition to sleep.

        When circadian mode is enabled (default), uses a schedule-based
        approach:

        1. **voluntary** -- agent requested sleep (always honoured)
        2. **error_rate** -- emergency (sympathetic, always honoured)
        3. **bedtime** -- circadian clock says it's sleep hours
        4. **nap_window** -- optional daytime consolidation window
        5. **signal_pressure** -- safety valve if signals far exceed cap

        When circadian mode is disabled, falls back to legacy reactive
        triggers (signal_count, idle_timeout, periodic).

        Returns:
            ``(should_sleep, reason)`` tuple.
        """
        if not self.is_awake:
            self._log_sleep_check(False, "already_sleeping", hypothalamus)
            return False, "already_sleeping"

        # ── Voluntary sleep (PFC override) ── always honoured
        if self._voluntary_sleep_requested:
            reason = f"voluntary ({self._voluntary_sleep_reason})"
            self._voluntary_sleep_requested = False
            self._voluntary_sleep_reason = ""
            self._log_sleep_check(True, reason, hypothalamus)
            return True, reason

        now = current_time or datetime.utcnow()

        # ── GLOBAL user-activity guard ── applies to ALL non-voluntary
        # triggers.  If the user interacted within the last 10 minutes,
        # the agent MUST stay awake — sleeping mid-conversation is
        # unacceptable.  This supersedes error_rate, low_energy, etc.
        _user_active_recently = False
        _user_guard_window = 600  # 10 minutes
        if self._last_interaction_at is not None:
            _idle_secs = (now - self._last_interaction_at).total_seconds()
            _user_active_recently = _idle_secs < _user_guard_window

        if _user_active_recently:
            self._log_sleep_check(
                False, "user_active (all sleep deferred)", hypothalamus,
            )
            return False, "user_active"

        # ── Error rate (sympathetic) ── honoured only after min_turns
        # and only when the user is NOT actively engaged (guard above).
        triggers = self.config.sleep_triggers
        er_cfg = triggers.error_rate
        _min_turns = getattr(er_cfg, "min_turns", 25)
        if (
            self._turn_counter >= _min_turns
            and len(self._recent_errors) >= er_cfg.window_turns
        ):
            rate = self.error_rate
            if rate >= er_cfg.threshold:
                reason = f"error_rate ({rate:.0%} >= {er_cfg.threshold:.0%})"
                self._log_sleep_check(True, reason, hypothalamus)
                return True, reason

        # Must have SOME signals to justify sleeping
        if self.signal_count == 0:
            self._log_sleep_check(False, "no_signals", hypothalamus)
            return False, "no_signals"

        # ═══════════════════════════════════════════════════════════
        # CIRCADIAN PATH (schedule-based)
        # ═══════════════════════════════════════════════════════════
        if self.circadian.enabled:
            # 1. Bedtime check
            if self.circadian.is_bedtime(now):
                reason = (
                    f"bedtime ({self.circadian.config.bedtime}"
                    f"-{self.circadian.config.wake_time} "
                    f"{self.circadian.config.timezone})"
                )
                self._log_sleep_check(True, reason, hypothalamus)
                return True, reason

            # 2. Nap window check
            window = self.circadian.is_nap_window(now)
            if window:
                eff_threshold = self.effective_signal_threshold(hypothalamus)
                learnable = self.learnable_signal_count
                if learnable >= eff_threshold:
                    reason = (
                        f"nap_window ({window.start.strftime('%H:%M')}"
                        f"-{window.end.strftime('%H:%M')}, "
                        f"signals={learnable}>={eff_threshold:.0f})"
                    )
                    self._log_sleep_check(True, reason, hypothalamus)
                    return True, reason

            # 3. Signal pressure safety valve
            base_threshold = triggers.signal_count.base_threshold
            pressure_cap = self.circadian.signal_pressure_cap(base_threshold)
            learnable = self.learnable_signal_count
            if learnable >= pressure_cap:
                reason = (
                    f"signal_pressure ({learnable} >= "
                    f"{pressure_cap:.0f} cap)"
                )
                self._log_sleep_check(True, reason, hypothalamus)
                return True, reason

            # 4. Front-brain sleep triggers (IR-4)
            # Low energy: only trigger when truly depleted
            if self._current_energy < 0.08:
                reason = f"low_energy ({self._current_energy:.2f} < 0.08)"
                self._log_sleep_check(True, reason, hypothalamus)
                return True, reason

            # Sustained cognitive overload: WM at capacity for extended period
            if self._sustained_high_load_turns > 25:
                reason = (
                    f"cognitive_fatigue ({self._sustained_high_load_turns} "
                    f"turns at high load)"
                )
                self._log_sleep_check(True, reason, hypothalamus)
                return True, reason

            # Episode completion nap: high resonance peak faded
            if (self._last_resonance_peak > 0.7
                    and self.learnable_signal_count >= 3):
                reason = (
                    f"episode_reflection (peak_resonance="
                    f"{self._last_resonance_peak:.2f})"
                )
                self._last_resonance_peak = 0.0
                self._log_sleep_check(True, reason, hypothalamus)
                return True, reason

            self._log_sleep_check(False, "circadian_awake", hypothalamus)
            return False, "circadian_awake"

        # ═══════════════════════════════════════════════════════════
        # LEGACY PATH (reactive triggers, circadian disabled)
        # ═══════════════════════════════════════════════════════════
        should_sleep, reason = False, "no_trigger"

        # Signal count threshold
        eff_threshold = self.effective_signal_threshold(hypothalamus)
        learnable = self.learnable_signal_count
        if learnable >= eff_threshold:
            should_sleep, reason = True, (
                f"signal_count ({learnable} >= {eff_threshold:.0f})"
            )

        # Idle timeout
        if not should_sleep and self._last_interaction_at:
            idle_secs = (now - self._last_interaction_at).total_seconds()
            if idle_secs >= triggers.idle_timeout_seconds:
                should_sleep, reason = True, (
                    f"idle_timeout ({idle_secs:.0f}s "
                    f">= {triggers.idle_timeout_seconds:.0f}s)"
                )

        # Periodic timer
        if not should_sleep and self._last_sleep_at:
            since_sleep = (now - self._last_sleep_at).total_seconds()
            if since_sleep >= triggers.periodic_seconds:
                should_sleep, reason = True, (
                    f"periodic ({since_sleep:.0f}s "
                    f">= {triggers.periodic_seconds:.0f}s)"
                )

        self._log_sleep_check(should_sleep, reason, hypothalamus)
        return should_sleep, reason

    def _log_sleep_check(
        self, should_sleep: bool, reason: str, hypothalamus: Any
    ) -> None:
        """Log a sleep check decision."""
        if self._event_logger is None:
            return
        eff = self.effective_signal_threshold(hypothalamus)
        self._event_logger.log_sleep_check(
            should_sleep=should_sleep,
            reason=reason,
            effective_threshold=eff,
            signal_count=self.signal_count,
        )

    def determine_sleep_mode(
        self, hypothalamus: Any | None = None
    ) -> SleepMode:
        """Determine sleep mode.

        Always returns PARASYMPATHETIC.  The sympathetic/parasympathetic
        distinction was removed because sympathetic mode capped non-error
        signals during triage, causing massive signal loss on productive
        sessions (e.g. 87% loss on a 1001-signal buffer).  The priority
        ordering in triage already ensures errors are processed first;
        capping knowledge signals on top of that is harmful.

        The cortisol and error-rate checks are retained as advisory
        metadata logged in the sleep report but no longer gate the mode.
        """
        _advisory_stressed = False
        thresholds = self.config.sleep_mode_thresholds.sympathetic

        if hypothalamus is not None and "cortisol" in hypothalamus.hormones:
            cortisol = hypothalamus.hormones["cortisol"]
            defn = hypothalamus.config.hormones.get("cortisol")
            if defn:
                deviation = cortisol.level - defn.baseline
                if deviation > thresholds.cortisol_above_baseline:
                    _advisory_stressed = True

        if self._recent_errors:
            rate = self.error_rate
            if rate > thresholds.error_rate_above:
                _advisory_stressed = True

        if _advisory_stressed:
            logger.info(
                "ANS: elevated stress indicators detected but using "
                "PARASYMPATHETIC mode (sympathetic capping removed)."
            )

        return SleepMode.PARASYMPATHETIC

    # -------------------------------------------------------------------
    # Sleep Pipeline
    # -------------------------------------------------------------------

    def begin_sleep(
        self, hypothalamus: Any | None = None
    ) -> SleepMode:
        """Transition AWAKE -> DROWSY -> SLEEPING.

        Returns:
            The selected ``SleepMode``.

        Raises:
            RuntimeError: If the agent is not awake.
        """
        if not self.is_awake:
            raise RuntimeError(
                f"Cannot begin sleep from state: {self._state.value}"
            )

        mode = self.determine_sleep_mode(hypothalamus)

        # AWAKE -> DROWSY (pre-sleep transition)
        self._state = AgentState.DROWSY
        self._state_entered_at = datetime.utcnow()
        self._current_sleep_start = datetime.utcnow()
        self._current_sleep_mode = mode

        logger.info(
            "ANS: AWAKE -> DROWSY (mode: %s, signals: %d)",
            mode.value,
            self.signal_count,
        )

        # DROWSY -> SLEEPING
        self._state = AgentState.SLEEPING
        self._state_entered_at = datetime.utcnow()

        logger.info("ANS: DROWSY -> SLEEPING")

        # Persist SLEEPING state immediately so it survives crashes
        try:
            self.save_state()
        except Exception as _save_exc:
            logger.warning("ANS: failed to persist SLEEPING state: %s", _save_exc)

        # Research logging
        if self._event_logger is not None:
            self._event_logger.log_sleep_phase(
                "begin",
                mode=mode.value,
                signal_count=self.signal_count,
            )

        return mode

    def _score_signal(self, signal: NerveSignal) -> float:
        """Compute a priority score for a signal.

        Higher scores are processed first within each triage bucket.
        Combines hormonal salience, recency, source priority,
        feedback/edit boosts, and front-brain weights (PE, narrative,
        ToM interest matching).
        """
        import math

        now_ts = datetime.utcnow().timestamp()
        age_hours = max(0.01, (now_ts - signal.timestamp.timestamp()) / 3600)
        recency = math.exp(-0.1 * age_hours)

        salience = 0.5
        if signal.hormonal_snapshot:
            salience = sum(signal.hormonal_snapshot.values()) / max(
                len(signal.hormonal_snapshot), 1
            )

        source_weights = {
            "user": 1.0, "user_edit": 1.0,
            "ans_safety_net": 0.6, "tool": 0.5, "dmn": 0.3, "web": 0.4,
        }
        source_w = source_weights.get(signal.source, 0.5)

        feedback_boost = 1.0
        dp = signal.domain_path or ""
        if dp.startswith("Feedback.") or signal.source == "user_edit":
            feedback_boost = 2.0

        # Front-brain weights (IR-7.1)
        pe_weight = 0.0
        pe = getattr(signal, "pe_at_collection", 0.0) or 0.0
        if pe > 0.4:
            pe_weight = 0.3

        narrative_weight = 0.0
        episode_tag = getattr(signal, "episode_tag", "") or ""
        if episode_tag:
            narrative_weight = 0.2

        tom_weight = 0.0
        if dp and self._tom_interests:
            dp_lower = dp.lower()
            for topic in self._tom_interests:
                if topic.lower() in dp_lower:
                    tom_weight = 0.2
                    break

        base = recency * salience * source_w * feedback_boost
        return base * (1.0 + pe_weight + narrative_weight + tom_weight)

    def triage(self) -> TriagedSignals:
        """Phase 1 -- NREM Stage 1 (light sleep): sort and prioritize.

        Groups signals into three priority buckets:

        1. ``error_correction``: EVALUATE:incorrect, user_correction,
           EVALUATE:uncertain, ERASE, user_edit signals
        2. ``new_knowledge``: LEARN, UNKNOWN
        3. ``behavior_reinforcement``: EVALUATE:correct, user_positive,
           task_completed, LOOKUP

        Signals are scored by hormonal salience, recency, source priority,
        and feedback/edit boosts.  Within each bucket, higher-scored
        signals are processed first.

        Already-processed signals (with ``processed_at`` set) are skipped
        so multi-cycle nightly sleep doesn't re-triage the same signals.

        In **sympathetic** mode, non-error signals are capped to avoid
        spending precious consolidation time on low-priority items when
        the agent is in crisis.

        Returns:
            ``TriagedSignals`` with signals organized by priority.

        Raises:
            RuntimeError: If the agent is not sleeping.
        """
        if self._state != AgentState.SLEEPING:
            raise RuntimeError(
                f"Cannot triage in state: {self._state.value}"
            )

        triaged = TriagedSignals()

        base_max = self.config.sleep_phases.triage.max_signals_per_cycle
        buf_size = len(self._signal_buffer)
        if buf_size > 500:
            max_signals = min(buf_size, base_max * 4)
        elif buf_size > 200:
            max_signals = min(buf_size, base_max * 2)
        else:
            max_signals = base_max
        processed = 0
        _dropped_count = 0

        # Decrement suppressed-domain expiry counters at cycle start
        expired = [d for d, n in self._suppressed_domains.items() if n <= 0]
        for d in expired:
            del self._suppressed_domains[d]

        _ERROR_TRIAGE = frozenset({
            "EVALUATE:incorrect", "user_correction", "EVALUATE:uncertain",
            "task_failed", "EVALUATE:PFC.ToolError", "EVALUATE:frustrated",
            "EVALUATE:conflicted", "COHERENCE:low", "EVALUATE:overwhelmed",
            "RECALL_MISS",
        })
        _KNOWLEDGE_TRIAGE = frozenset({
            "LEARN", "UNKNOWN", "REFLECT", "CONNECT", "DOUBT",
            "PLAN_CREATE", "PLAN_STEP", "DELEGATE_START",
            "CHANNEL_CONTEXT",
        })
        _REINFORCEMENT_TRIAGE = frozenset({
            "EVALUATE:correct", "user_positive", "task_completed",
            "LOOKUP", "EVALUATE:PFC.ToolSuccess",
            "EVALUATE:curious", "EVALUATE:insightful",
            "EVALUATE:aligned", "EVALUATE:understanding",
            "EVALUATE:crystallizing", "EVALUATE:connecting",
            "COHERENCE:high", "COHERENCE:self_correction",
            "RECALL_HIT", "SKILL_INVOKE",
            "SKILL_CRYSTALLIZATION_READY",
        })

        for signal in reversed(self._signal_buffer):
            if processed >= max_signals:
                break

            if getattr(signal, "processed_at", None) is not None:
                continue

            stype = signal.signal_type

            # ERASE and user_edit always go to error_correction (highest priority)
            if stype == "ERASE" or signal.source == "user_edit":
                triaged.error_correction.append(signal)
            elif stype in _ERROR_TRIAGE:
                triaged.error_correction.append(signal)
            elif stype in _KNOWLEDGE_TRIAGE:
                triaged.new_knowledge.append(signal)
            elif stype in _REINFORCEMENT_TRIAGE:
                triaged.behavior_reinforcement.append(signal)
            elif stype.startswith("EVALUATE:"):
                # Catch-all for any EVALUATE:* not explicitly listed —
                # route to reinforcement so nothing is silently dropped.
                triaged.behavior_reinforcement.append(signal)
            else:
                # Truly unknown signal type — still triage it rather
                # than waste capacity.  Route to reinforcement as low
                # priority (scoring will push it to the bottom).
                triaged.behavior_reinforcement.append(signal)
                _dropped_count += 1

            processed += 1

        # Sort each bucket by signal score (highest first)
        triaged.error_correction.sort(
            key=lambda s: self._score_signal(s), reverse=True,
        )
        triaged.new_knowledge.sort(
            key=lambda s: self._score_signal(s), reverse=True,
        )
        triaged.behavior_reinforcement.sort(
            key=lambda s: self._score_signal(s), reverse=True,
        )

        # Mark triaged signals as processed for multi-cycle sleep
        now = datetime.utcnow()
        for bucket_name in ("error_correction", "new_knowledge", "behavior_reinforcement"):
            for sig in getattr(triaged, bucket_name, []):
                sig.processed_at = now  # type: ignore[attr-defined]

        # Decrement suppressed domain counters (one cycle consumed)
        for d in list(self._suppressed_domains):
            self._suppressed_domains[d] -= 1

        self._current_triaged = triaged

        _categorized = (
            len(triaged.error_correction)
            + len(triaged.new_knowledge)
            + len(triaged.behavior_reinforcement)
        )
        logger.info(
            "Triage complete: errors=%d, knowledge=%d, reinforcement=%d, "
            "dropped=%d (mode: %s, buffer=%d, cap=%d)",
            len(triaged.error_correction),
            len(triaged.new_knowledge),
            len(triaged.behavior_reinforcement),
            _dropped_count,
            self._current_sleep_mode.value
            if self._current_sleep_mode
            else "unknown",
            buf_size,
            max_signals,
        )

        return triaged

    def consolidate(
        self,
        triaged: TriagedSignals | None = None,
        mining_callback: Any | None = None,
    ) -> dict[str, Any]:
        """Phase 2 -- NREM Stage 3 (SWS / deep sleep): consolidate.

        Collects signals in priority order, converts them to training
        data, and runs micro-training.

        In the full pipeline, this calls:

        1. ``distiller.aku_to_training_entries()`` for each signal
        2. ``trainer.mine_block()`` for micro-training
        3. ``merkle.add_block()`` to add the delta to the chain

        The ``mining_callback`` parameter allows injecting the real
        pipeline (or a test mock) without hard-coding dependencies.

        Args:
            triaged: Pre-triaged signals. Uses last triage if ``None``.
            mining_callback: Optional ``callable(signals, config) -> dict``
                that performs the actual mining. If ``None``, returns a
                dry-run summary.

        Returns:
            Summary dict with consolidation results.

        Raises:
            RuntimeError: If not sleeping or triage not run.
        """
        if self._state != AgentState.SLEEPING:
            raise RuntimeError(
                f"Cannot consolidate in state: {self._state.value}"
            )

        triaged = triaged or self._current_triaged
        if triaged is None:
            raise RuntimeError("Must run triage() before consolidate()")

        # Collect signals in priority order
        priority_order = (
            self.config.sleep_phases.triage.priority_order
        )
        all_signals: list[NerveSignal] = []
        for priority in priority_order:
            bucket = getattr(triaged, priority, [])
            all_signals.extend(bucket)

        max_aku = self.config.sleep_phases.consolidation.max_aku_per_cycle
        to_process = all_signals[:max_aku]

        # Build summary
        by_type: dict[str, int] = {}
        for sig in to_process:
            by_type[sig.signal_type] = by_type.get(sig.signal_type, 0) + 1

        summary: dict[str, Any] = {
            "signals_processed": len(to_process),
            "by_type": by_type,
            "akus_generated": 0,
            "blocks_added": 0,
            "training_complete": False,
        }

        # --- PRODUCTION HOOK ---
        # If a mining_callback is provided, delegate the real work.
        # The callback receives the signals and training config and
        # returns an enriched summary dict.
        # Wrapped in try/except: if the callback fails (e.g. GPU OOM
        # during consolidation), we log the error but let the sleep cycle
        # continue to the integration phase (which reloads the model).
        if mining_callback is not None:
            try:
                cb_result = mining_callback(
                    to_process,
                    self.config.sleep_phases.consolidation,
                )
                if isinstance(cb_result, dict):
                    summary.update(cb_result)
            except Exception as cb_exc:
                logger.warning(
                    "Mining callback failed: %s (sleep will continue "
                    "without training)", cb_exc,
                )
                summary["training_error"] = str(cb_exc)
        else:
            # Dry-run: report what WOULD be consolidated
            summary["akus_generated"] = len(to_process)
            summary["training_complete"] = True

        logger.info(
            "Consolidation: processed %d signals, AKUs=%d, trained=%s",
            summary["signals_processed"],
            summary["akus_generated"],
            summary["training_complete"],
        )

        return summary

    def integrate(
        self,
        integration_callback: Any | None = None,
    ) -> dict[str, Any]:
        """Phase 3 -- REM sleep: merge, recalibrate, prune.

        In the full pipeline, this calls:

        1. ``merger.ties_merge()`` if enough deltas have accumulated
        2. Thalamus recalibration (Phase 3 of the roadmap)
        3. Regression check against known-good prompts

        The ``integration_callback`` parameter allows injecting the
        real pipeline (or a test mock).

        Args:
            integration_callback: Optional ``callable(config) -> dict``
                that performs the real integration. If ``None``, returns
                a dry-run summary.

        Returns:
            Summary dict with integration results.

        Raises:
            RuntimeError: If not sleeping.
        """
        if self._state != AgentState.SLEEPING:
            raise RuntimeError(
                f"Cannot integrate in state: {self._state.value}"
            )

        cfg = self.config.sleep_phases.integration

        summary: dict[str, Any] = {
            "merge_performed": False,
            "thalamus_recalibrated": False,
            "regression_detected": False,
        }

        if integration_callback is not None:
            cb_result = integration_callback(cfg)
            if isinstance(cb_result, dict):
                summary.update(cb_result)
        else:
            # Dry-run
            if cfg.recalibrate_thalamus:
                summary["thalamus_recalibrated"] = True

        logger.info(
            "Integration: merge=%s, recal=%s, regression=%s",
            summary["merge_performed"],
            summary["thalamus_recalibrated"],
            summary["regression_detected"],
        )

        return summary

    def wake(
        self, hypothalamus: Any | None = None
    ) -> SleepReport:
        """Complete the sleep cycle: SLEEPING -> WAKING -> AWAKE.

        Post-sleep morning routine:

        1. Reset hormones (sleep reduces stress, boosts stability)
        2. Clear signal buffer
        3. Generate sleep report
        4. Transition back to AWAKE

        Args:
            hypothalamus: Optional ``HypothalamusEngine`` for hormone
                reset.

        Returns:
            ``SleepReport`` summarizing the completed sleep cycle.

        Raises:
            RuntimeError: If the agent is not sleeping.
        """
        if self._state != AgentState.SLEEPING:
            raise RuntimeError(
                f"Cannot wake from state: {self._state.value}"
            )

        # SLEEPING -> WAKING
        self._state = AgentState.WAKING
        self._state_entered_at = datetime.utcnow()

        now = datetime.utcnow()
        duration = 0.0
        if self._current_sleep_start:
            duration = (now - self._current_sleep_start).total_seconds()

        # Build report
        triaged = self._current_triaged or TriagedSignals()
        report = SleepReport(
            sleep_mode=(
                self._current_sleep_mode.value
                if self._current_sleep_mode
                else "unknown"
            ),
            started_at=self._current_sleep_start or now,
            completed_at=now,
            duration_seconds=duration,
            total_signals_processed=self.signal_count,
            signals_by_type=self._count_signals_by_type(),
            signals_by_priority={
                "error_correction": len(triaged.error_correction),
                "new_knowledge": len(triaged.new_knowledge),
                "behavior_reinforcement": len(
                    triaged.behavior_reinforcement
                ),
            },
        )

        # Reset hormones (the "morning refresh")
        hormone_resets: dict[str, float] = {}
        if hypothalamus is not None:
            reset_cfg = self.config.post_sleep.hormone_reset
            for hormone_name, target in reset_cfg.items():
                if hormone_name == "description":
                    continue
                if hormone_name not in hypothalamus.hormones:
                    continue
                h = hypothalamus.hormones[hormone_name]
                if target == "baseline":
                    defn = hypothalamus.config.hormones.get(hormone_name)
                    if defn:
                        h.level = defn.baseline
                elif isinstance(target, (int, float)):
                    h.level = float(target)
                hormone_resets[hormone_name] = h.level

        report.hormones_reset = hormone_resets

        # Front-brain metrics (IR-7.5)
        report.prediction_accuracy_before_sleep = self._current_pe
        report.energy_before = self._current_energy
        report.resonance_peak = self._last_resonance_peak
        self._last_resonance_peak = 0.0  # reset for next session

        # Selectively clear only processed signals; preserve unprocessed
        # ones for the next sleep cycle so no data is lost.
        if self.config.post_sleep.clear_signal_buffer:
            before_len = len(self._signal_buffer)
            self._signal_buffer = [
                s for s in self._signal_buffer
                if getattr(s, "processed_at", None) is None
            ]
            _cleared = before_len - len(self._signal_buffer)
            _remaining = len(self._signal_buffer)
        else:
            _cleared = 0
            _remaining = len(self._signal_buffer)

        # Store report
        self._sleep_reports.append(report)

        # Update tracking
        self._last_sleep_at = now
        self._current_sleep_start = None
        self._current_sleep_mode = None
        self._current_triaged = None
        self._recent_errors.clear()
        self._sustained_high_load_turns = 0

        # WAKING -> AWAKE
        self._state = AgentState.AWAKE
        self._state_entered_at = datetime.utcnow()

        logger.info(
            "ANS: Woke up after %.1fs (%s mode). "
            "Cleared %d processed signals, %d unprocessed preserved.",
            duration,
            report.sleep_mode,
            _cleared,
            _remaining,
        )

        # Research logging
        if self._event_logger is not None:
            self._event_logger.log_sleep_phase(
                "wake",
                mode=report.sleep_mode,
                duration_seconds=round(duration, 2),
                total_signals=report.total_signals_processed,
                signals_by_priority=report.signals_by_priority,
                hormones_reset=hormone_resets,
            )

        return report

    def full_sleep_cycle(
        self,
        hypothalamus: Any | None = None,
        mining_callback: Any | None = None,
        integration_callback: Any | None = None,
    ) -> SleepReport:
        """Execute a complete sleep cycle in one call.

        Convenience method: begin -> triage -> consolidate -> integrate
        -> wake.

        Args:
            hypothalamus: For hormone interaction.
            mining_callback: For consolidation phase.
            integration_callback: For integration phase.

        Returns:
            ``SleepReport`` from the completed cycle.
        """
        self.begin_sleep(hypothalamus)
        triaged = self.triage()
        cons = self.consolidate(triaged, mining_callback)
        intg = self.integrate(integration_callback)

        # Phase 3.5: Crystallization evaluation (after REM, before wake)
        crystal_summary = self._evaluate_crystallization()
        if crystal_summary:
            intg["crystallization"] = crystal_summary

        report = self.wake(hypothalamus)
        report.consolidation_summary = cons
        report.integration_summary = intg
        return report

    def _evaluate_crystallization(self) -> dict[str, Any] | None:
        """Evaluate AgentSkill crystallization candidates during sleep.

        Returns a summary dict or None if crystallization is disabled
        or no calibrator is available.
        """
        try:
            from .crystallization import (
                CrystallizationConfig,
                evaluate_candidates,
                save_candidates,
            )
            from pathlib import Path

            config_path = Path("nls/config/crystallization.json")
            config = CrystallizationConfig.load(config_path)
            if not config.enabled:
                return None

            calibrator = getattr(self, "_calibrator", None)
            if calibrator is None:
                return None

            dt = getattr(calibrator, "domain_tracker", None)
            if dt is None or not hasattr(dt, "skill_encounters"):
                return None

            skill_tracker = {
                k: v.model_dump() if hasattr(v, "model_dump") else v
                for k, v in dt.skill_encounters.items()
            }
            if not skill_tracker:
                return None

            task_summaries = self._recent_tasks[-config.task_memory_lookback:]

            # Gather ToM context for crystallization biasing (IR-9)
            user_interests: dict[str, float] | None = None
            user_expertise: dict[str, float] | None = None
            goal_skills: list[str] | None = None

            tom = getattr(self, "_tom_ref", None)
            if tom is not None:
                try:
                    um = tom.get_user()
                    if um is not None:
                        interests_list = um.top_interests(10) if hasattr(um, "top_interests") else []
                        user_interests = {t: 1.0 for t in interests_list} if interests_list else None
                        if hasattr(um, "expertise"):
                            user_expertise = um.expertise if um.expertise else None
                except Exception:
                    pass

            wm = getattr(self, "_wm_ref", None)
            if wm is not None:
                try:
                    goals = getattr(wm, "_goals", [])
                    goal_skills = [
                        g.content for g in goals
                        if hasattr(g, "content") and "crystallize" in g.content.lower()
                    ] or None
                except Exception:
                    pass

            candidates = evaluate_candidates(
                skill_tracker=skill_tracker,
                task_summaries=task_summaries,
                config=config,
                user_interests=user_interests,
                user_expertise=user_expertise,
                goal_crystallize_skills=goal_skills,
            )

            ready = [c for c in candidates if c.ready]

            if candidates:
                data_dir = os.environ.get("NLS_DATA_DIR", "data")
                save_path = Path(data_dir) / "crystallization_candidates.json"
                save_candidates(candidates, save_path)

            if ready:
                for c in ready:
                    logger.info(
                        "Crystallization ready: skill=%s score=%.2f uses=%d",
                        c.skill_name, c.readiness_score, c.total_uses,
                    )

            return {
                "evaluated": len(candidates),
                "ready": len(ready),
                "ready_skills": [c.skill_name for c in ready],
            }
        except Exception as exc:
            logger.warning("Crystallization evaluation failed: %s", exc)
            return None

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    def contribute_to_state(self, self_state: Any) -> None:
        """Write ANS readings into the unified SelfState.

        Part of the SelfState collection protocol -- each brain component
        contributes its readings to the unified self-representation.
        """
        self_state.signal_buffer_depth = self.learnable_signal_count

    def save_state(self, path: Path | None = None) -> None:
        """Save ANS state to disk (signal buffer, counters, reports).

        The signal buffer persists across crashes so no signals are lost.
        ``path`` is optional; if omitted the last path used is reused.
        """
        if path is not None:
            self._last_persist_path: Path | None = path
        effective_path = getattr(self, "_last_persist_path", None)
        if effective_path is None:
            return
        path = effective_path
        state = {
            "version": "1.2",
            "state": self._state.value,
            "turn_counter": self._turn_counter,
            "signal_buffer": [
                s.model_dump(mode="json") for s in self._signal_buffer
            ],
            "recent_errors": self._recent_errors,
            "recent_tasks": self._recent_tasks,
            "success_streak": self._success_streak,
            "failure_streak": self._failure_streak,
            "last_sleep_at": (
                self._last_sleep_at.isoformat()
                if self._last_sleep_at
                else None
            ),
            "last_interaction_at": (
                self._last_interaction_at.isoformat()
                if self._last_interaction_at
                else None
            ),
            "sleep_report_count": len(self._sleep_reports),
            "suppressed_domains": self._suppressed_domains,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)
        logger.info("ANS state saved to %s", path)

    def load_state(self, path: Path) -> None:
        """Load ANS state from disk."""
        if not path.exists():
            logger.warning("No ANS state file at %s", path)
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Corrupted ANS state file at %s: %s — starting fresh", path, exc)
            backup = path.with_suffix(".json.bak")
            try:
                path.rename(backup)
                logger.info("Backed up corrupted state to %s", backup)
            except OSError:
                pass
            return

        self._state = AgentState(state.get("state", "awake"))
        self._turn_counter = state.get("turn_counter", 0)
        self._signal_buffer = [
            NerveSignal(**s) for s in state.get("signal_buffer", [])
        ]
        self._recent_errors = state.get("recent_errors", [])
        self._recent_tasks = state.get("recent_tasks", [])
        self._success_streak = state.get("success_streak", 0)
        self._failure_streak = state.get("failure_streak", 0)

        if state.get("last_sleep_at"):
            self._last_sleep_at = datetime.fromisoformat(
                state["last_sleep_at"]
            )
        if state.get("last_interaction_at"):
            self._last_interaction_at = datetime.fromisoformat(
                state["last_interaction_at"]
            )

        self._suppressed_domains = state.get("suppressed_domains", {})

        logger.info(
            "ANS state loaded: state=%s, turns=%d, signals=%d, suppressed=%d",
            self._state.value,
            self._turn_counter,
            self.signal_count,
            len(self._suppressed_domains),
        )

    def reset(self) -> None:
        """Reset the ANS to initial state (fresh boot)."""
        self._state = AgentState.AWAKE
        self._state_entered_at = datetime.utcnow()
        self._signal_buffer.clear()
        self._turn_counter = 0
        self._last_sleep_at = None
        self._last_interaction_at = None
        self._sleep_reports.clear()
        self._current_sleep_start = None
        self._current_sleep_mode = None
        self._current_triaged = None
        self._recent_errors.clear()
        self._voluntary_sleep_requested = False
        self._voluntary_sleep_reason = ""
        self._recent_tasks.clear()

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _count_signals_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self._signal_buffer:
            counts[s.signal_type] = counts.get(s.signal_type, 0) + 1
        return counts

    def get_buffer_summary(self) -> dict[str, Any]:
        """Get a snapshot summary of the current signal buffer."""
        return {
            "state": self._state.value,
            "turn_count": self._turn_counter,
            "total_signals": self.signal_count,
            "learnable_signals": self.learnable_signal_count,
            "by_type": self._count_signals_by_type(),
            "error_rate": f"{self.error_rate:.0%}",
            "oldest": (
                self._signal_buffer[0].timestamp.isoformat()
                if self._signal_buffer
                else None
            ),
            "newest": (
                self._signal_buffer[-1].timestamp.isoformat()
                if self._signal_buffer
                else None
            ),
            "last_sleep": (
                self._last_sleep_at.isoformat()
                if self._last_sleep_at
                else "never"
            ),
            "sleep_cycles_completed": len(self._sleep_reports),
        }
