"""NLS Drive Engine -- The Will to Act.

Maps to the human motivation circuit:
  - VTA / Nucleus Accumbens: converts hormone deprivation into drive pressure
  - Prefrontal Cortex (PFC): prioritizes competing drives into a single goal
  - Anterior Cingulate Cortex (ACC): two-layer effort gate
      Layer 1 -- Knowledge Calibration (rational): DomainTracker + myelination + experience
      Layer 2 -- Hormonal Bias (perception distortion): inverse to confidence

Five drives in Maslow hierarchy:
  1. Homeostasis  (cortisol)       -- self-check / knowledge integrity
  2. Curiosity    (norepinephrine) -- explore / web search
  3. Competence   (dopamine)       -- self-test / verify knowledge
  4. Social       (oxytocin)       -- reach out to user
  5. Self-direction (serotonin)    -- reflect / set goals
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class DrivePressure:
    """Output of the VTA: how much a drive is 'wanting'."""

    drive_name: str
    hormone: str
    maslow_level: int
    deprivation: float        # how far below baseline (or above for cortisol)
    pressure: float           # deprivation * sensitivity
    action_type: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "drive": self.drive_name,
            "hormone": self.hormone,
            "maslow_level": self.maslow_level,
            "deprivation": round(self.deprivation, 4),
            "pressure": round(self.pressure, 4),
            "action_type": self.action_type,
        }


@dataclass
class DriveGoal:
    """Output of the PFC: a single prioritized goal."""

    drive_name: str
    action_type: str
    pressure: float
    domain: str = ""
    query: str = ""
    message: str = ""
    will_to_act: float = 0.0
    base_effort: float = 0.0
    confidence: float = 0.0
    perceived_effort: float = 0.0
    skill_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "drive": self.drive_name,
            "action_type": self.action_type,
            "pressure": round(self.pressure, 4),
            "domain": self.domain,
            "query": self.query,
            "message": self.message,
            "will_to_act": round(self.will_to_act, 4),
            "base_effort": round(self.base_effort, 4),
            "confidence": round(self.confidence, 4),
            "perceived_effort": round(self.perceived_effort, 4),
        }
        if self.skill_name:
            d["skill_name"] = self.skill_name
        return d


# ---------------------------------------------------------------------------
# Experience Tracker -- per-domain outcome memory
# ---------------------------------------------------------------------------


@dataclass
class DomainExperience:
    """Record of past attempts in a specific domain."""

    attempts: int = 0
    successes: int = 0
    failures: int = 0
    last_effort: float = 0.5
    last_outcome: str = ""
    last_search_time: float = 0.0  # timestamp of last search for this domain


class ExperienceTracker:
    """Tracks per-domain success/failure history.

    Calibrates future effort estimates. Persisted to disk so experience
    survives restarts. Novel domains start at initial_effort (0.5 =
    "how hard can it be?" -- the entrepreneur's starting point).

    Also tracks per-domain search cooldowns to prevent re-searching
    the same domain repeatedly (the runaway curiosity fix).
    """

    def __init__(
        self,
        initial_effort: float = 0.5,
        success_adjustment: float = -0.05,
        failure_adjustment: float = 0.10,
        min_effort: float = 0.1,
        max_effort: float = 0.95,
        domain_search_cooldown: float = 600.0,
    ):
        self.initial_effort = initial_effort
        self.success_adj = success_adjustment
        self.failure_adj = failure_adjustment
        self.min_effort = min_effort
        self.max_effort = max_effort
        self.domain_search_cooldown = domain_search_cooldown
        self._domains: dict[str, DomainExperience] = {}

    def get(self, domain: str) -> DomainExperience:
        """Get experience for a domain. Returns fresh entry if novel."""
        if domain not in self._domains:
            self._domains[domain] = DomainExperience(
                last_effort=self.initial_effort
            )
        return self._domains[domain]

    def is_on_cooldown(self, domain: str) -> bool:
        """Check if a domain was searched recently (within cooldown period).

        Prevents the runaway curiosity loop where the same domain gets
        searched 6+ times in a row because each 'success' raises confidence.
        """
        exp = self._domains.get(domain)
        if exp is None:
            return False
        if exp.last_search_time == 0.0:
            return False
        elapsed = time.time() - exp.last_search_time
        return elapsed < self.domain_search_cooldown

    def mark_searched(self, domain: str) -> None:
        """Mark a domain as just-searched. Starts the cooldown timer."""
        exp = self.get(domain)
        exp.last_search_time = time.time()

    def record_outcome(self, domain: str, success: bool) -> None:
        """Record the outcome of an autonomous action."""
        exp = self.get(domain)
        exp.attempts += 1
        if success:
            exp.successes += 1
            exp.last_outcome = "success"
            exp.last_effort = max(
                self.min_effort,
                exp.last_effort + self.success_adj,
            )
        else:
            exp.failures += 1
            exp.last_outcome = "failure"
            exp.last_effort = min(
                self.max_effort,
                exp.last_effort + self.failure_adj,
            )
        logger.info(
            "ExperienceTracker: domain=%s outcome=%s effort=%.3f attempts=%d",
            domain, exp.last_outcome, exp.last_effort, exp.attempts,
        )

    def save(self, path: Path) -> None:
        """Persist experience to disk."""
        data = {}
        for domain, exp in self._domains.items():
            data[domain] = {
                "attempts": exp.attempts,
                "successes": exp.successes,
                "failures": exp.failures,
                "last_effort": exp.last_effort,
                "last_outcome": exp.last_outcome,
                "last_search_time": exp.last_search_time,
            }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, path: Path) -> None:
        """Load persisted experience from disk."""
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Support both flat format {domain: {...}} and wrapped format
        # {description: ..., experiences: {...}, ...} from genesis templates.
        if "experiences" in data and isinstance(data["experiences"], dict):
            data = data["experiences"]
        for domain, d in data.items():
            if not isinstance(d, dict):
                continue  # skip metadata keys like 'description'
            self._domains[domain] = DomainExperience(
                attempts=d.get("attempts", 0),
                successes=d.get("successes", 0),
                failures=d.get("failures", 0),
                last_effort=d.get("last_effort", self.initial_effort),
                last_outcome=d.get("last_outcome", ""),
                last_search_time=d.get("last_search_time", 0.0),
            )

    @property
    def all_domains(self) -> list[str]:
        return list(self._domains.keys())


# ---------------------------------------------------------------------------
# Drive Engine
# ---------------------------------------------------------------------------


class DriveEngine:
    """The motivation circuit: VTA -> PFC -> ACC.

    Reads hormonal state, computes deprivation pressures, selects the
    highest-priority goal, and decides whether the agent has the will
    to act through a two-layer effort gate (knowledge + hormonal bias).

    Operates in three stages each tick:
        1. VTA  -- compute deprivation pressures for all five drives
        2. PFC  -- select the single highest-priority goal (Maslow order)
        3. ACC  -- two-layer effort gate: knowledge calibration + hormonal bias
    """

    def __init__(self, config: dict[str, Any], agent_dir: Path | None = None):
        self._cfg = config
        self._agent_dir = agent_dir

        # Global settings
        self._tick_seconds = config.get("drive_tick_seconds", 30)
        self._action_threshold = config.get("action_threshold", 0.15)
        self._max_actions_per_hour = config.get(
            "max_autonomous_actions_per_hour", 20
        )

        # Parse drive configs
        self._drives = config.get("drives", {})

        # Experience tracker
        exp_cfg = config.get("experience_tracker", {})
        self.experience = ExperienceTracker(
            initial_effort=exp_cfg.get("initial_effort", 0.5),
            success_adjustment=exp_cfg.get("success_adjustment", -0.05),
            failure_adjustment=exp_cfg.get("failure_adjustment", 0.10),
            min_effort=exp_cfg.get("min_effort", 0.1),
            max_effort=exp_cfg.get("max_effort", 0.95),
            domain_search_cooldown=exp_cfg.get(
                "domain_search_cooldown_seconds", 600.0
            ),
        )

        # Knowledge confidence scaling
        self._kc = config.get("knowledge_confidence", {})
        self._encounter_weight = self._kc.get("encounter_weight", 0.05)
        self._myelination_weight = self._kc.get("myelination_weight", 0.3)
        self._experience_weight = self._kc.get("experience_weight", 0.1)
        self._max_confidence = self._kc.get("max_confidence", 1.0)

        # Cooldown tracking: drive_name -> last_fire_time
        self._cooldowns: dict[str, float] = {}

        # Action counter for safety cap
        self._actions_this_hour: list[float] = []

        # Frustration tracking -- ACC detects blocked goals.
        # In the human brain, when the ACC sees repeated goal conflicts
        # (wanting to act but unable), it produces a frustration signal:
        # mild cortisol increase, dopamine decrease.  The longer the
        # blockage persists, the stronger the signal -- until the
        # organism disengages (DMN takes over) or falls asleep.
        self._consecutive_blocked: int = 0

        # DomainDB reference (set by runtime for disconfirmation drive)
        self.domain_db = None

        # Event logger (set by runtime)
        self._event_logger = None

        # Front-brain enrichment context (set per-tick by runtime, Phase 9)
        self._enrichment_context: str | None = None

        # Cross-drive boost from front-brain context (Phase 9)
        gen_cfg = config.get("drive_generation", {})
        self._cross_drive_boost = gen_cfg.get("cross_drive_boost", 0.15)

        # Load persisted experience
        if agent_dir is not None:
            exp_path = agent_dir / exp_cfg.get(
                "persistence_file", "experience_tracker.json"
            )
            self.experience.load(exp_path)

    # =======================================================================
    # Stage 1: VTA -- Deprivation to Pressure
    # =======================================================================

    def compute_pressures(self, hypothalamus: Any) -> list[DrivePressure]:
        """VTA analog: convert hormonal deprivation into drive pressures.

        For most drives, pressure = (baseline - level) * sensitivity
        when the hormone is below baseline (deprivation).

        Cortisol is special: homeostasis fires when cortisol is ABOVE
        baseline (stress), not below.
        """
        pressures: list[DrivePressure] = []
        now = time.time()

        for drive_name, dcfg in self._drives.items():
            hormone_name = dcfg["hormone"]
            threshold = dcfg.get("deprivation_threshold", 0.05)
            sensitivity = dcfg.get("pressure_sensitivity", 1.0)
            cooldown = dcfg.get("cooldown_seconds", 60)

            # Check cooldown
            last_fire = self._cooldowns.get(drive_name, 0.0)
            if (now - last_fire) < cooldown:
                continue

            # Get hormone level and baseline
            h_state = hypothalamus.hormones.get(hormone_name)
            if h_state is None:
                continue
            level = h_state.level
            baseline = h_state.definition.baseline

            # Compute deprivation
            mode = dcfg.get("deprivation_mode", "below_baseline")
            if mode == "above_baseline":
                # Cortisol: stress is above baseline
                deprivation = max(0.0, level - baseline)
            else:
                # Standard: deprivation is below baseline
                deprivation = max(0.0, baseline - level)

            if deprivation < threshold:
                continue

            pressure = deprivation * sensitivity

            pressures.append(DrivePressure(
                drive_name=drive_name,
                hormone=hormone_name,
                maslow_level=dcfg.get("maslow_level", 5),
                deprivation=deprivation,
                pressure=pressure,
                action_type=dcfg.get("action_type", "reflect"),
            ))

        return pressures

    # =======================================================================
    # Stage 2: PFC -- Goal Prioritizer
    # =======================================================================

    def select_goal(
        self,
        pressures: list[DrivePressure],
        calibrator: Any | None = None,
        ans: Any | None = None,
    ) -> DriveGoal | None:
        """PFC analog: select the single highest-priority goal.

        Priority rules (Maslow):
            - If homeostasis pressure is critical (> 2x threshold), it always wins
            - Otherwise, highest pressure wins
            - Only one goal at a time (inhibit competing drives)
        """
        if not pressures:
            return None

        # Safety cap check
        now = time.time()
        hour_ago = now - 3600
        self._actions_this_hour = [
            t for t in self._actions_this_hour if t > hour_ago
        ]
        if len(self._actions_this_hour) >= self._max_actions_per_hour:
            logger.warning(
                "DriveEngine: safety cap reached (%d actions/hour)",
                self._max_actions_per_hour,
            )
            return None

        # Check for critical homeostasis
        homeostasis = [
            p for p in pressures if p.drive_name == "homeostasis"
        ]
        if homeostasis:
            h_cfg = self._drives.get("homeostasis", {})
            emergency_thresh = h_cfg.get("deprivation_threshold", 0.10) * 2
            if homeostasis[0].deprivation > emergency_thresh:
                winner = homeostasis[0]
                goal = self._build_goal(winner, calibrator, ans)
                if goal is not None:
                    return goal

        # Sort by pressure (highest first), with Maslow level as tiebreaker
        pressures_sorted = sorted(
            pressures,
            key=lambda p: (-p.pressure, p.maslow_level),
        )

        # Try each pressure in priority order -- if the top candidate's
        # domains are all on cooldown, fall through to the next drive
        for candidate in pressures_sorted:
            goal = self._build_goal(candidate, calibrator, ans)
            if goal is not None:
                return goal

        # All drives have exhausted their domains (all on cooldown).
        # Demoted to DEBUG: in the continuous inner loop this fires
        # every breath when blocked -- INFO would drown the log.
        logger.debug("DriveEngine: all candidate domains on cooldown, no goal")
        return None

    def _build_goal(
        self,
        pressure: DrivePressure,
        calibrator: Any | None = None,
        ans: Any | None = None,
    ) -> DriveGoal | None:
        """Convert a DrivePressure into a concrete DriveGoal with domain and query.

        Returns None if no valid domain is available (e.g., all domains
        are on search cooldown). This prevents generating goals with
        empty/nonsensical queries.
        """
        dcfg = self._drives.get(pressure.drive_name, {})
        domain = self._pick_domain(pressure, dcfg, calibrator, ans)

        # If domain is empty, all candidates are on cooldown -- skip.
        # Demoted to DEBUG: fires every breath in continuous loop.
        if not domain:
            logger.debug(
                "DriveEngine: all domains on cooldown for %s, skipping goal",
                pressure.drive_name,
            )
            return None

        query = self._pick_query(
            pressure, dcfg, domain,
            enrichment_context=self._enrichment_context,
        )
        message = self._pick_message(
            dcfg, domain,
            enrichment_context=self._enrichment_context,
        )

        return DriveGoal(
            drive_name=pressure.drive_name,
            action_type=pressure.action_type,
            pressure=pressure.pressure,
            domain=domain,
            query=query,
            message=message,
        )

    def _pick_domain(
        self,
        pressure: DrivePressure,
        dcfg: dict,
        calibrator: Any | None,
        ans: Any | None,
    ) -> str:
        """Select a target domain for this drive goal.

        Respects per-domain search cooldowns: if a domain was searched
        recently, it will be skipped in favor of another candidate.
        This prevents the runaway curiosity loop.
        """
        sources = dcfg.get("topic_sources", [])
        domain_tracker = (
            calibrator.domain_tracker if calibrator is not None else None
        )

        # Try each source in order
        for source in sources:
            if source == "pending_unknowns" and ans is not None:
                # Domains the agent previously marked UNKNOWN
                summary = ans.get_buffer_summary()
                by_type = summary.get("by_type", {})
                if by_type.get("UNKNOWN", 0) > 0:
                    for sig in reversed(ans.signal_buffer):
                        if (
                            getattr(sig, "signal_type", "") == "UNKNOWN"
                            and getattr(sig, "domain_path", "")
                        ):
                            domain = sig.domain_path
                            if not self.experience.is_on_cooldown(domain):
                                return domain
                    # All unknowns are on cooldown
                    continue

            elif source == "weak_routing_domains" and domain_tracker is not None:
                # Domains with low encounter count (pruning candidates)
                weak = [
                    (path, entry)
                    for path, entry in domain_tracker.domains.items()
                    if entry.encounter_count < 3
                    and not self.experience.is_on_cooldown(path)
                ]
                if weak:
                    chosen = random.choice(weak)
                    return chosen[0]

            elif source == "strong_routing_domains" and domain_tracker is not None:
                # Well-known domains (for competence self-test)
                strong = [
                    (path, entry)
                    for path, entry in domain_tracker.domains.items()
                    if entry.encounter_count >= 3
                    and not self.experience.is_on_cooldown(path)
                ]
                if strong:
                    chosen = random.choice(strong)
                    return chosen[0]

            elif source == "recently_learned" and ans is not None:
                # Domains from recent LEARN signals
                for sig in reversed(ans.signal_buffer):
                    if (
                        getattr(sig, "signal_type", "") == "LEARN"
                        and getattr(sig, "domain_path", "")
                    ):
                        domain = sig.domain_path
                        if not self.experience.is_on_cooldown(domain):
                            return domain

            elif source == "random_exploration":
                pass

        # ── Disconfirmation source: high-confidence schemas ──────────
        # The disconfirmation drive picks schemas to challenge.
        if (
            pressure.action_type == "disconfirm"
            and self.domain_db is not None
        ):
            conf_threshold = dcfg.get("confidence_threshold", 0.7)
            try:
                schemas = self.domain_db.get_all_valid_schemas(limit=20)
                high_conf = [
                    s for s in schemas
                    if s["confidence"] >= conf_threshold
                    and not self.experience.is_on_cooldown(s["domain"])
                ]
                if high_conf:
                    chosen = random.choice(high_conf)
                    return chosen["domain"]
            except Exception:
                pass  # Tables may not exist yet

        # Fallback -- but if everything is on cooldown, return empty
        # to signal that no search should happen right now
        if self.experience.is_on_cooldown("general knowledge"):
            return ""  # Signal: nothing to search
        return "general knowledge"

    def _pick_query(
        self,
        pressure: DrivePressure,
        dcfg: dict,
        domain: str,
        enrichment_context: str | None = None,
    ) -> str:
        """Select a query string from the action templates.

        Humanizes domain paths before inserting into templates:
          'User.Personal.Preferences' -> 'user personal preferences'
          'Philosophy.Consciousness'  -> 'philosophy consciousness'
          'Geography.Structures'      -> 'geography structures'

        When ``enrichment_context`` is provided (Phase 9), it is
        prepended to the template output to give the model richer
        grounding for query generation.
        """
        templates = dcfg.get("action_templates", dcfg.get("reflection_prompts", []))

        human_domain = self._humanize_domain(domain)

        if not templates:
            base = f"What is {human_domain}?"
        else:
            template = random.choice(templates)
            base = template.replace("{domain}", human_domain)

        if enrichment_context:
            return f"{enrichment_context}\n{base}"
        return base

    @staticmethod
    def _humanize_domain(domain: str) -> str:
        """Convert internal domain taxonomy paths to natural language.

        Examples:
            'User.Personal.Preferences' -> 'user personal preferences'
            'Philosophy.Consciousness'  -> 'philosophy of consciousness'
            'Geography.Structures'      -> 'geography structures'
            'general knowledge'         -> 'general knowledge' (unchanged)
        """
        if not domain:
            return "general knowledge"

        # Split on dots and/or camelCase
        parts = domain.replace(".", " ").split()

        # Lowercase everything
        parts = [p.lower() for p in parts]

        # Filter out generic taxonomy noise
        noise_words = {"user", "personal", "details", "general", "misc", "other"}
        meaningful = [p for p in parts if p not in noise_words]

        # If we filtered everything, keep original
        if not meaningful:
            meaningful = parts

        return " ".join(meaningful)

    def _pick_message(
        self,
        dcfg: dict,
        domain: str,
        enrichment_context: str | None = None,
    ) -> str:
        """Select a message for social-type drives.

        When ``enrichment_context`` is provided (Phase 9), it is
        prepended to the template output so the model can generate
        a message grounded in current mood, user interests, etc.
        """
        templates = dcfg.get("message_templates", [])
        if not templates:
            return ""
        human_domain = self._humanize_domain(domain)
        template = random.choice(templates)
        base = template.replace(
            "{domain}", human_domain,
        ).replace("{domains}", human_domain)

        if enrichment_context:
            return f"{enrichment_context}\n{base}"
        return base

    # =======================================================================
    # Stage 3: ACC -- Two-Layer Effort Gate
    # =======================================================================

    def effort_gate(
        self,
        goal: DriveGoal,
        hypothalamus: Any,
        calibrator: Any | None = None,
    ) -> bool:
        """Anterior Cingulate Cortex analog: decide 'will I act?'

        Layer 1 -- Knowledge Calibration:
            Uses DomainTracker, myelination state, and ExperienceTracker
            to produce (base_effort, confidence).

        Layer 2 -- Hormonal Bias:
            Distorts base_effort inversely proportional to confidence.
            Novel domains (confidence ~0) get full hormonal distortion.
            Expert domains (confidence ~1) barely move with mood.

        Returns True if will_to_act exceeds threshold.
        """
        base_effort, confidence = self._knowledge_estimate(
            goal, calibrator,
        )

        # --- Layer 2: Hormonal bias ---
        min_bias = self._kc.get("min_bias_strength", 0.0)
        bias_strength = max(min_bias, 1.0 - confidence)

        dcfg = self._drives.get(goal.drive_name, {})
        effort_bias_cfg = dcfg.get("effort_bias", {})

        # Read hormone levels
        cortisol = self._get_hormone_level(hypothalamus, "cortisol")
        norepi = self._get_hormone_level(hypothalamus, "norepinephrine")
        dopamine = self._get_hormone_level(hypothalamus, "dopamine")
        serotonin = self._get_hormone_level(hypothalamus, "serotonin")

        # Compute effort bias from hormonal state
        cortisol_bias = effort_bias_cfg.get("cortisol_factor", 0.0) * cortisol
        norepi_bias = effort_bias_cfg.get("norepinephrine_factor", 0.0) * norepi
        dopamine_bias = effort_bias_cfg.get("dopamine_factor", 0.0) * dopamine
        serotonin_noise = (
            effort_bias_cfg.get("serotonin_noise_factor", 0.0)
            * (1.0 - serotonin)
        )

        # Total hormonal distortion (scaled by bias_strength)
        hormonal_shift = (
            cortisol_bias + norepi_bias + dopamine_bias
        ) * bias_strength

        # Serotonin noise (low serotonin = unstable gate)
        gate_noise = serotonin_noise * bias_strength

        # Skill myelination reduction: practiced skills feel easier
        myelin_reduction = 0.0
        if goal.skill_name and calibrator is not None:
            dt = getattr(calibrator, "domain_tracker", None)
            if dt and hasattr(dt, "skill_encounters"):
                skill_entry = dt.skill_encounters.get(goal.skill_name)
                if skill_entry:
                    myelin_reduction = skill_entry.myelination_score * 0.3

        # Perceived effort = knowledge base + hormonal distortion - skill myelination
        perceived_effort = base_effort + hormonal_shift - myelin_reduction

        # Will to act = pressure (wanting) - perceived_effort + noise
        will_to_act = goal.pressure - perceived_effort + gate_noise

        # Store computation results on the goal for visibility
        goal.base_effort = base_effort
        goal.confidence = confidence
        goal.perceived_effort = perceived_effort
        goal.will_to_act = will_to_act

        logger.info(
            "ACC: drive=%s base_effort=%.3f confidence=%.3f "
            "bias_strength=%.3f perceived=%.3f will=%.3f threshold=%.3f -> %s",
            goal.drive_name, base_effort, confidence, bias_strength,
            perceived_effort, will_to_act, self._action_threshold,
            "ACT" if will_to_act > self._action_threshold else "INHIBIT",
        )

        return will_to_act > self._action_threshold

    def _knowledge_estimate(
        self,
        goal: DriveGoal,
        calibrator: Any | None,
    ) -> tuple[float, float]:
        """Layer 1: Knowledge calibration.

        Returns (base_effort, confidence) where:
            - base_effort: how hard this domain is expected to be (0.0-1.0)
            - confidence: how calibrated our estimate is (0.0 = no idea, 1.0 = expert)

        Three data sources:
            1. DomainTracker: encounter frequency
            2. Myelination: routing strength
            3. ExperienceTracker: past attempt outcomes
        """
        domain = goal.domain

        # --- Source 1: Domain encounters ---
        encounters = 0
        if calibrator is not None and hasattr(calibrator, "domain_tracker"):
            dt = calibrator.domain_tracker
            if domain in dt.domains:
                encounters = dt.domains[domain].encounter_count

        # --- Source 2: Myelination (routing strength) ---
        myelination = 0.0
        if calibrator is not None and hasattr(calibrator, "domain_tracker"):
            dt = calibrator.domain_tracker
            if domain in dt.domains:
                entry = dt.domains[domain]
                # Myelination strength: normalized encounter count
                # Higher encounters = stronger routing = more myelinated
                myelination = min(1.0, entry.encounter_count / 20.0)

        # --- Source 3: Experience tracker (past attempts) ---
        exp = self.experience.get(domain)

        # --- Compute confidence ---
        confidence = min(
            self._max_confidence,
            (encounters * self._encounter_weight)
            + (myelination * self._myelination_weight)
            + (exp.attempts * self._experience_weight),
        )

        # --- Compute base_effort ---
        if exp.attempts > 0:
            # Use the calibrated effort from the experience tracker.
            # This decreases with each success and increases with each
            # failure (asymmetric: failures teach harder lessons).
            base_effort = exp.last_effort
        else:
            # Novel domain: naive starting point (entrepreneur optimism)
            base_effort = self.experience.initial_effort

        return base_effort, confidence

    @staticmethod
    def _get_hormone_level(hypothalamus: Any, name: str) -> float:
        """Safely read a hormone level."""
        try:
            h = hypothalamus.hormones.get(name)
            if h is not None:
                return h.level
        except (AttributeError, TypeError):
            pass
        return 0.0

    # =======================================================================
    # Front-brain enrichment (Phase 9)
    # =======================================================================

    def set_enrichment_context(self, context: str | None) -> None:
        """Set per-tick enrichment context from front-brain modules.

        Called by the runtime before ``tick()`` so that fallback template
        queries incorporate mood, uncertainty, user interests, and goals.
        """
        self._enrichment_context = context

    def _apply_cross_drive_boost(
        self,
        pressures: list[DrivePressure],
        ctx: dict[str, Any],
    ) -> list[DrivePressure]:
        """Boost drive pressures that align with front-brain context.

        Mapping:
          - WM goals → competence, self_direction boost
          - PP high-uncertainty domains → curiosity boost
          - ToM user interests → social, curiosity boost

        Returns a new list with boosted pressure values (originals are
        immutable dataclasses).
        """
        boost = self._cross_drive_boost
        has_goals = bool(ctx.get("wm_goals"))
        has_uncertainty = bool(ctx.get("high_uncertainty"))
        has_interests = bool(ctx.get("user_interests"))

        if not (has_goals or has_uncertainty or has_interests):
            return pressures

        boosted: list[DrivePressure] = []
        for p in pressures:
            extra = 0.0
            if p.drive_name == "curiosity" and has_uncertainty:
                extra = boost
            elif p.drive_name == "curiosity" and has_interests:
                extra = boost * 0.5
            elif p.drive_name == "social" and has_interests:
                extra = boost
            elif p.drive_name == "competence" and has_goals:
                extra = boost
            elif p.drive_name == "self_direction" and has_goals:
                extra = boost * 0.5

            if extra > 0:
                boosted.append(DrivePressure(
                    drive_name=p.drive_name,
                    hormone=p.hormone,
                    maslow_level=p.maslow_level,
                    deprivation=p.deprivation,
                    pressure=p.pressure + extra,
                    action_type=p.action_type,
                    timestamp=p.timestamp,
                ))
            else:
                boosted.append(p)

        return boosted

    # =======================================================================
    # Main tick
    # =======================================================================

    def tick(
        self,
        hypothalamus: Any,
        calibrator: Any | None = None,
        ans: Any | None = None,
        front_brain_context: dict[str, Any] | None = None,
    ) -> DriveGoal | None:
        """Full drive evaluation cycle.

        Called by the runtime on each drive tick (e.g. every 30s idle).
        Returns a DriveGoal if the agent decides to act, None otherwise.
        """
        # Stage 1: VTA -- compute pressures
        pressures = self.compute_pressures(hypothalamus)

        # Log all pressures (even empty -- shows idle ticks)
        if self._event_logger is not None:
            self._event_logger.log_drive_tick(
                pressures=[p.to_dict() for p in pressures],
            )

        if not pressures:
            # No drives above threshold -- organism is content.
            # Reset frustration: nothing is blocked, nothing is wanted.
            self._consecutive_blocked = 0
            return None

        # Cross-drive coordination (Phase 9): boost pressures for drives
        # whose action domains align with WM goals, PP uncertainty, or
        # user interests.  This makes drives feel purposeful -- curiosity
        # targets what the agent is uncertain about, social reaches out
        # about topics the user cares about, etc.
        if front_brain_context and self._cross_drive_boost > 0:
            pressures = self._apply_cross_drive_boost(
                pressures, front_brain_context,
            )

        # Stage 2: PFC -- select goal
        goal = self.select_goal(pressures, calibrator, ans)
        if goal is None:
            # ACC frustration: drives are wanting but all domains are
            # blocked.  This is the neural signature of frustrated
            # intention -- the ACC detects the conflict between
            # "I want to act" and "I cannot act."
            self._consecutive_blocked += 1
            return None

        # Stage 3: ACC -- effort gate
        should_act = self.effort_gate(goal, hypothalamus, calibrator)

        # Log effort gate decision
        if self._event_logger is not None:
            self._event_logger.log_drive_effort_gate(
                drive=goal.drive_name,
                base_effort=goal.base_effort,
                confidence=goal.confidence,
                bias_strength=1.0 - goal.confidence,
                perceived_effort=goal.perceived_effort,
                will_to_act=goal.will_to_act,
                passed=should_act,
            )

        if not should_act:
            logger.info(
                "DriveEngine: goal inhibited by effort gate (drive=%s, will=%.3f)",
                goal.drive_name, goal.will_to_act,
            )
            self._consecutive_blocked += 1
            return None

        # Goal released!  Frustration resolves -- the ACC conflict
        # clears and dopamine fires from successful intention.
        self._consecutive_blocked = 0

        # Log selected goal
        if self._event_logger is not None:
            self._event_logger.log_drive_goal(goal.to_dict())

        # Mark cooldown and action
        self._cooldowns[goal.drive_name] = time.time()
        self._actions_this_hour.append(time.time())

        logger.info(
            "DriveEngine: goal released! drive=%s action=%s domain=%s will=%.3f",
            goal.drive_name, goal.action_type, goal.domain, goal.will_to_act,
        )

        return goal

    # =======================================================================
    # Frustration (ACC conflict signal)
    # =======================================================================

    @property
    def frustration_ticks(self) -> int:
        """How many consecutive ticks drives had pressure but no goal.

        Maps to the ACC conflict signal: the longer the blockage,
        the stronger the frustration response.  Used by the Inner Loop
        to emit hormonal frustration signals and by SelfState to
        modulate engagement (blocked drives are not engagement,
        they are restlessness).
        """
        return self._consecutive_blocked

    @property
    def is_frustrated(self) -> bool:
        """Whether the drive engine is currently in a frustrated state.

        True when drives are wanting but blocked for at least 2
        consecutive ticks.  The first blocked tick gets a free pass
        (momentary blockage is normal).  Persistent blockage is
        frustration.
        """
        return self._consecutive_blocked >= 2

    # =======================================================================
    # State persistence
    # =======================================================================

    def contribute_to_state(self, self_state: Any, hypothalamus: Any) -> None:
        """Write current drive pressures into the unified SelfState.

        Part of the SelfState collection protocol -- each brain component
        contributes its readings to the unified self-representation.
        """
        try:
            pressures = self.compute_pressures(hypothalamus)
            self_state.drive_pressures = {p.drive_name: p.pressure for p in pressures}
        except Exception:
            pass  # Drive engine may not be fully initialized

    def save_state(self, agent_dir: Path) -> None:
        """Persist experience tracker and cooldown state."""
        exp_file = self._cfg.get("experience_tracker", {}).get(
            "persistence_file", "experience_tracker.json"
        )
        self.experience.save(agent_dir / exp_file)

        # Save cooldowns
        cooldown_path = agent_dir / "drive_cooldowns.json"
        with open(cooldown_path, "w", encoding="utf-8") as f:
            json.dump(self._cooldowns, f, indent=2)

    def load_state(self, agent_dir: Path) -> None:
        """Load persisted experience and cooldown state."""
        exp_file = self._cfg.get("experience_tracker", {}).get(
            "persistence_file", "experience_tracker.json"
        )
        self.experience.load(agent_dir / exp_file)

        cooldown_path = agent_dir / "drive_cooldowns.json"
        if cooldown_path.exists():
            with open(cooldown_path, "r", encoding="utf-8") as f:
                self._cooldowns = json.load(f)

    # =======================================================================
    # Diagnostics
    # =======================================================================

    def get_status(self, hypothalamus: Any) -> dict[str, Any]:
        """Return current drive state for status display."""
        pressures = self.compute_pressures(hypothalamus)
        return {
            "active_drives": [p.to_dict() for p in pressures],
            "actions_this_hour": len(self._actions_this_hour),
            "max_actions_per_hour": self._max_actions_per_hour,
            "experience_domains": self.experience.all_domains,
            "cooldowns": {
                k: round(time.time() - v, 1)
                for k, v in self._cooldowns.items()
            },
        }

    @property
    def tick_interval(self) -> float:
        """Drive tick interval in seconds."""
        return self._tick_seconds
