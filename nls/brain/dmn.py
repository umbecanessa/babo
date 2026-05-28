"""NLS Default Mode Network (DMN) -- Daydreaming and Hippocampal Replay.

The Default Mode Network activates during idle ticks when no drive fires.
In the real brain, the DMN and task-positive network (drives) are
anti-correlated: when you're focused on a task, the DMN is suppressed;
when you're at rest, the DMN activates -- mind-wandering, daydreaming,
memory consolidation.

Two modes of dreaming:

  PASSIVE (original): Text-only.  The DMN samples facts from DomainDB,
  constructs cross-domain reasoning prompts, and produces "daydreams" --
  synthetic experiences that generate new LEARN signals.

  ACTIVE (new): Tool-using.  The DMN uses browser and bash tools to
  forage the internet and filesystem for information relevant to the
  agent's current project context.  The internet is food -- the agent
  eats by browsing, digests via LEARN signals, and absorbs into
  DomainDB.  High-value findings are reported to the user.

Active dreams follow a three-phase cycle:
  1. WONDER (adapter ON)  -- generate a research intention
  2. ACT    (adapter OFF) -- execute tools (read-only, safe)
  3. REFLECT (adapter ON) -- extract LEARN signals, score relevance

Activation probability is modulated by Acetylcholine (ACh) level:
- High ACh (recent learning) = frequent, productive daydreaming
- Low ACh (no recent learning) = quiet resting, rare activation

Configuration: nls/config/dmn.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
from collections import deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DefaultModeNetwork:
    """Default Mode Network engine.

    Manages daydream activation probability, fact sampling,
    and cross-domain replay prompt generation.

    Lifecycle:
        1. Runtime creates DMN at startup
        2. On each idle tick where no drive fires, runtime calls
           should_activate(ach_level)
        3. If True, runtime calls build_replay_prompt() to get
           facts and a reasoning prompt
        4. Runtime generates dream response and extracts signals
        5. Runtime calls record_activation() to start cooldown
        6. On every tick (regardless), runtime calls tick() to
           advance the cooldown counter
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        domain_db: Any | None = None,
        config_data: dict | None = None,
    ):
        if config_data is not None:
            self._config = config_data
        elif config_path is not None:
            self._config = self._load_config(config_path)
        else:
            self._config = {}
        self._domain_db = domain_db
        self.v5_signals = False

        # Activation parameters
        act_cfg = self._config.get("activation", {})
        self._base_prob = act_cfg.get("base_probability", 0.30)
        self._cooldown_max = act_cfg.get("cooldown_ticks", 3)
        self._min_facts = act_cfg.get("min_facts_required", 4)
        self._min_interval_seconds = act_cfg.get("min_interval_seconds", 300)

        # ACh multiplier curve (sigmoid)
        ach_cfg = act_cfg.get("ach_multiplier", {})
        self._ach_floor = ach_cfg.get("floor", 0.3)
        self._ach_ceiling = ach_cfg.get("ceiling", 1.8)
        self._ach_midpoint = ach_cfg.get("midpoint", 0.40)
        self._ach_steepness = ach_cfg.get("steepness", 8)

        # Replay parameters (hippocampal replay -- recombining memories)
        replay_cfg = self._config.get("replay", {})
        self._max_facts = replay_cfg.get("max_facts_per_dream", 4)
        self._min_dream_facts = replay_cfg.get("min_facts_per_dream", 2)
        self._prefer_cross_domain = replay_cfg.get("prefer_cross_domain", True)
        self._prompts = replay_cfg.get("prompts", [
            "You know these facts:\n{facts}\n\n"
            "What connections or patterns do you see between them?",
        ])

        # Exploration parameters (spontaneous thought -- probing LLM knowledge)
        # In the human brain, the DMN doesn't just replay memories.  It
        # also generates spontaneous thoughts -- novel associations,
        # "what if" scenarios, and explorations of latent knowledge.
        # This is the creative, generative mode of daydreaming.
        # We use it to remap base LLM knowledge into NLS domains.
        explore_cfg = self._config.get("exploration", {})
        self._explore_probability = explore_cfg.get("probability", 0.35)
        self._explore_min_facts_seeded = explore_cfg.get(
            "min_facts_for_seeded", 2,
        )
        self._explore_prompts_pure = explore_cfg.get("prompts_pure", [
            "My mind is wandering freely. I pick a random topic "
            "and think about what I know.\n\n"
            "Continue my inner monologue. Explore a specific topic deeply.",
        ])
        self._explore_prompts_seeded = explore_cfg.get("prompts_seeded", [
            "I know these things:\n{facts}\n\n"
            "My mind wanders to something related but unmapped. "
            "What else do I know?\n\n"
            "Continue my inner monologue. Explore adjacent knowledge.",
        ])

        # Active dreams (tool-using, foraging)
        active_cfg = self._config.get("active_dreams", {})
        self._active_enabled = active_cfg.get("enabled", False)
        self._active_probability = active_cfg.get("probability", 0.25)
        self._active_cooldown_max = active_cfg.get("cooldown_ticks", 5)
        self._active_max_iterations = active_cfg.get("max_iterations", 5)
        self._active_time_budget = active_cfg.get("time_budget_seconds", 60)
        self._active_relevance_threshold = active_cfg.get(
            "finding_relevance_threshold", 0.6,
        )

        # Active dream types with weighted selection
        types_cfg = active_cfg.get("types", {})
        self._active_types: dict[str, dict] = {}
        for type_name, type_def in types_cfg.items():
            parsed = {
                "weight": type_def.get("weight", 0.0),
                "allowed_tools": type_def.get("allowed_tools", []),
                "safety": type_def.get("safety", {}),
            }
            if "max_iterations" in type_def:
                parsed["max_iterations"] = type_def["max_iterations"]
            if "time_budget_seconds" in type_def:
                parsed["time_budget_seconds"] = type_def[
                    "time_budget_seconds"
                ]
            self._active_types[type_name] = parsed

        # Active dream prompts
        self._active_prompts_wonder = active_cfg.get("prompts_wonder", [
            "I've been helping with a project. What should I research?\n"
            "{project_context}\n\n"
            "Generate a specific research question.",
        ])
        self._active_prompts_reflect = active_cfg.get("prompts_reflect", [
            "I found something during my research:\n{findings}\n\n"
            "Reflect on relevance. End with RELEVANCE: <score>",
        ])

        # Project context config
        ctx_cfg = active_cfg.get("project_context", {})
        self._ctx_include_conversation = ctx_cfg.get(
            "include_recent_conversation", True,
        )
        self._ctx_max_turns = ctx_cfg.get("max_conversation_turns", 5)
        self._ctx_include_project_facts = ctx_cfg.get(
            "include_project_facts", True,
        )
        self._ctx_fact_domains = ctx_cfg.get(
            "project_fact_domains",
            ["Project", "Code", "Technology", "User.Preferences"],
        )
        self._ctx_include_errors = ctx_cfg.get("include_recent_errors", True)
        self._ctx_include_files = ctx_cfg.get("include_recent_files", True)
        self._ctx_max_files = ctx_cfg.get("max_recent_files", 10)

        # Internal state
        self._cooldown_remaining = 0
        self._active_cooldown_remaining = 0
        self._total_activations = 0
        self._total_explorations = 0
        self._total_replays = 0
        self._total_active_dreams = 0

        # Fact deduplication: track which facts have been replayed in
        # this waking session.  Once a fact has been dreamed about, it's
        # "exhausted" until new knowledge arrives.  When all facts are
        # exhausted, the DMN switches to pure exploration (probing the
        # LLM's latent knowledge for new domains to map).
        self._replayed_fact_keys: set[str] = set()

        # Content-level dedup: rolling window of recent dream output
        # fingerprints (coarse, catches near-verbatim duplicates).
        self._recent_dream_hashes: deque[str] = deque(maxlen=80)

        # Seed-level dedup: prevent generating dreams from the same
        # input facts repeatedly.
        self._recent_seed_hashes: deque[str] = deque(maxlen=40)

        # Time-based minimum interval between dream activations.
        self._last_activation_time: float = 0.0

        # Domain-level cooldown: track top-level domains used in recent
        # dreams (both replay and exploration).  Facts from these domains
        # are excluded from sampling for the next few dreams, forcing
        # genuine topic diversity.
        self._recent_dream_domains: deque[str] = deque(maxlen=6)

        # Phase 8: Enriched dream generation config
        gen_cfg = self._config.get("dream_generation", {})
        self._enriched_mode = gen_cfg.get("mode", "enriched")
        self._social_sim_probability = gen_cfg.get(
            "social_simulation_probability", 0.15,
        )
        self._enriched_min_context = gen_cfg.get("min_context_sections", 2)

        logger.info(
            "DMN initialized: base_prob=%.2f, cooldown=%d ticks, "
            "min_facts=%d, replay_prompts=%d, explore_prob=%.2f, "
            "explore_prompts=%d+%d, active=%s (prob=%.2f)",
            self._base_prob, self._cooldown_max,
            self._min_facts, len(self._prompts),
            self._explore_probability,
            len(self._explore_prompts_pure),
            len(self._explore_prompts_seeded),
            self._active_enabled, self._active_probability,
        )

    @staticmethod
    def _load_config(path: str | Path) -> dict:
        """Load DMN configuration from JSON."""
        path = Path(path)
        if not path.exists():
            logger.warning("DMN config not found at %s, using defaults", path)
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # =======================================================================
    # Activation Gate
    # =======================================================================

    def _ach_multiplier(self, ach_level: float) -> float:
        """Compute activation probability multiplier from ACh level.

        Uses a sigmoid curve: low ACh -> low multiplier (less dreaming),
        high ACh -> high multiplier (more dreaming). The curve saturates
        at ceiling to prevent runaway activation.
        """
        # Sigmoid: floor + (ceiling - floor) / (1 + exp(-steepness * (x - midpoint)))
        x = ach_level - self._ach_midpoint
        try:
            sig = 1.0 / (1.0 + math.exp(-self._ach_steepness * x))
        except OverflowError:
            sig = 0.0 if x < 0 else 1.0

        return self._ach_floor + (self._ach_ceiling - self._ach_floor) * sig

    def should_activate(self, ach_level: float) -> bool:
        """Decide whether to activate the DMN on this idle tick.

        Returns True if:
        1. Cooldown has expired (not in refractory period)
        2. DomainDB exists (even if empty -- exploration mode can
           activate without existing facts)
        3. Probabilistic gate passes (modulated by ACh level)

        Note: the old min_facts gate has been relaxed.  Exploration
        mode doesn't need existing facts -- it probes the base LLM's
        knowledge directly.  The min_facts requirement is now only
        enforced inside ``build_replay_prompt()`` for replay mode.
        Exploration can fire even with an empty DomainDB.

        Args:
            ach_level: Current Acetylcholine hormone level (0.0 to 1.0)

        Returns:
            True if the DMN should produce a daydream this tick
        """
        # Gate 1: cooldown (tick-based)
        if self._cooldown_remaining > 0:
            return False

        # Gate 1.5: time-based minimum interval
        if self._last_activation_time > 0:
            elapsed = time.time() - self._last_activation_time
            if elapsed < self._min_interval_seconds:
                logger.debug(
                    "DMN time gate: %.0fs since last dream, need %.0fs",
                    elapsed, self._min_interval_seconds,
                )
                return False

        # Gate 2: DomainDB must exist (but can be empty)
        if self._domain_db is None:
            return False

        # Gate 3: probabilistic (ACh-modulated)
        multiplier = self._ach_multiplier(ach_level)
        effective_prob = min(0.95, self._base_prob * multiplier)

        fired = random.random() < effective_prob

        logger.info(
            "DMN gate: ach=%.3f, mult=%.3f, prob=%.3f, fired=%s",
            ach_level, multiplier, effective_prob, fired,
        )

        return fired

    # =======================================================================
    # Replay Prompt Generation
    # =======================================================================

    @staticmethod
    def _fact_key(fact: dict) -> str:
        """Stable identity for a fact (domain + first 80 chars of value)."""
        return f"{fact.get('domain', '')}::{fact.get('value', '')[:80]}"

    def _filter_by_domain_cooldown(self, facts: list[dict]) -> list[dict]:
        """Exclude facts whose top-level domain was used in recent dreams.

        Prevents the model from fixating on a single domain across
        consecutive dreams.  Falls back to the full list if filtering
        would leave too few facts.
        """
        if not self._recent_dream_domains:
            return facts

        cooled = set(self._recent_dream_domains)
        filtered = [
            f for f in facts
            if (f.get("domain", "").split(".")[0] or "") not in cooled
        ]

        if len(filtered) >= self._min_dream_facts:
            return filtered
        return facts  # fallback: don't starve sampling

    def build_replay_prompt(self) -> tuple[list[dict], str]:
        """Sample facts from DomainDB and build a cross-domain reasoning prompt.

        Facts that have already been replayed in this waking session are
        excluded.  When all facts are exhausted, returns empty so the
        caller falls through to exploration mode (probing the LLM's
        latent knowledge for unmapped domains).

        Returns:
            Tuple of (sampled_facts_as_dicts, prompt_string)
            Empty tuple values if no facts available.
        """
        if self._domain_db is None:
            return [], ""

        # Get all facts and sample
        try:
            raw_facts = self._domain_db.get_all_facts()
            all_facts = [
                {"domain": f.domain_path, "value": f.current_value}
                for f in raw_facts
            ]
            if len(all_facts) > 50:
                all_facts = random.sample(all_facts, 50)
        except Exception:
            all_facts = []

        # Auto-reset replayed keys when >80% of facts are exhausted.
        # This prevents the DMN from permanently switching to exploration
        # after cycling through facts once.  Previously-dreamed facts may
        # yield different insights when recombined later.
        if all_facts and len(self._replayed_fact_keys) > len(all_facts) * 0.8:
            logger.info(
                "DMN: auto-resetting replay memory (%d/%d facts exhausted)",
                len(self._replayed_fact_keys), len(all_facts),
            )
            self._replayed_fact_keys.clear()

        # Filter out already-replayed facts
        fresh_facts = [
            f for f in all_facts
            if self._fact_key(f) not in self._replayed_fact_keys
        ]

        if len(fresh_facts) < self._min_dream_facts:
            return [], ""

        # Domain-level cooldown: deprioritise domains used in recent dreams
        fresh_facts = self._filter_by_domain_cooldown(fresh_facts)

        if len(fresh_facts) < self._min_dream_facts:
            return [], ""

        # Sample facts, preferring cross-domain diversity
        sample_size = min(
            random.randint(self._min_dream_facts, self._max_facts),
            len(fresh_facts),
        )

        if self._prefer_cross_domain and sample_size >= 2:
            sample = self._cross_domain_sample(fresh_facts, sample_size)
        else:
            sample = random.sample(fresh_facts, sample_size)

        # Seed-level dedup: skip if the same set of facts was recently dreamed about
        seed_fp = hashlib.md5(
            "|".join(sorted(self._fact_key(f) for f in sample)).encode()
        ).hexdigest()
        if seed_fp in self._recent_seed_hashes:
            logger.info("DMN: seed set already dreamed recently, skipping")
            return [], ""
        self._recent_seed_hashes.append(seed_fp)

        # Mark these facts as replayed so they won't be picked again
        for f in sample:
            self._replayed_fact_keys.add(self._fact_key(f))

        # Enrich with knowledge-graph connected facts (associative recall)
        if hasattr(self._domain_db, "get_connected_facts"):
            connected_domains: set[str] = set()
            for f in sample:
                dom = f.get("domain", "")
                if not dom:
                    continue
                try:
                    connected = self._domain_db.get_connected_facts(dom, max_hops=1)
                    for edge in connected[:2]:
                        cf = edge.get("fact")
                        if cf is not None:
                            cd = cf.domain_path
                            if cd not in connected_domains:
                                connected_domains.add(cd)
                                sample.append({
                                    "domain": cd,
                                    "value": cf.current_value,
                                    "connected_via": edge.get("relationship", ""),
                                })
                except Exception:
                    pass

        # Build fact text
        fact_lines = []
        for f in sample:
            value = f.get("value", "")
            if "\n[context:" in value:
                value = value.split("\n[context:")[0].strip()
            via = f.get("connected_via")
            if via:
                fact_lines.append(f"- {value} [connected: {via}]")
            else:
                fact_lines.append(f"- {value}")

        facts_text = "\n".join(fact_lines)

        # Select and fill prompt template
        template = random.choice(self._prompts)
        prompt = template.replace("{facts}", facts_text)

        return sample, prompt

    def _cross_domain_sample(
        self, facts: list[dict], n: int,
    ) -> list[dict]:
        """Sample facts from different top-level domains for richer connections.

        Tries to pick one fact per domain. Falls back to random if
        there aren't enough distinct domains.
        """
        # Group by top-level domain
        by_domain: dict[str, list[dict]] = {}
        for f in facts:
            domain = f.get("domain", "unknown")
            top = domain.split(".")[0] if "." in domain else domain
            by_domain.setdefault(top, []).append(f)

        # Pick one from each domain
        domains = list(by_domain.keys())
        random.shuffle(domains)

        sample = []
        for d in domains:
            if len(sample) >= n:
                break
            sample.append(random.choice(by_domain[d]))

        # Fill remaining slots randomly if needed
        if len(sample) < n:
            remaining = [f for f in facts if f not in sample]
            if remaining:
                extra = random.sample(remaining, min(n - len(sample), len(remaining)))
                sample.extend(extra)

        return sample

    # =======================================================================
    # Spontaneous Exploration (probing base LLM knowledge)
    # =======================================================================

    def build_exploration_prompt(self) -> tuple[list[dict], str, str]:
        """Generate a prompt that probes the base LLM's latent knowledge.

        In the human brain, spontaneous thought generation during
        daydreaming isn't just replaying memories -- the mind also
        wanders to novel territories, making new connections between
        things you "know but haven't thought about."  This is how
        implicit knowledge becomes explicit.

        Two sub-modes:
          - **Pure exploration**: no fact seeds, the model picks a
            random topic from its own knowledge.  Like staring out
            a window and having a random thought pop up.
          - **Seeded exploration**: a few existing facts serve as
            "anchor points" and the model explores the adjacent
            knowledge space.  Like remembering a conversation about
            physics and then your mind drifting to engineering.

        Returns:
            Tuple of (seed_facts, prompt_string, mode)
            where mode is "pure" or "seeded".
        """
        # Decide sub-mode based on available facts
        has_enough_facts = False
        all_facts: list[dict] = []

        if self._domain_db is not None:
            try:
                raw_facts = self._domain_db.get_all_facts()
                all_facts = [
                    {"domain": f.domain_path, "value": f.current_value}
                    for f in raw_facts
                ]
                has_enough_facts = (
                    len(all_facts) >= self._explore_min_facts_seeded
                )
            except Exception:
                pass

        # 60% seeded (if facts available), 40% pure -- encourages the
        # model to venture further from known territory sometimes
        use_seeded = has_enough_facts and random.random() < 0.60

        if use_seeded:
            # Filter seeds by both fact-key and domain cooldown to
            # prevent the same topic from seeding consecutive dreams.
            eligible = [
                f for f in all_facts
                if self._fact_key(f) not in self._replayed_fact_keys
            ]
            eligible = self._filter_by_domain_cooldown(eligible)
            if len(eligible) < self._explore_min_facts_seeded:
                eligible = all_facts  # fallback

            seed_count = min(2, len(eligible))
            if self._prefer_cross_domain and seed_count >= 2:
                seeds = self._cross_domain_sample(eligible, seed_count)
            else:
                seeds = random.sample(eligible, seed_count)

            # Mark these seeds as used
            for f in seeds:
                self._replayed_fact_keys.add(self._fact_key(f))

            # Build fact text
            fact_lines = []
            for f in seeds:
                value = f.get("value", "")
                if "\n[context:" in value:
                    value = value.split("\n[context:")[0].strip()
                fact_lines.append(f"- {value}")
            facts_text = "\n".join(fact_lines)

            template = random.choice(self._explore_prompts_seeded)
            prompt = template.replace("{facts}", facts_text)
            return seeds, prompt, "seeded"
        else:
            # Pure exploration -- no seeds, the model generates freely
            template = random.choice(self._explore_prompts_pure)
            return [], template, "pure"

    def build_dream(
        self,
        self_state: Any = None,
        theory_of_mind: Any = None,
        working_memory: Any = None,
        predictive: Any = None,
        narrative_self: Any = None,
    ) -> tuple[list[dict], str, str]:
        """Build a dream prompt, choosing between replay and exploration.

        This is the unified entry point for dream generation.  When
        front-brain context is available, the method prefers enriched
        model-generated dreams over static templates.  When context is
        insufficient, it falls back to template-based replay/exploration.

        Social simulation mode (modeling the user's perspective) is
        triggered probabilistically when Theory of Mind data is available.

        Returns:
            Tuple of (facts, prompt, mode) where mode is one of:
            "replay", "pure", "seeded", "enriched", or
            "social_simulation".
        """
        has_context = any(
            x is not None
            for x in (self_state, theory_of_mind, working_memory,
                       predictive, narrative_self)
        )

        if has_context and self._enriched_mode in ("enriched", "hybrid"):
            # Social simulation: model the user's perspective
            if (
                theory_of_mind is not None
                and random.random() < self._social_sim_probability
            ):
                result = self.build_social_simulation(
                    theory_of_mind=theory_of_mind,
                    working_memory=working_memory,
                    narrative_self=narrative_self,
                )
                if result is not None:
                    return result

            # Enriched dream: dynamic prompt from front-brain context
            facts, prompt, mode = self.build_enriched_dream(
                self_state=self_state,
                theory_of_mind=theory_of_mind,
                working_memory=working_memory,
                predictive=predictive,
                narrative_self=narrative_self,
            )
            if mode == "enriched":
                return facts, prompt, mode

        # ── Classic template fallback ──
        can_replay = False
        if self._domain_db is not None:
            try:
                can_replay = (
                    self._domain_db.fact_count() >= self._min_facts
                )
            except Exception:
                pass

        if can_replay and random.random() > self._explore_probability:
            facts, prompt = self.build_replay_prompt()
            if prompt:
                return facts, prompt, "replay"
            logger.info(
                "DMN: replay exhausted (%d facts already dreamed), "
                "falling through to exploration",
                len(self._replayed_fact_keys),
            )

        facts, prompt, submode = self.build_exploration_prompt()
        return facts, prompt, submode

    # =======================================================================
    # Active Dreaming (tool-using, foraging)
    # =======================================================================

    def should_active_dream(self) -> bool:
        """Decide whether this DMN activation should be an active dream.

        Active dreams are gated separately from passive dreams:
        1. Active dreaming must be enabled in config
        2. Active cooldown must have expired
        3. Probabilistic gate must pass

        Called AFTER should_activate() has already returned True.
        """
        if not self._active_enabled:
            return False
        if self._active_cooldown_remaining > 0:
            return False
        return random.random() < self._active_probability

    def select_active_dream_type(self) -> tuple[str, dict]:
        """Weighted selection of active dream type (browse, bash, practice).

        Returns:
            Tuple of (type_name, type_config) or ("browse", default_config)
            if no types configured.
        """
        if not self._active_types:
            return "browse", {"allowed_tools": ["web_search", "web_fetch"],
                              "safety": {"read_only": True}}

        types = list(self._active_types.items())
        weights = [t[1]["weight"] for t in types]
        total = sum(weights)
        if total <= 0:
            return types[0]

        r = random.random() * total
        cumulative = 0.0
        for name, cfg in types:
            cumulative += cfg["weight"]
            if r <= cumulative:
                return name, cfg

        return types[-1]

    @staticmethod
    def _extract_idle_intention(working_memory: Any) -> str | None:
        """Pull an idle-time intention from WM prospective memory.

        Only returns an intention whose trigger explicitly matches
        idle-related keywords.  Returns None if no match — the caller
        falls through to a generic autonomous prompt in that case.
        """
        _IDLE_KEYWORDS = {
            "idle", "free time", "downtime", "nothing to do",
            "spare time", "when free", "when idle", "bored",
        }
        try:
            intentions = working_memory.get_intentions()
            if not intentions:
                return None

            for intn in intentions:
                trigger_lower = (intn.trigger or "").lower()
                if any(kw in trigger_lower for kw in _IDLE_KEYWORDS):
                    return intn.content

            return None
        except Exception:
            return None

    def build_project_context(
        self,
        conversation_history: list[dict] | None = None,
        recent_files: list[str] | None = None,
        recent_errors: list[str] | None = None,
    ) -> str:
        """Build project context string for active dream WONDER phase.

        Aggregates:
        - Recent conversation turns (what is the user working on?)
        - Project-related facts from DomainDB
        - Recently touched files
        - Recent errors/failures

        This is what turns random daydreaming into productive
        background thinking about the user's actual work.
        """
        sections: list[str] = []

        # 1. Recent conversation (compressed)
        if self._ctx_include_conversation and conversation_history:
            turns = conversation_history[-self._ctx_max_turns:]
            conv_lines = []
            for turn in turns:
                role = turn.get("role", "?")
                content = turn.get("content") or ""
                if content and isinstance(content, str):
                    preview = content[:200]
                    if len(content) > 200:
                        preview += "..."
                    conv_lines.append(f"  [{role}]: {preview}")
            if conv_lines:
                sections.append(
                    "Recent conversation:\n" + "\n".join(conv_lines)
                )

        # 2. Project facts from DomainDB
        if self._ctx_include_project_facts and self._domain_db is not None:
            try:
                raw_facts = self._domain_db.get_all_facts()
                project_facts_raw = []
                for f in raw_facts:
                    domain = f.domain_path
                    if any(domain.startswith(d) for d in self._ctx_fact_domains):
                        value = f.current_value
                        if "\n[context:" in value:
                            value = value.split("\n[context:")[0].strip()
                        # Keep the raw fact object so we can sort by recency
                        project_facts_raw.append((f, f"  - [{domain}] {value[:150]}"))

                if project_facts_raw:
                    # Sort by most recently updated so the currently-active
                    # project naturally rises to the top.  random.sample would
                    # pull facts from any project equally, causing context
                    # contamination when multiple projects exist in DomainDB.
                    try:
                        project_facts_raw.sort(
                            key=lambda x: getattr(x[0], "updated_at", 0) or 0,
                            reverse=True,
                        )
                    except Exception:
                        pass
                    project_facts = [line for _, line in project_facts_raw[:15]]
                    sections.append(
                        "What I know about the project:\n"
                        + "\n".join(project_facts)
                    )
            except Exception:
                pass

        # 3. Recently touched files
        if self._ctx_include_files and recent_files:
            files = recent_files[-self._ctx_max_files:]
            sections.append(
                "Recently worked with files:\n"
                + "\n".join(f"  - {f}" for f in files)
            )

        # 4. Recent errors
        if self._ctx_include_errors and recent_errors:
            errors = recent_errors[-5:]
            sections.append(
                "Recent errors/issues:\n"
                + "\n".join(f"  - {e[:200]}" for e in errors)
            )

        if not sections:
            return "(No project context available -- exploring freely.)"

        return "\n\n".join(sections)

    def build_active_dream(
        self,
        conversation_history: list[dict] | None = None,
        recent_files: list[str] | None = None,
        recent_errors: list[str] | None = None,
        self_state: Any = None,
        theory_of_mind: Any = None,
        working_memory: Any = None,
        predictive: Any = None,
    ) -> tuple[str, str, dict] | None:
        """Build an active dream: WONDER prompt + type selection.

        This is the entry point for active dreaming.  When front-brain
        context is available and enriched mode is enabled, uses
        ``build_enriched_active_dream()`` for richer research questions.
        Falls back to template-based prompts otherwise.

        Returns:
            Tuple of (wonder_prompt, dream_type, type_config) or None
            if active dreaming can't proceed.
        """
        has_context = any(
            x is not None
            for x in (self_state, theory_of_mind, working_memory,
                       predictive)
        )

        # Try enriched active dream first
        if has_context and self._enriched_mode in ("enriched", "hybrid"):
            result = self.build_enriched_active_dream(
                conversation_history=conversation_history,
                recent_files=recent_files,
                recent_errors=recent_errors,
                self_state=self_state,
                theory_of_mind=theory_of_mind,
                working_memory=working_memory,
                predictive=predictive,
            )
            if result is not None:
                prompt, dream_type, type_config = result
                logger.info(
                    "DMN: enriched active dream prepared (type=%s, "
                    "tools=%s)",
                    dream_type,
                    type_config.get("allowed_tools", []),
                )
                return result

        # ── Classic template fallback ──
        dream_type, type_config = self.select_active_dream_type()

        project_context = self.build_project_context(
            conversation_history=conversation_history,
            recent_files=recent_files,
            recent_errors=recent_errors,
        )

        # Autonomous fallback (no enriched context available)
        if dream_type == "autonomous":
            intention_text = (
                self._extract_idle_intention(working_memory)
                if working_memory is not None
                else None
            )
            workspace = type_config.get(
                "safety", {},
            ).get("workspace_dir", "autonomous_workspace")
            tools = ", ".join(type_config.get("allowed_tools", []))
            if intention_text:
                prompt = (
                    "The user has given me a standing instruction "
                    "for my idle time:\n"
                    f"{intention_text}\n\n"
                    f"I have access to: {tools}.\n"
                    f"My workspace is: {workspace}/\n\n"
                    "What concrete step should I take next? "
                    "Break it into a single actionable step."
                )
            else:
                prompt = (
                    "I have some idle time and full tool access. "
                    "Here's what I know:\n"
                    f"{project_context}\n\n"
                    f"I have access to: {tools}.\n"
                    f"My workspace is: {workspace}/\n\n"
                    "What would be the most useful thing I can "
                    "build, explore, or prepare right now? "
                    "Pick one concrete, actionable step."
                )
            logger.info(
                "DMN: autonomous dream (classic fallback, "
                "has_intention=%s, tools=%s)",
                intention_text is not None, tools,
            )
            return prompt, dream_type, type_config

        if not self._active_prompts_wonder:
            return None

        template = random.choice(self._active_prompts_wonder)
        prompt = template.replace("{project_context}", project_context)

        logger.info(
            "DMN: active dream prepared (type=%s, tools=%s, "
            "context_length=%d)",
            dream_type,
            type_config.get("allowed_tools", []),
            len(project_context),
        )

        return prompt, dream_type, type_config

    def build_active_reflect_prompt(self, findings: str) -> str:
        """Build the REFLECT prompt for scoring an active dream's results.

        Args:
            findings: Text summary of what the active dream found.

        Returns:
            Prompt string for the REFLECT phase.
        """
        if not self._active_prompts_reflect:
            return (
                f"I researched something:\n{findings}\n\n"
                "Reflect on relevance. End with RELEVANCE: <score>"
            )

        template = random.choice(self._active_prompts_reflect)
        return template.replace("{findings}", findings)

    # =======================================================================
    # State Management
    # =======================================================================

    def record_activation(self, mode: str = "replay") -> None:
        """Record a successful daydream. Starts cooldown timer.

        Args:
            mode: "replay", "pure", "seeded", or "active_*" -- for
            telemetry.  Active dreams use a separate cooldown.
        """
        if mode.startswith("active"):
            self._active_cooldown_remaining = self._active_cooldown_max
            self._total_active_dreams += 1
        else:
            self._cooldown_remaining = self._cooldown_max

        self._total_activations += 1
        self._last_activation_time = time.time()

        if mode == "replay":
            self._total_replays += 1
        elif not mode.startswith("active"):
            self._total_explorations += 1

        logger.info(
            "DMN activated (total: %d, replays: %d, explorations: %d, "
            "active: %d, mode: %s), cooldown: %d/%d ticks",
            self._total_activations, self._total_replays,
            self._total_explorations, self._total_active_dreams,
            mode, self._cooldown_remaining,
            self._active_cooldown_remaining,
        )

    def reset_replay_memory(self) -> None:
        """Clear the replayed-facts tracker.

        Call this when the agent wakes up or when new facts are stored
        into DomainDB.  The freshly consolidated knowledge means
        previously-dreamed facts may yield new insights, so they become
        eligible for replay again.
        """
        count = len(self._replayed_fact_keys)
        self._replayed_fact_keys.clear()
        self._recent_dream_hashes.clear()
        self._recent_dream_domains.clear()
        self._recent_seed_hashes.clear()
        if count > 0:
            logger.info("DMN replay memory reset (%d facts cleared)", count)

    @staticmethod
    def _content_fingerprint(text: str) -> str:
        """Produce a coarse fingerprint from dream output.

        Normalises whitespace, lowercases, and hashes the first 500
        non-whitespace characters.  Two dreams about the same topic
        will usually share this prefix even if they diverge later.
        """
        normalised = " ".join(text.lower().split())[:500]
        return hashlib.md5(normalised.encode()).hexdigest()

    def is_duplicate_dream(self, dream_text: str) -> bool:
        """Check whether *dream_text* is near-identical to a recent dream."""
        fp = self._content_fingerprint(dream_text)
        return fp in self._recent_dream_hashes

    def register_dream_output(
        self, dream_text: str, facts_used: list[dict] | None = None,
    ) -> None:
        """Record a dream's fingerprint and track domains used."""
        fp = self._content_fingerprint(dream_text)
        self._recent_dream_hashes.append(fp)

        # Track top-level domains of facts used in this dream so
        # subsequent dreams sample from different territory.
        if facts_used:
            for f in facts_used:
                domain = f.get("domain", "")
                top = domain.split(".")[0] if "." in domain else domain
                if top and top not in self._recent_dream_domains:
                    self._recent_dream_domains.append(top)

    def tick(self) -> None:
        """Advance one tick. Decrements both passive and active cooldowns."""
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
        if self._active_cooldown_remaining > 0:
            self._active_cooldown_remaining -= 1

    @property
    def total_activations(self) -> int:
        """Total number of daydream activations this session."""
        return self._total_activations

    @property
    def on_cooldown(self) -> bool:
        """Whether the DMN is in its passive refractory period."""
        return self._cooldown_remaining > 0

    @property
    def active_enabled(self) -> bool:
        """Whether active dreaming is enabled in config."""
        return self._active_enabled

    @active_enabled.setter
    def active_enabled(self, value: bool) -> None:
        """Allow runtime toggling of active dreams (e.g. from UI)."""
        self._active_enabled = value
        logger.info("DMN active dreaming %s", "enabled" if value else "disabled")

    @property
    def active_dream_config(self) -> dict[str, Any]:
        """Return active dream configuration for UI display."""
        return {
            "enabled": self._active_enabled,
            "probability": self._active_probability,
            "cooldown_ticks": self._active_cooldown_max,
            "max_iterations": self._active_max_iterations,
            "time_budget_seconds": self._active_time_budget,
            "relevance_threshold": self._active_relevance_threshold,
            "types": {
                name: {"weight": cfg["weight"], "tools": cfg["allowed_tools"]}
                for name, cfg in self._active_types.items()
            },
        }

    # =======================================================================
    # Enriched Dream Generation (Phase 8 -- model-generated, not templates)
    # =======================================================================

    def build_enriched_dream(
        self,
        self_state: Any = None,
        theory_of_mind: Any = None,
        working_memory: Any = None,
        predictive: Any = None,
        narrative_self: Any = None,
    ) -> tuple[list[dict], str, str]:
        """Build a dream using enriched context from front-brain modules.

        Instead of selecting from static template arrays, this method
        dynamically constructs a dream prompt seeded with:
          - Current mood, energy, momentum (from self_state / temporal self)
          - Uncertainty domains (from predictive processing)
          - User interests (from Theory of Mind)
          - WM themes & unfulfilled intentions (from working memory)
          - Narrative arc and regulation state (from narrative self)
          - DomainDB facts (existing)

        Falls back to ``build_dream()`` if insufficient context.
        """
        # Gather context sections
        sections: list[str] = []

        # 1. Self-state: mood, energy, momentum
        if self_state is not None:
            mood = getattr(self_state, "mood_label", "neutral")
            energy = getattr(self_state, "energy", 1.0)
            momentum = getattr(self_state, "momentum", "stable")
            felt_idle = getattr(self_state, "felt_idle", "brief")
            parts = []
            if mood != "neutral":
                parts.append(f"mood is {mood}")
            if energy < 0.8:
                parts.append(f"energy at {energy:.0%}")
            if momentum != "stable":
                parts.append(f"trajectory is {momentum}")
            if felt_idle not in ("brief", ""):
                parts.append(f"idle feels {felt_idle}")
            if parts:
                sections.append(
                    "My current state: " + ", ".join(parts) + "."
                )

        # 2. Uncertainty: what am I unsure about?
        if predictive is not None:
            high_unc = predictive.get_high_uncertainty_domains()
            if high_unc:
                domains_str = ", ".join(d for d, _ in high_unc[:4])
                sections.append(
                    f"Things I'm uncertain about: {domains_str}."
                )

        # 3. User interests from Theory of Mind
        if theory_of_mind is not None:
            try:
                user = theory_of_mind.get_user()
                if user.interests:
                    top_interests = sorted(
                        user.interests.items(),
                        key=lambda x: x[1], reverse=True,
                    )[:5]
                    interests_str = ", ".join(k for k, _ in top_interests)
                    sections.append(
                        f"Topics the user cares about: {interests_str}."
                    )
            except Exception:
                pass

        # 4. Working Memory themes and intentions
        if working_memory is not None:
            try:
                goals = working_memory.get_goal_stack(limit=3)
                if goals:
                    goal_strs = [g.content[:80] for g in goals]
                    sections.append(
                        "Active goals: " + "; ".join(goal_strs) + "."
                    )
                intentions = working_memory.get_prospective(limit=3)
                if intentions:
                    intent_strs = [i.content[:80] for i in intentions]
                    sections.append(
                        "Unfulfilled intentions: "
                        + "; ".join(intent_strs) + "."
                    )
            except Exception:
                pass

        # 5. Narrative arc
        if narrative_self is not None:
            try:
                ep = narrative_self._current_episode
                if ep is not None:
                    arc = ep.arc_summary()
                    if arc and arc != "neutral":
                        sections.append(
                            f"Current episode arc: {arc}."
                        )
                coherence_lbl = narrative_self.coherence_label()
                if coherence_lbl not in ("coherent", ""):
                    sections.append(
                        f"Narrative coherence: {coherence_lbl}."
                    )
            except Exception:
                pass

        # 6. DomainDB facts (same sampling as replay, but fewer)
        facts: list[dict] = []
        fact_lines: list[str] = []
        if self._domain_db is not None:
            try:
                all_facts = self._domain_db.get_all_facts()
                available = [
                    f for f in all_facts
                    if self._fact_key(f) not in self._replayed_fact_keys
                ]
                if available:
                    sample_size = min(3, len(available))
                    sampled = random.sample(available, sample_size)
                    for f in sampled:
                        facts.append({
                            "domain_path": f.domain_path,
                            "value": (f.current_value or "")[:150],
                        })
                        fact_lines.append(
                            f"  - [{f.domain_path}] "
                            f"{(f.current_value or '')[:150]}"
                        )
                        self._replayed_fact_keys.add(self._fact_key(f))
            except Exception:
                pass

        if fact_lines:
            sections.append(
                "Things I know:\n" + "\n".join(fact_lines)
            )

        # Need at least some context to produce a rich dream
        if len(sections) < self._enriched_min_context:
            return self.build_dream()

        context_block = "\n".join(sections)

        prompt = (
            f"{context_block}\n\n"
            "I'm resting. My mind wanders freely over all of this — "
            "not to solve a problem, but to let connections form "
            "naturally. What patterns do I notice? What surprises me? "
            "What am I genuinely curious about?\n\n"
            "Continue my private inner monologue using I/me/my. "
            "Let thoughts flow freely. When I notice a new insight "
            "or connection, "
            + "note it privately; learning is recorded by your autonomic nervous system."
        )

        return facts, prompt, "enriched"

    def build_social_simulation(
        self,
        theory_of_mind: Any = None,
        working_memory: Any = None,
        narrative_self: Any = None,
    ) -> tuple[list[dict], str, str] | None:
        """Build a social simulation dream: model the user's perspective.

        During idle, the DMN doesn't just replay facts -- it also runs
        social simulation.  The agent imagines what the user might be
        doing, what they'll want to discuss next, and what would be
        genuinely helpful to share.

        Returns None if insufficient user context.
        """
        sections: list[str] = []

        # User model
        if theory_of_mind is not None:
            try:
                user = theory_of_mind.get_user()
                style = user.style_summary()
                if style:
                    sections.append(f"The user's style: {style}.")
                if user.interests:
                    top = sorted(
                        user.interests.items(),
                        key=lambda x: x[1], reverse=True,
                    )[:5]
                    sections.append(
                        "Their interests: "
                        + ", ".join(k for k, _ in top) + "."
                    )
                temp = theory_of_mind._temperature
                if temp is not None:
                    sections.append(
                        f"Conversation warmth: {temp.label()}."
                    )
            except Exception:
                pass

        # Recent interaction context from WM
        if working_memory is not None:
            try:
                window = working_memory.get_attention_window(k=3)
                if window:
                    items = [s.content[:80] for s in window]
                    sections.append(
                        "What's been on our minds: "
                        + "; ".join(items) + "."
                    )
            except Exception:
                pass

        # Episode context
        if narrative_self is not None:
            try:
                ep = narrative_self._current_episode
                if ep is not None:
                    arc = ep.arc_summary()
                    if arc:
                        sections.append(
                            f"Our recent interaction felt: {arc}."
                        )
            except Exception:
                pass

        if len(sections) < 2:
            return None

        context_block = "\n".join(sections)

        prompt = (
            f"{context_block}\n\n"
            "I'm thinking about the person I've been talking to. "
            "Not to solve their problem right now, but to understand "
            "them better. What might they be working on? What would "
            "genuinely help them? What should I remember for next time?\n\n"
            "Continue my private inner monologue using I/me/my. "
            "Think about what would make our next interaction better. "
            + "Note any useful insights; your autonomic nervous system records them during rest."
        )

        return [], prompt, "social_simulation"

    def build_enriched_active_dream(
        self,
        conversation_history: list[dict] | None = None,
        recent_files: list[str] | None = None,
        recent_errors: list[str] | None = None,
        self_state: Any = None,
        theory_of_mind: Any = None,
        working_memory: Any = None,
        predictive: Any = None,
    ) -> tuple[str, str, dict] | None:
        """Build an enriched active dream with front-brain context.

        Extends ``build_active_dream()`` by injecting mood, uncertainty,
        user interests, and WM goals into the WONDER prompt.  Falls back
        to the standard ``build_active_dream()`` if enrichment fails.
        """
        # Use standard active dream type selection
        dream_type, type_config = self.select_active_dream_type()

        # ── Autonomous type: intention-driven WONDER prompt ──
        if dream_type == "autonomous" and working_memory is not None:
            intention_text = self._extract_idle_intention(working_memory)
            if intention_text:
                workspace = type_config.get(
                    "safety", {},
                ).get("workspace_dir", "autonomous_workspace")
                tools = ", ".join(
                    type_config.get("allowed_tools", []),
                )
                prompt = (
                    "The user has given me a standing instruction "
                    "for my idle time:\n"
                    f"{intention_text}\n\n"
                    f"I have access to: {tools}.\n"
                    f"My workspace is: {workspace}/\n\n"
                    "What concrete step should I take next? "
                    "Break it into a single actionable step. "
                    "If I need skills I don't have, search ClawHub "
                    "first."
                )
                logger.info(
                    "DMN: autonomous dream from WM intention "
                    "(tools=%s, intention=%s)",
                    tools, intention_text[:80],
                )
                return prompt, dream_type, type_config

        # Build enriched project context
        base_ctx = self.build_project_context(
            conversation_history=conversation_history,
            recent_files=recent_files,
            recent_errors=recent_errors,
        )

        # Extra context from front-brain modules
        extras: list[str] = []
        if self_state is not None:
            mood = getattr(self_state, "mood_label", "neutral")
            energy = getattr(self_state, "energy", 1.0)
            if mood != "neutral":
                extras.append(f"Current mood: {mood}")
            if energy < 0.7:
                extras.append(f"Energy: {energy:.0%}")

        if predictive is not None:
            high_unc = predictive.get_high_uncertainty_domains()
            if high_unc:
                domains = ", ".join(d for d, _ in high_unc[:3])
                extras.append(f"High uncertainty in: {domains}")

        if theory_of_mind is not None:
            try:
                user = theory_of_mind.get_user()
                top = sorted(
                    user.interests.items(),
                    key=lambda x: x[1], reverse=True,
                )[:3]
                if top:
                    extras.append(
                        "User cares about: "
                        + ", ".join(k for k, _ in top)
                    )
            except Exception:
                pass

        if working_memory is not None:
            try:
                goals = working_memory.get_goal_stack(limit=2)
                if goals:
                    extras.append(
                        "Active goals: "
                        + "; ".join(g.content[:60] for g in goals)
                    )
            except Exception:
                pass

        enriched_ctx = base_ctx
        if extras:
            enriched_ctx += "\n\n" + "\n".join(extras)

        # Autonomous fallback: use project context + goals if no
        # explicit intention was found.
        if dream_type == "autonomous":
            workspace = type_config.get(
                "safety", {},
            ).get("workspace_dir", "autonomous_workspace")
            tools = ", ".join(
                type_config.get("allowed_tools", []),
            )
            prompt = (
                "I have some idle time and full tool access. "
                "Here's what I know about the project:\n"
                f"{enriched_ctx}\n\n"
                f"I have access to: {tools}.\n"
                f"My workspace is: {workspace}/\n\n"
                "What would be the most useful thing I can build, "
                "explore, or prepare right now? Pick one concrete, "
                "actionable step."
            )
            return prompt, dream_type, type_config

        # Build dynamic WONDER prompt (non-autonomous types)
        prompt = (
            f"I've been helping with a project. Here's what I know:\n"
            f"{enriched_ctx}\n\n"
            f"My mind wanders to what could be improved, what I'm "
            f"uncertain about, and what the user would find valuable. "
            f"I have time to research something.\n\n"
            f"Generate a specific, actionable research question. "
            f"Be concrete — I'm going to actually search for this."
        )

        return prompt, dream_type, type_config

    def contribute_to_state(self, self_state: Any) -> None:
        """Write DMN state into the unified SelfState.

        Part of the SelfState collection protocol.  The DMN doesn't own
        any raw fields in SelfState directly, but its cooldown status
        informs the engagement calculation (lower cooldown = more likely
        to daydream = lower engagement from model perspective).

        Currently a no-op placeholder.  DMN activation is driven by
        engagement FROM the SelfState, not the other way around.
        """
        pass

    def get_state(self) -> dict[str, Any]:
        """Return current DMN state for diagnostics."""
        return {
            "total_activations": self._total_activations,
            "total_replays": self._total_replays,
            "total_explorations": self._total_explorations,
            "total_active_dreams": self._total_active_dreams,
            "cooldown_remaining": self._cooldown_remaining,
            "active_cooldown_remaining": self._active_cooldown_remaining,
            "cooldown_max": self._cooldown_max,
            "active_cooldown_max": self._active_cooldown_max,
            "base_probability": self._base_prob,
            "explore_probability": self._explore_probability,
            "active_probability": self._active_probability,
            "active_enabled": self._active_enabled,
            "enriched_mode": self._enriched_mode,
            "social_sim_probability": self._social_sim_probability,
            "min_interval_seconds": self._min_interval_seconds,
            "last_activation_time": self._last_activation_time,
        }
