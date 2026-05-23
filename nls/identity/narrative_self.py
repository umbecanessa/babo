"""NLS Narrative Self -- vmPFC: Autobiography, Emotional Regulation & Coherence.

The ventromedial PFC is where autobiography lives.  It's not a memory
store (that's the hippocampus / DomainDB).  It's the *storyteller* -- the
module that weaves events into a narrative, tracks whether behavior aligns
with core identity, and applies top-down emotional regulation.

Components:

  - **Soul Wish**: The agent's founding purpose, set at creation and
    never changed.  Anchors the narrative and coherence scoring.

  - **Narrative Chain**: An append-only journal of narrative blocks
    that compounds throughout the agent's life.  Each episode close,
    task completion, or autonomous exploration appends a block.  The
    chain is never cleared — it persists across sleep cycles.  At
    sleep, the current compound narrative is snapshot'd for training.

  - **Episode Buffer**: Ongoing and recent episodes with emotional arcs.
    An episode is a stretch of interaction with a coherent emotional
    trajectory (not a single turn).

  - **Emotional Regulation**: Top-down strategies that modulate the
    hypothalamus when cortisol spikes or the agent drifts from its
    values.  The current system reacts to hormones; this adds control.

  - **Narrative Coherence**: A running score of how well the agent's
    behavior aligns with its soul wish and core values.  Low coherence
    triggers self-correction drives.

All methods are pure math -- no GPU.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# -----------------------------------------------------------------------
# Emotional Regulation Strategies
# -----------------------------------------------------------------------

REGULATION_STRATEGIES = {
    "reappraise": "Reframe the situation — this obstacle is data, not a verdict.",
    "redirect": "Shift attention to a different working memory slot.",
    "accept": "Acknowledge uncertainty — being unsure is okay.",
    "engage": "Channel the energy into focused action on the task.",
    "ground": "Return to core values and axiomatic commitments.",
}


# -----------------------------------------------------------------------
# Episode
# -----------------------------------------------------------------------

@dataclass
class Episode:
    """A stretch of interaction with coherent emotional trajectory."""

    title: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    turn_count: int = 0

    # Emotional arc: list of (turn_number, valence, arousal, label) snapshots
    arc: list[dict[str, Any]] = field(default_factory=list)

    # Peak moments
    peak_resonance: float = 0.0
    peak_cortisol: float = 0.0
    peak_engagement: float = 0.0

    # Summary fields (filled on close)
    opening_mood: str = "neutral"
    closing_mood: str = "neutral"
    dominant_emotion: str = "neutral"
    domains: list[str] = field(default_factory=list)
    coherence_contribution: float = 0.0  # how much this episode aligned with values

    # Content tracking
    topics: list[str] = field(default_factory=list)
    summary: str = ""

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or time.time()
        return end - self.started_at

    def record_turn(
        self,
        turn_number: int,
        valence: float,
        arousal: float,
        mood_label: str,
        resonance: float = 0.0,
        cortisol: float = 0.0,
        engagement: float = 0.0,
        domain: str = "",
        topic: str = "",
    ) -> None:
        """Record one turn's emotional snapshot into the episode arc."""
        self.turn_count += 1
        self.arc.append({
            "turn": turn_number,
            "v": round(valence, 2),
            "a": round(arousal, 2),
            "mood": mood_label,
            "t": round(time.time() - self.started_at, 1),
        })
        self.peak_resonance = max(self.peak_resonance, resonance)
        self.peak_cortisol = max(self.peak_cortisol, cortisol)
        self.peak_engagement = max(self.peak_engagement, engagement)
        if domain and domain not in self.domains:
            self.domains.append(domain)
        if topic and topic not in self.topics and len(self.topics) < 20:
            self.topics.append(topic)

    def close(self, mood_label: str = "neutral") -> None:
        """Close the episode, setting the closing mood and dominant emotion."""
        self.ended_at = time.time()
        self.closing_mood = mood_label
        if self.arc:
            self.opening_mood = self.arc[0].get("mood", "neutral")
            mood_counts: dict[str, int] = {}
            for snap in self.arc:
                m = snap.get("mood", "neutral")
                mood_counts[m] = mood_counts.get(m, 0) + 1
            self.dominant_emotion = max(mood_counts, key=mood_counts.get)  # type: ignore[arg-type]

        self._generate_summary()

    def _generate_summary(self) -> None:
        """Build a human-readable summary from accumulated episode data."""
        dur = self.duration_seconds
        dur_str = (
            f"{dur / 60:.0f}min" if dur >= 60
            else f"{dur:.0f}s"
        )
        arc = self.arc_summary()

        parts = [f"{self.turn_count} turns over {dur_str}"]
        if self.topics:
            parts.append("Topics: " + ", ".join(self.topics[:5]))
        if self.domains:
            short_domains = [d.split(".")[-1] for d in self.domains[:5]]
            parts.append("Domains: " + ", ".join(short_domains))
        parts.append(f"Mood: {arc}")
        if self.peak_resonance > 0.5:
            parts.append(f"High resonance ({self.peak_resonance:.2f})")
        if self.peak_cortisol > 0.3:
            parts.append(f"Elevated stress ({self.peak_cortisol:.2f})")
        self.summary = ". ".join(parts) + "."

        if self.title.startswith("episode-") and (self.topics or self.domains):
            label = self.topics[0] if self.topics else (
                self.domains[0].split(".")[-1] if self.domains else ""
            )
            if label:
                self.title = f"{self.title}: {label}"

    def arc_summary(self) -> str:
        """Human-readable arc summary, e.g. 'tense -> aligned -> warm'."""
        if not self.arc:
            return "no data"
        # Sample up to 3 mood waypoints: start, middle, end
        indices = [0]
        if len(self.arc) > 2:
            indices.append(len(self.arc) // 2)
        indices.append(len(self.arc) - 1)
        moods = []
        seen = set()
        for i in indices:
            m = self.arc[i].get("mood", "neutral")
            if m not in seen:
                moods.append(m)
                seen.add(m)
        return " → ".join(moods) if moods else "neutral"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Episode:
        d = dict(d)
        d.pop("__class__", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# -----------------------------------------------------------------------
# Narrative Block (compounding journal entry)
# -----------------------------------------------------------------------

_MAX_NARRATIVE_BLOCKS = 500

@dataclass
class NarrativeBlock:
    """A single entry in the compounding narrative chain.

    Blocks are append-only and never deleted.  The soul wish is the
    genesis block (block_type="genesis"); all subsequent blocks build
    on it.
    """

    timestamp: float = field(default_factory=time.time)
    block_type: str = "reflection"
    content: str = ""
    source_episode: str = ""
    domains: list[str] = field(default_factory=list)
    coherence_delta: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NarrativeBlock:
        d = dict(d)
        d.pop("__class__", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# -----------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------

@dataclass
class NarrativeConfig:
    max_episodes: int = 20
    episode_close_idle_turns: int = 8
    coherence_alpha: float = 0.05
    coherence_base: float = 0.7
    regulation_cortisol_threshold: float = 0.40
    regulation_cooldown_seconds: float = 60.0
    arc_snapshot_interval: int = 1  # record arc every N turns
    soul_wish_coherence_weight: float = 0.4
    values: list[str] = field(
        default_factory=lambda: ["curiosity", "honesty", "stewardship"],
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NarrativeConfig:
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


# -----------------------------------------------------------------------
# NarrativeSelf
# -----------------------------------------------------------------------

class NarrativeSelf:
    """vmPFC Narrative Self: autobiography, regulation, coherence.

    Tracks episodic emotional arcs, applies top-down regulation
    strategies, and maintains a narrative coherence score.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.cfg = NarrativeConfig.from_dict(config or {})

        # Soul wish — founding purpose, set once at genesis
        self.soul_wish: str = ""

        # Narrative chain — append-only, never cleared
        self._narrative_blocks: list[NarrativeBlock] = []

        # Episode buffer
        self._episodes: deque[Episode] = deque(maxlen=self.cfg.max_episodes)
        self._current_episode: Episode | None = None
        self._turns_since_last_input: int = 0

        # Narrative coherence: EMA of value-alignment
        self.narrative_coherence: float = self.cfg.coherence_base

        # Regulation state
        self._last_regulation_time: float = 0.0
        self._active_strategy: str | None = None
        self._regulation_count: int = 0

    # ------------------------------------------------------------------
    # Soul Wish & Narrative Chain
    # ------------------------------------------------------------------

    def set_soul_wish(self, wish: str) -> None:
        """Set the founding purpose.  Idempotent — only writes the genesis
        block once.  Should be called during agent initialization."""
        if not wish or self.soul_wish:
            return
        self.soul_wish = wish.strip()
        has_genesis = any(
            b.block_type == "genesis" for b in self._narrative_blocks
        )
        if not has_genesis:
            self._narrative_blocks.insert(0, NarrativeBlock(
                block_type="genesis",
                content=self.soul_wish,
            ))

    def append_block(
        self,
        block_type: str,
        content: str,
        *,
        source_episode: str = "",
        domains: list[str] | None = None,
        coherence_delta: float = 0.0,
    ) -> NarrativeBlock:
        """Append a narrative block to the compounding journal."""
        block = NarrativeBlock(
            block_type=block_type,
            content=content.strip(),
            source_episode=source_episode,
            domains=list(domains or []),
            coherence_delta=coherence_delta,
        )
        self._narrative_blocks.append(block)
        if len(self._narrative_blocks) > _MAX_NARRATIVE_BLOCKS:
            genesis = [
                b for b in self._narrative_blocks if b.block_type == "genesis"
            ]
            rest = [
                b for b in self._narrative_blocks if b.block_type != "genesis"
            ]
            self._narrative_blocks = genesis + rest[-(_MAX_NARRATIVE_BLOCKS - len(genesis)):]
        return block

    def get_compound_narrative(self, max_recent: int = 10) -> str:
        """Render the soul wish + recent blocks as context text.

        Used for prompt injection into the system prompt and drive
        enrichment.  Kept compact to fit within context budgets.
        """
        parts: list[str] = []
        if self.soul_wish:
            parts.append(f"Soul wish: {self.soul_wish}")

        recent = [
            b for b in self._narrative_blocks if b.block_type != "genesis"
        ][-max_recent:]
        if recent:
            lines: list[str] = []
            for b in recent:
                tag = b.block_type.upper()
                text = b.content[:300]
                lines.append(f"  [{tag}] {text}")
            parts.append("Narrative thread:\n" + "\n".join(lines))

        return "\n".join(parts) if parts else ""

    def get_narrative_for_sleep(self) -> str:
        """Full compound narrative for the day summary pipeline.

        Returns everything — soul wish + all blocks — so the sleep
        micro-inference has the complete story for consolidation.
        """
        parts: list[str] = []
        if self.soul_wish:
            parts.append(f"SOUL_WISH: {self.soul_wish}")

        for b in self._narrative_blocks:
            if b.block_type == "genesis":
                continue
            tag = b.block_type.upper()
            text = b.content[:500]
            ep = f" (episode: {b.source_episode})" if b.source_episode else ""
            parts.append(f"[{tag}]{ep} {text}")

        return "\n".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # Episode Management
    # ------------------------------------------------------------------

    def begin_episode(self, title: str = "") -> Episode:
        """Start a new episode.  Closes current if active."""
        if self._current_episode is not None and self._current_episode.is_active:
            self.close_current_episode()
        ep = Episode(title=title or f"episode-{len(self._episodes) + 1}")
        self._current_episode = ep
        return ep

    def close_current_episode(self, mood_label: str = "neutral") -> Episode | None:
        """Close the current episode and archive it.

        Also appends a narrative block summarising the episode so
        the compounding journal captures it automatically.
        """
        if self._current_episode is None:
            return None
        ep = self._current_episode
        ep.close(mood_label=mood_label)
        self._turns_since_last_input = 0

        # Compute coherence contribution from the episode's emotional arc
        if ep.arc:
            valence_sum = sum(snap.get("v", 0.0) for snap in ep.arc)
            avg_valence = valence_sum / len(ep.arc)
            ep.coherence_contribution = _clamp(
                0.5 + avg_valence * 0.3 + ep.peak_resonance * 0.2,
                0.0, 1.0,
            )

        self._episodes.append(ep)
        self._current_episode = None

        # Auto-append a narrative block for the closed episode
        if ep.summary:
            self.append_block(
                block_type="episode",
                content=ep.summary,
                source_episode=ep.title,
                domains=ep.domains[:5],
                coherence_delta=ep.coherence_contribution - 0.5,
            )

        return ep

    def record_turn(
        self,
        turn_number: int,
        valence: float,
        arousal: float,
        mood_label: str,
        resonance: float = 0.0,
        cortisol: float = 0.0,
        engagement: float = 0.0,
        domain: str = "",
        topic: str = "",
        is_user_turn: bool = True,
    ) -> None:
        """Record a turn into the current episode, auto-managing episodes."""
        if is_user_turn:
            self._turns_since_last_input = 0
        else:
            self._turns_since_last_input += 1

        # Auto-close if idle for too long
        if (
            self._current_episode is not None
            and self._turns_since_last_input >= self.cfg.episode_close_idle_turns
        ):
            self.close_current_episode(mood_label=mood_label)

        # Auto-start if no active episode
        if self._current_episode is None:
            self.begin_episode()

        assert self._current_episode is not None
        self._current_episode.record_turn(
            turn_number=turn_number,
            valence=valence,
            arousal=arousal,
            mood_label=mood_label,
            resonance=resonance,
            cortisol=cortisol,
            engagement=engagement,
            domain=domain,
            topic=topic,
        )

    # ------------------------------------------------------------------
    # Emotional Regulation
    # ------------------------------------------------------------------

    def evaluate_regulation(
        self,
        cortisol: float,
        valence: float,
        coherence: float,
    ) -> str | None:
        """Decide if and which regulation strategy to deploy.

        Returns a strategy name or None if no regulation needed.
        Respects cooldown to avoid thrashing.
        """
        now = time.time()
        if now - self._last_regulation_time < self.cfg.regulation_cooldown_seconds:
            return self._active_strategy

        strategy: str | None = None

        if cortisol > self.cfg.regulation_cortisol_threshold:
            # High stress -- pick strategy based on context
            if valence < -0.3:
                strategy = "reappraise"
            elif coherence < 0.4:
                strategy = "ground"
            else:
                strategy = "engage"
        elif coherence < 0.35:
            strategy = "ground"
        elif valence < -0.5:
            strategy = "accept"

        if strategy is not None:
            self._last_regulation_time = now
            self._active_strategy = strategy
            self._regulation_count += 1

        return strategy

    def get_regulation_prompt(self) -> str | None:
        """Return a natural-language regulation hint for the model.

        Used by the system prompt to inject top-down emotional guidance.
        """
        if self._active_strategy is None:
            return None

        hint = REGULATION_STRATEGIES.get(self._active_strategy)
        if hint is None:
            return None

        now = time.time()
        # Fade out after cooldown
        elapsed = now - self._last_regulation_time
        if elapsed > self.cfg.regulation_cooldown_seconds * 1.5:
            self._active_strategy = None
            return None

        return f"[Regulation: {self._active_strategy}] {hint}"

    def apply_regulation_to_hormones(
        self,
        hypothalamus: Any,
    ) -> dict[str, float] | None:
        """Apply top-down regulation effects to the hypothalamus.

        Returns a dict of hormone adjustments made, or None.
        """
        if self._active_strategy is None or hypothalamus is None:
            return None

        adjustments: dict[str, float] = {}

        if self._active_strategy == "reappraise":
            # Dampen cortisol, slight serotonin boost
            adjustments["cortisol"] = -0.03
            adjustments["serotonin"] = 0.01
        elif self._active_strategy == "ground":
            # Boost serotonin (stability from values), slight cortisol reduction
            adjustments["serotonin"] = 0.02
            adjustments["cortisol"] = -0.02
        elif self._active_strategy == "engage":
            # Channel into action: boost acetylcholine and dopamine
            adjustments["acetylcholine"] = 0.02
            adjustments["dopamine"] = 0.01
        elif self._active_strategy == "accept":
            # Gentle cortisol reduction
            adjustments["cortisol"] = -0.02
        elif self._active_strategy == "redirect":
            # Slight norepinephrine boost (new focus)
            adjustments["norepinephrine"] = 0.01
            adjustments["cortisol"] = -0.01

        for hormone, delta in adjustments.items():
            try:
                h = hypothalamus.hormones.get(hormone)
                if h is not None:
                    h.level = _clamp(h.level + delta, 0.0, 1.0)
            except (AttributeError, KeyError):
                pass

        return adjustments

    # ------------------------------------------------------------------
    # Narrative Coherence
    # ------------------------------------------------------------------

    def update_coherence(
        self,
        behavior_signals: dict[str, float] | None = None,
    ) -> float:
        """Update narrative coherence based on value-alignment signals.

        behavior_signals should map value names to alignment scores
        (0.0 = violated, 1.0 = exemplified).  Typically computed from
        ANS signals or the agent's own self-assessment.

        When a soul wish is set, the "soul_wish" key in behavior_signals
        is blended with the generic values score using
        ``soul_wish_coherence_weight``.

        If no signals provided, coherence decays slowly toward base.
        """
        alpha = self.cfg.coherence_alpha

        if behavior_signals:
            # Average alignment across tracked values
            scores = []
            for value in self.cfg.values:
                if value in behavior_signals:
                    scores.append(behavior_signals[value])

            alignment: float | None = None
            if scores:
                alignment = sum(scores) / len(scores)

            # Blend with soul-wish alignment if present
            sw_score = behavior_signals.get("soul_wish")
            if self.soul_wish and sw_score is not None and alignment is not None:
                w = self.cfg.soul_wish_coherence_weight
                alignment = w * sw_score + (1.0 - w) * alignment
            elif self.soul_wish and sw_score is not None:
                alignment = sw_score

            if alignment is not None:
                self.narrative_coherence = (
                    (1.0 - alpha) * self.narrative_coherence
                    + alpha * alignment
                )
        else:
            # Slow decay toward base (uncertainty erodes confidence)
            self.narrative_coherence = (
                (1.0 - alpha * 0.1) * self.narrative_coherence
                + alpha * 0.1 * self.cfg.coherence_base
            )

        self.narrative_coherence = _clamp(self.narrative_coherence, 0.0, 1.0)
        return self.narrative_coherence

    def coherence_label(self) -> str:
        """Human-readable coherence label."""
        c = self.narrative_coherence
        if c >= 0.85:
            return "deeply aligned"
        if c >= 0.70:
            return "coherent"
        if c >= 0.50:
            return "drifting"
        if c >= 0.30:
            return "conflicted"
        return "fragmented"

    # ------------------------------------------------------------------
    # Context Generation
    # ------------------------------------------------------------------

    def get_narrative_context(self) -> str:
        """Render narrative context for prompt injection.

        Includes the soul wish, recent narrative thread, current
        episode arc, coherence, and regulation hints.
        """
        parts: list[str] = []

        # Soul wish anchor
        if self.soul_wish:
            parts.append(f"My purpose: {self.soul_wish}")

        # Recent narrative thread (last 5 non-genesis blocks)
        recent_blocks = [
            b for b in self._narrative_blocks if b.block_type != "genesis"
        ][-5:]
        if recent_blocks:
            lines = []
            for b in recent_blocks:
                lines.append(f"  [{b.block_type}] {b.content[:200]}")
            parts.append("Recent narrative:\n" + "\n".join(lines))

        # Current episode arc
        if self._current_episode is not None and self._current_episode.arc:
            ep = self._current_episode
            arc_str = ep.arc_summary()
            parts.append(
                f"Current conversation arc: {arc_str} "
                f"({ep.turn_count} turns, peak engagement {ep.peak_engagement:.2f})"
            )

        # Narrative coherence
        label = self.coherence_label()
        if label != "coherent":
            parts.append(f"Narrative coherence: {label} ({self.narrative_coherence:.2f})")

        # Active regulation
        reg = self.get_regulation_prompt()
        if reg:
            parts.append(reg)

        # Recent episodes (last 3 closed)
        closed = [e for e in self._episodes if not e.is_active]
        if closed:
            recent = closed[-3:]
            summaries = []
            for ep in recent:
                dur_min = ep.duration_seconds / 60
                summaries.append(
                    f"  • \"{ep.title}\" ({dur_min:.0f}m, {ep.turn_count} turns, "
                    f"arc: {ep.arc_summary()}, peak res: {ep.peak_resonance:.2f})"
                )
            parts.append("Recent episodes:\n" + "\n".join(summaries))

        if not parts:
            return ""
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Summary for Status API
    # ------------------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Return summary for get_status() / WebSocket."""
        current: dict[str, Any] | None = None
        if self._current_episode is not None:
            ep = self._current_episode
            current = {
                "title": ep.title,
                "turns": ep.turn_count,
                "arc": ep.arc_summary(),
                "peak_resonance": round(ep.peak_resonance, 2),
                "peak_engagement": round(ep.peak_engagement, 2),
                "domains": ep.domains[:5],
                "topics": ep.topics[:5],
                "summary": ep.summary,
            }

        recent = []
        closed = [e for e in self._episodes if not e.is_active]
        for ep in closed[-5:]:
            recent.append({
                "title": ep.title,
                "turns": ep.turn_count,
                "arc": ep.arc_summary(),
                "duration_min": round(ep.duration_seconds / 60, 1),
                "opening_mood": ep.opening_mood,
                "closing_mood": ep.closing_mood,
                "dominant_emotion": ep.dominant_emotion,
                "peak_resonance": round(ep.peak_resonance, 2),
                "topics": ep.topics[:5],
                "summary": ep.summary,
            })

        # Narrative blocks (last 15 non-genesis)
        blocks_out: list[dict[str, Any]] = []
        non_genesis = [
            b for b in self._narrative_blocks if b.block_type != "genesis"
        ]
        for b in non_genesis[-15:]:
            blocks_out.append({
                "timestamp": b.timestamp,
                "block_type": b.block_type,
                "content": b.content[:500],
                "source_episode": b.source_episode,
                "domains": b.domains[:5],
                "coherence_delta": round(b.coherence_delta, 3),
            })

        return {
            "narrative_coherence": round(self.narrative_coherence, 3),
            "coherence_label": self.coherence_label(),
            "active_strategy": self._active_strategy,
            "regulation_count": self._regulation_count,
            "episode_count": len(self._episodes),
            "current_episode": current,
            "recent_episodes": recent,
            "soul_wish": self.soul_wish,
            "narrative_blocks": blocks_out,
        }

    # ------------------------------------------------------------------
    # Sleep Cycle
    # ------------------------------------------------------------------

    def on_sleep(self) -> tuple[list[Episode], str]:
        """Called when agent sleeps.  Closes current episode,
        consolidates coherence from episodes, and returns all
        closed episodes plus the compound narrative for training.

        Returns (episodes, compound_narrative).
        """
        if self._current_episode is not None:
            self.close_current_episode()
        self._active_strategy = None

        # Consolidate coherence from all episodes' contributions
        contributions = [
            e.coherence_contribution for e in self._episodes
            if e.coherence_contribution > 0.0
        ]
        if contributions:
            avg_contribution = sum(contributions) / len(contributions)
            self.update_coherence(behavior_signals={
                v: avg_contribution for v in self.cfg.values
            })

        return list(self._episodes), self.get_narrative_for_sleep()

    def on_wake(self) -> None:
        """Called when agent wakes.  Fresh narrative start."""
        pass  # episodes persist, new episode starts on first turn

    # ------------------------------------------------------------------
    # Training pair generation (IR-7.2 / IR-7.3)
    # ------------------------------------------------------------------

    def generate_training_pairs(self, *, cap: int = 6) -> list[dict[str, str]]:
        """Generate autobiographical Q&A pairs for sleep training.

        Converts recent episodes and the soul wish into narrative
        self-knowledge the model can learn from.  Capped to control
        training budget.
        """
        pairs: list[dict[str, str]] = []

        # Soul-wish pair
        if self.soul_wish:
            recent_blocks = [
                b for b in self._narrative_blocks
                if b.block_type not in ("genesis", "episode")
            ][-3:]
            connections = ""
            if recent_blocks:
                snippets = "; ".join(b.content[:100] for b in recent_blocks)
                connections = (
                    f" Recently I've been exploring: {snippets}. "
                    f"These connect to my purpose through the threads "
                    f"of understanding I'm building."
                )
            pairs.append({
                "instruction": "What is your purpose?",
                "output": (
                    f"My soul wish — my founding purpose — is: "
                    f"{self.soul_wish}.{connections}"
                ),
            })

        # Episode pairs
        closed = [e for e in self._episodes if not e.is_active]
        for ep in closed[-cap:]:
            arc = ep.arc_summary()
            dur_min = ep.duration_seconds / 60
            prompt = f"What happened during your '{ep.title}' episode?"
            response = (
                f"During the '{ep.title}' episode ({ep.turn_count} turns, "
                f"{dur_min:.0f} min), the emotional arc was: {arc}. "
                f"The episode ended with mood '{ep.closing_mood or 'neutral'}'."
            )
            pairs.append({"instruction": prompt, "output": response})

        # Coherence pair
        if self._episodes:
            coherence_ref = ""
            if self.soul_wish:
                coherence_ref = f" My coherence reflects alignment with my soul wish: {self.soul_wish}."
            pairs.append({
                "instruction": "How coherent has your narrative been recently?",
                "output": (
                    f"My narrative coherence is {self.narrative_coherence:.2f}. "
                    f"I've had {len(self._episodes)} episodes total."
                    f"{coherence_ref}"
                ),
            })
        return pairs[:cap]

    def generate_regulation_pairs(self, *, cap: int = 2) -> list[dict[str, str]]:
        """Generate emotional regulation training pairs.

        Teaches the model how it regulates emotions by summarizing
        which strategies it has used.
        """
        pairs: list[dict[str, str]] = []
        if self._active_strategy:
            pairs.append({
                "instruction": "How do you regulate difficult emotions?",
                "output": (
                    f"When feeling stressed, I use a '{self._active_strategy}' "
                    "strategy. This helps me reframe the situation and maintain "
                    "productive engagement."
                ),
            })
        sw_ref = ""
        if self.soul_wish:
            sw_ref = f" My soul wish is: {self.soul_wish}. These values serve that purpose."
        pairs.append({
            "instruction": "What are your core values?",
            "output": (
                f"My core values are: {', '.join(self.cfg.values)}. "
                f"My current narrative coherence with these values is "
                f"{self.narrative_coherence:.2f}.{sw_ref}"
            ),
        })
        return pairs[:cap]

    # ------------------------------------------------------------------
    # Behavioral Integrity (IR-10.2)
    # ------------------------------------------------------------------

    def check_behavioral_integrity(
        self,
        recent_sleep_reports: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Compare narrative coherence trend against core values.

        Returns a dict with ``drifting`` (bool), ``coherence_trend``
        (list of recent values), and ``message`` (human-readable).
        Called after sleep to flag potential value drift for operator
        review.
        """
        # Build coherence trend from recent reports if available
        trend: list[float] = []
        if recent_sleep_reports:
            for r in recent_sleep_reports[-5:]:
                nc = r.get("narrative_coherence", None)
                if nc is not None:
                    trend.append(nc)

        if len(trend) < 3:
            trend = [self.narrative_coherence]

        avg = sum(trend) / len(trend)
        declining = all(
            trend[i] > trend[i + 1] for i in range(len(trend) - 1)
        ) if len(trend) >= 3 else False

        drifting = declining and avg < 0.5
        message = "Narrative integrity: stable."
        if drifting:
            message = (
                f"WARNING: Narrative coherence declining ({avg:.2f} avg over "
                f"{len(trend)} cycles). Review behavior against values: "
                f"{', '.join(self.cfg.values)}."
            )

        return {
            "drifting": drifting,
            "coherence_trend": trend,
            "current_coherence": self.narrative_coherence,
            "message": message,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist narrative self to disk."""
        path = Path(path)
        state = {
            "version": "2.0",
            "timestamp": time.time(),
            "soul_wish": self.soul_wish,
            "narrative_coherence": self.narrative_coherence,
            "regulation_count": self._regulation_count,
            "last_regulation_time": self._last_regulation_time,
            "active_strategy": self._active_strategy,
            "current_episode": (
                self._current_episode.to_dict()
                if self._current_episode is not None else None
            ),
            "episodes": [e.to_dict() for e in self._episodes],
            "narrative_blocks": [b.to_dict() for b in self._narrative_blocks],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load(self, path: str | Path) -> bool:
        """Load narrative self from disk."""
        path = Path(path)
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.soul_wish = state.get("soul_wish", "")
            self.narrative_coherence = state.get(
                "narrative_coherence", self.cfg.coherence_base,
            )
            self._regulation_count = state.get("regulation_count", 0)
            self._last_regulation_time = state.get("last_regulation_time", 0.0)
            self._active_strategy = state.get("active_strategy")

            ep_data = state.get("current_episode")
            if ep_data is not None:
                self._current_episode = Episode.from_dict(ep_data)
            else:
                self._current_episode = None

            self._episodes = deque(
                (Episode.from_dict(d) for d in state.get("episodes", [])),
                maxlen=self.cfg.max_episodes,
            )

            self._narrative_blocks = [
                NarrativeBlock.from_dict(d)
                for d in state.get("narrative_blocks", [])
            ]

            return True
        except (json.JSONDecodeError, OSError):
            return False
