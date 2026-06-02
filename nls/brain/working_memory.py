"""NLS Working Memory -- Active Slots, Not a Flat Buffer.

The dorsolateral PFC maintains and manipulates information in working
memory.  It's not a passive buffer but an active workspace with limited
capacity, attention-weighted selection, and goal management.

Components:

  - **Slots**: Limited set of typed items (fact, goal, constraint,
    feeling, intention, user_state, prediction).  When at capacity,
    lowest-salience items are evicted.

  - **Goal Stack**: Strategic / tactical / immediate goals at multiple
    abstraction levels.  Enables planning across turns and sessions.

  - **Prospective Memory**: Deferred intentions with trigger conditions.
    "When the user mentions deployment, suggest Docker."

  - **Attention Spotlight**: Not all slots get equal weight.  High-arousal
    items are foregrounded.  Relevance to current input boosts priority.
    Salience-weighted selection picks which slots enter the prompt.

All methods are pure math -- no GPU.
"""

from __future__ import annotations

import collections
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON data to *path* atomically (temp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=path.stem,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -----------------------------------------------------------------------
# Slot Types
# -----------------------------------------------------------------------

SLOT_TYPES = {
    "fact",          # relevant piece of knowledge
    "goal",          # something the agent is trying to achieve
    "constraint",    # a rule or boundary to respect
    "feeling",       # an emotional context that matters right now
    "intention",     # a deferred action (prospective memory)
    "user_state",    # model of what the user is thinking/feeling
    "prediction",    # what the agent expects to happen next
    "schema",        # a primed reasoning pattern
    "perception",    # visual/sensory observation (decays 3x faster)
    "instruction",   # task directive (no decay, cleared post-reflect)
    "credential",    # project infrastructure credential (connection string, deploy URL, etc.)
}

FAST_DECAY_TYPES = {"perception"}
NO_DECAY_TYPES = {"instruction", "credential"}

GOAL_LEVELS = ("strategic", "tactical", "immediate")

CONSOLIDATION_DOMAINS = (
    "Consolidation.SessionProgress",
    "Consolidation.ActiveKnowledge",
    "Consolidation.TaskContext",
    "Consolidation.OrchestrationPatterns",
    "Consolidation.DayNarrative",
)

_MAX_SLOT_CONTENT = 600
_MAX_CONSOLIDATION_CHARS = 4800


_SIGNAL_TAG_RE = re.compile(
    r"\[(?:EVALUATE|LEARN|PLAN|IDENTITY|REFLECT|BOND)[:\|][^\]]*\]\s*",
)


def _strip_signal_tags(text: str) -> str:
    """Remove ANS signal tags like [EVALUATE:correct] from text."""
    return _SIGNAL_TAG_RE.sub("", text).strip()


def _is_consolidation_slot(slot: "WMSlot") -> bool:
    """Return True if the slot belongs to the protected consolidation tier."""
    return slot.domain in CONSOLIDATION_DOMAINS


_CREDENTIAL_DOMAIN_RE = re.compile(
    r"(?i)(^|\.)credential[s]?(\.|$)"
)


def _slot_is_credential_by_domain(slot: "WMSlot") -> bool:
    """True if the slot's domain signals credential content, even when
    slot_type is 'fact'.  Catches ANS-generated duplicates like
    ``Project.Credential.GitHub`` that bypass the slot_type filter."""
    if not slot.domain:
        return False
    return bool(_CREDENTIAL_DOMAIN_RE.search(slot.domain))


_WM_RELATIONSHIP_KEYWORDS = frozenset({
    "api", "endpoint", "assemblyai", "anthropic", "openai", "resend",
    "stripe", "webhook", "deploy", "railway", "vercel", "docker",
    "heroku", "aws", "database", "postgres", "redis", "prisma", "mongo",
    "email", "notification", "client", "customer", "coach", "mentor",
    "friend", "family", "partner", "colleague",
})
_WM_IDENTITY_KEYWORDS = frozenset({
    "name", "called", "i am", "my name", "babo", "agent", "identity",
    "account", "username", "login", "user", "umberto", "mauro",
})


def _classify_wm_pair_domain(slot: "WMSlot") -> str:
    """Route a WM slot to the right expert domain for training."""
    text = ((slot.domain or "") + " " + (slot.content or "")[:200]).lower()
    id_score = sum(1 for kw in _WM_IDENTITY_KEYWORDS if kw in text)
    rel_score = sum(1 for kw in _WM_RELATIONSHIP_KEYWORDS if kw in text)
    if id_score > rel_score and id_score > 0:
        return "identity"
    if rel_score > id_score and rel_score > 0:
        return "relationships"
    return "preferences"


def _extract_tag_content(line: str) -> str:
    """Extract content after a ``[Tag]`` bracket or ``Tag:`` colon prefix."""
    stripped = line.strip()
    if stripped.startswith("[") and "]" in stripped:
        return stripped.split("]", 1)[-1].strip()
    if ":" in stripped:
        return stripped.split(":", 1)[-1].strip()
    return ""


# -----------------------------------------------------------------------
# WMSlot
# -----------------------------------------------------------------------

ACCESS_GENESIS = "genesis"
ACCESS_SYSTEM = "system"
ACCESS_MALLEABLE = "malleable"
ACCESS_SESSION = "session"
ACCESS_TIERS = frozenset({ACCESS_GENESIS, ACCESS_SYSTEM, ACCESS_MALLEABLE, ACCESS_SESSION})


@dataclass
class WMSlot:
    """A single working memory slot."""

    slot_type: str              # One of SLOT_TYPES
    content: str                # Human-readable content
    salience: float = 0.8       # 0.0 (forgotten) to 1.0 (spotlight)
    created_at: float = field(default_factory=time.time)
    source: str = "system"      # "user", "drive", "dmn", "narrative", "prediction", "ans"
    domain: str = ""            # Optional domain path
    trigger: str = ""           # For intentions: condition that activates
    level: str = ""             # For goals: "strategic", "tactical", "immediate"
    metadata: dict[str, Any] = field(default_factory=dict)
    access: str = ACCESS_MALLEABLE  # "genesis" | "system" | "malleable" | "session"

    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WMSlot:
        d = dict(d)
        d.pop("__class__", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# -----------------------------------------------------------------------
# Orchestration State Types
# -----------------------------------------------------------------------

@dataclass
class OrchMemberState:
    """Snapshot of a single team member for WM orchestration tracking."""
    index: int
    task_summary: str
    status: str = "pending"
    delegate_number: int | None = None
    iterations_used: int = 0
    interventions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrchMemberState:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class OrchTeamState:
    """Snapshot of a team for WM orchestration tracking."""
    team_id: str
    plan_id: str
    status: str = "running"
    members: list[OrchMemberState] = field(default_factory=list)
    launched_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["members"] = [m.to_dict() for m in self.members]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrchTeamState:
        members = [OrchMemberState.from_dict(m) for m in d.get("members", [])]
        base = {k: v for k, v in d.items()
                if k in cls.__dataclass_fields__ and k != "members"}
        return cls(members=members, **base)


@dataclass
class OrchDecision:
    """A single orchestration decision with outcome for training."""
    timestamp: float = field(default_factory=time.time)
    action: str = ""
    context: str = ""
    outcome: str = ""
    team_id: str = ""
    member_idx: int = -1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrchDecision:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# -----------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------

@dataclass
class WorkingMemoryConfig:
    max_slots: int = 9
    max_goals: int = 5
    max_prospective: int = 10
    max_instructions: int = 8
    salience_decay_rate: float = 0.005
    attention_window_size: int = 5
    intention_check_method: str = "keyword"
    min_salience_evict: float = 0.05
    consolidation_slot_count: int = 3

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkingMemoryConfig:
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


# -----------------------------------------------------------------------
# WorkingMemory
# -----------------------------------------------------------------------

class WorkingMemory:
    """Slot-based working memory with goals and prospective memory.

    Maintains a limited set of actively held items, a goal stack,
    and a list of deferred intentions.  All methods are pure math.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.cfg = WorkingMemoryConfig.from_dict(config or {})
        self._slots: list[WMSlot] = []
        self._goal_stack: list[WMSlot] = []
        self._prospective: list[WMSlot] = []
        self._instructions: list[WMSlot] = []
        self._plan_position: str = ""
        self._todo_board: str = ""
        # Orchestration state
        self._orch_teams: dict[str, OrchTeamState] = {}
        self._coordinator_phase: str = "idle"
        self._coordinator_phase_detail: str = ""
        self._orch_decisions: collections.deque[OrchDecision] = collections.deque(maxlen=8)
        self._orch_escalations: list[OrchDecision] = []

    # ------------------------------------------------------------------
    # Slot Management
    # ------------------------------------------------------------------

    def add(self, slot: WMSlot) -> WMSlot | None:
        """Add a slot, evicting lowest-salience if at capacity.

        Consolidation slots are protected from eviction.
        Returns the evicted slot if one was removed, else None.
        """
        evicted = None
        if len(self._slots) >= self.cfg.max_slots:
            evictable = [s for s in self._slots if not _is_consolidation_slot(s)]
            if evictable:
                weakest = min(evictable, key=lambda s: s.salience)
                if weakest.salience < slot.salience:
                    self._slots.remove(weakest)
                    evicted = weakest
                else:
                    return None
            else:
                return None
        self._slots.append(slot)
        return evicted

    def add_fact(
        self, content: str, domain: str = "", source: str = "ans",
        salience: float = 0.8, metadata: dict[str, Any] | None = None,
    ) -> None:
        """Convenience: add a fact slot."""
        if domain not in CONSOLIDATION_DOMAINS and len(content) > _MAX_SLOT_CONTENT:
            content = content[:_MAX_SLOT_CONTENT].rstrip()
            last_sep = max(content.rfind(". "), content.rfind("\n"))
            if last_sep > _MAX_SLOT_CONTENT * 0.7:
                content = content[:last_sep + 1]
        self.add(WMSlot(
            slot_type="fact", content=content, domain=domain,
            source=source, salience=salience,
            metadata=metadata or {},
        ))

    def upsert_fact(
        self, domain: str, content: str, source: str = "tool",
        salience: float = 0.9,
    ) -> None:
        """Insert or replace a fact slot by domain key.

        If a fact with the same ``domain`` already exists, its content
        and timestamp are updated in place (no eviction dance).
        Otherwise a new slot is added normally.
        """
        if domain not in CONSOLIDATION_DOMAINS and len(content) > _MAX_SLOT_CONTENT:
            content = content[:_MAX_SLOT_CONTENT].rstrip()
            last_sep = max(content.rfind(". "), content.rfind("\n"))
            if last_sep > _MAX_SLOT_CONTENT * 0.7:
                content = content[:last_sep + 1]
        for slot in self._slots:
            if slot.slot_type == "fact" and slot.domain == domain:
                slot.content = content
                slot.salience = max(slot.salience, salience)
                slot.created_at = time.time()
                return
        self.add_fact(content=content, domain=domain,
                      source=source, salience=salience)

    def upsert_perception(
        self, domain: str, content: str, salience: float = 0.7,
    ) -> None:
        """Insert or replace a perception (visual) slot by domain key.

        Perception slots decay 3x faster than facts (ephemeral sensory data).
        Only the latest observation for a given domain occupies a slot.
        """
        for slot in self._slots:
            if slot.slot_type == "perception" and slot.domain == domain:
                slot.content = content
                slot.salience = max(slot.salience, salience)
                slot.created_at = time.time()
                return
        self.add(WMSlot(
            slot_type="perception", content=content, domain=domain,
            source="visual", salience=salience,
        ))

    def upsert_credential(
        self, domain: str, content: str, source: str = "user",
        salience: float = 1.0,
    ) -> None:
        """Insert or replace a project credential (connection string, URL, etc.).

        Credentials never decay and survive sleep cycles.  They are stored
        with ``persistent=True`` so they are kept across sessions.
        The domain should use the ``Project.Credential.*`` prefix.
        """
        if not domain.startswith("Project.Credential"):
            domain = f"Project.Credential.{domain}"
        for slot in self._slots:
            if slot.slot_type == "credential" and slot.domain == domain:
                slot.content = content
                slot.salience = salience
                slot.created_at = time.time()
                return
        self.add(WMSlot(
            slot_type="credential", content=content, domain=domain,
            source=source, salience=salience,
            metadata={"persistent": True},
        ))

    def get_credentials(self) -> list[WMSlot]:
        """Return all credential slots."""
        return [s for s in self._slots if s.slot_type == "credential"]

    def add_feeling(self, content: str, salience: float = 0.7) -> None:
        """Convenience: add a feeling slot."""
        self.add(WMSlot(
            slot_type="feeling", content=content, salience=salience,
        ))

    def add_user_state(self, content: str, salience: float = 0.6) -> None:
        """Convenience: add a user state observation."""
        existing = [s for s in self._slots if s.slot_type == "user_state"]
        if existing:
            existing[0].content = content
            existing[0].salience = max(existing[0].salience, salience)
            existing[0].created_at = time.time()
        else:
            self.add(WMSlot(
                slot_type="user_state", content=content, salience=salience,
            ))

    def remove_by_domain(self, domain: str) -> int:
        """Remove all slots matching a domain. Returns count removed."""
        before = len(self._slots)
        self._slots = [s for s in self._slots if s.domain != domain]
        return before - len(self._slots)

    def remove_by_metadata(self, key: str, value: Any) -> int:
        """Remove all slots whose metadata[key] == value. Returns count."""
        before = len(self._slots)
        self._slots = [
            s for s in self._slots
            if s.metadata.get(key) != value
        ]
        return before - len(self._slots)

    # ------------------------------------------------------------------
    # Instructions (task-scoped, no decay)
    # ------------------------------------------------------------------

    def add_instruction(
        self, content: str, source: str = "task", salience: float = 1.0,
    ) -> None:
        """Add a task instruction. Evicts oldest if at capacity."""
        if len(self._instructions) >= self.cfg.max_instructions:
            self._instructions.pop(0)
        self._instructions.append(WMSlot(
            slot_type="instruction", content=content,
            source=source, salience=salience,
        ))

    def get_instructions(self) -> list[WMSlot]:
        """Return all active instruction slots."""
        return list(self._instructions)

    def clear_instructions(self) -> None:
        """Wipe all instructions (post-reflect cleanup)."""
        self._instructions.clear()

    def update_instruction(self, index: int, content: str) -> bool:
        """Update instruction at index. Returns True on success."""
        if 0 <= index < len(self._instructions):
            self._instructions[index].content = content
            self._instructions[index].created_at = time.time()
            return True
        return False

    def delete_instruction(self, index: int) -> bool:
        """Delete instruction at index. Returns True on success."""
        if 0 <= index < len(self._instructions):
            self._instructions.pop(index)
            return True
        return False

    # ------------------------------------------------------------------
    # Plan Position (sliding window)
    # ------------------------------------------------------------------

    def set_plan_position(self, position: str) -> None:
        """Store the current plan position string (updated each iteration)."""
        self._plan_position = position

    def get_plan_position(self) -> str:
        """Return the current plan position string."""
        return self._plan_position

    # ------------------------------------------------------------------
    # Todo Board Snapshot
    # ------------------------------------------------------------------

    def set_todo_board(self, board: str) -> None:
        """Store a compact snapshot of the todo board (refreshed each turn)."""
        self._todo_board = board

    def get_todo_board(self) -> str:
        return self._todo_board

    # ------------------------------------------------------------------
    # Orchestration State
    # ------------------------------------------------------------------

    def orch_update_team(
        self,
        team_id: str,
        plan_id: str = "",
        status: str = "running",
        members: list[dict[str, Any]] | None = None,
    ) -> None:
        """Upsert a team's state in the orchestration roster."""
        existing = self._orch_teams.get(team_id)
        if existing:
            existing.status = status
            if members is not None:
                existing.members = [
                    OrchMemberState(
                        index=m.get("index", i),
                        task_summary=m.get("task_summary", "")[:80],
                        status=m.get("status", "pending"),
                        delegate_number=m.get("delegate_number"),
                        iterations_used=m.get("iterations_used", 0),
                        interventions=m.get("interventions", 0),
                    )
                    for i, m in enumerate(members)
                ]
            if status in ("completed", "partial", "failed"):
                existing.completed_at = time.time()
        else:
            orch_members = []
            if members:
                orch_members = [
                    OrchMemberState(
                        index=m.get("index", i),
                        task_summary=m.get("task_summary", "")[:80],
                        status=m.get("status", "pending"),
                        delegate_number=m.get("delegate_number"),
                        iterations_used=m.get("iterations_used", 0),
                        interventions=m.get("interventions", 0),
                    )
                    for i, m in enumerate(members)
                ]
            self._orch_teams[team_id] = OrchTeamState(
                team_id=team_id,
                plan_id=plan_id or "",
                status=status,
                members=orch_members,
            )

    def orch_update_member(
        self,
        team_id: str,
        member_idx: int,
        *,
        status: str | None = None,
        iterations_used: int | None = None,
        interventions: int | None = None,
    ) -> None:
        """Update a specific member's state within a team."""
        team = self._orch_teams.get(team_id)
        if team is None:
            return
        for m in team.members:
            if m.index == member_idx:
                if status is not None:
                    m.status = status
                if iterations_used is not None:
                    m.iterations_used = iterations_used
                if interventions is not None:
                    m.interventions = interventions
                return

    def orch_record_decision(
        self,
        action: str,
        context: str,
        outcome: str = "",
        team_id: str = "",
        member_idx: int = -1,
    ) -> None:
        """Append an orchestration decision to the rolling log."""
        self._orch_decisions.append(OrchDecision(
            action=action,
            context=context[:200],
            outcome=outcome[:200],
            team_id=team_id,
            member_idx=member_idx,
        ))

    def orch_add_escalation(
        self,
        team_id: str,
        member_idx: int,
        context: str,
    ) -> None:
        """Add a pending escalation request."""
        self._orch_escalations.append(OrchDecision(
            action="escalation",
            context=context[:200],
            team_id=team_id,
            member_idx=member_idx,
        ))

    def orch_resolve_escalation(
        self,
        team_id: str,
        member_idx: int,
        outcome: str,
    ) -> None:
        """Remove a pending escalation and record the resolution decision."""
        self._orch_escalations = [
            e for e in self._orch_escalations
            if not (e.team_id == team_id and e.member_idx == member_idx)
        ]
        self.orch_record_decision(
            action="escalation_resolved",
            context=f"Escalation for {team_id} member #{member_idx}",
            outcome=outcome[:200],
            team_id=team_id,
            member_idx=member_idx,
        )

    def orch_prune_stale_escalations(
        self,
        member_terminal: Callable[[str, int], bool],
    ) -> int:
        """Drop WM escalation slots whose member is already terminal."""
        before = len(self._orch_escalations)
        self._orch_escalations = [
            e for e in self._orch_escalations
            if not member_terminal(e.team_id, e.member_idx)
        ]
        return before - len(self._orch_escalations)

    def orch_get_active_teams(self) -> list[OrchTeamState]:
        """Return teams not in a terminal state."""
        return [
            t for t in self._orch_teams.values()
            if t.status not in ("completed", "partial", "failed")
        ]

    def orch_get_pending_escalations(self) -> list[OrchDecision]:
        """Return unresolved escalation requests."""
        return list(self._orch_escalations)

    def orch_clear(self) -> None:
        """Reset all orchestration state."""
        self._orch_teams.clear()
        self._orch_decisions.clear()
        self._orch_escalations.clear()
        self._coordinator_phase = "idle"
        self._coordinator_phase_detail = ""

    def orch_set_coordinator_phase(self, phase: str, detail: str = "") -> None:
        """Record coordinator phase for wake context and ring priority."""
        self._coordinator_phase = (phase or "idle").strip()
        self._coordinator_phase_detail = (detail or "")[:120]

    def get_orchestration_wake_lines(self) -> list[str]:
        """Compact one-liners for orchestration wake packets."""
        lines: list[str] = []
        if self._coordinator_phase and self._coordinator_phase != "idle":
            line = f"Coordinator phase: {self._coordinator_phase}"
            if self._coordinator_phase_detail:
                line += f" ({self._coordinator_phase_detail})"
            lines.append(line)
        pos = self.get_plan_position()
        if pos:
            lines.append(f"Plan: {pos[:220]}")
        active = self.orch_get_active_teams()
        if active:
            for team in active[:2]:
                lines.append(f"Team {team.team_id}: {team.status}")
        elif self._orch_teams:
            recent = max(
                self._orch_teams.values(),
                key=lambda t: t.completed_at or t.launched_at,
            )
            lines.append(f"Last team {recent.team_id}: {recent.status}")
        pending = self.orch_get_pending_escalations()
        if pending:
            lines.append(f"⚠ {len(pending)} pending escalation(s)")
        return lines

    def _render_orch_block(self) -> str:
        """Render the [ORCHESTRATION STATE] block for prompt injection."""
        if not self._orch_teams and not self._orch_decisions and not self._orch_escalations:
            return ""
        parts: list[str] = ["[ORCHESTRATION STATE]"]

        if self._coordinator_phase and self._coordinator_phase != "idle":
            _phase_line = f"  Phase: {self._coordinator_phase}"
            if self._coordinator_phase_detail:
                _phase_line += f" — {self._coordinator_phase_detail}"
            parts.append(_phase_line)

        # Pending escalations first (highest priority)
        if self._orch_escalations:
            for esc in self._orch_escalations:
                age = int(time.time() - esc.timestamp)
                parts.append(
                    f"  ⚠ ESCALATION: Team {esc.team_id} member #{esc.member_idx} "
                    f"({age}s ago) — {esc.context}"
                )

        # Active teams as one-line summaries
        for team in self._orch_teams.values():
            member_parts = []
            for m in team.members:
                tag = {
                    "done": "done", "running": f"iter {m.iterations_used}",
                    "failed": "FAILED", "blocked": "NEEDS HELP",
                    "pending": "pending",
                }.get(m.status, m.status)
                member_parts.append(f"#{m.index} {m.task_summary[:30]} {tag}")
            members_str = " | ".join(member_parts) if member_parts else "no members"
            parts.append(f"  Team {team.team_id} ({team.status}): {members_str}")

        # Recent decisions (last 3)
        recent = list(self._orch_decisions)[-3:]
        if recent:
            decision_parts = []
            for d in recent:
                outcome_str = f" → {d.outcome}" if d.outcome else ""
                decision_parts.append(f"{d.action}{outcome_str}")
            parts.append(f"  Recent: {' | '.join(decision_parts)}")

        parts.append("[END ORCHESTRATION STATE]")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Goal Stack
    # ------------------------------------------------------------------

    def add_goal(
        self, level: str, content: str, source: str = "system",
    ) -> None:
        """Add a goal at the given level (strategic/tactical/immediate)."""
        if level not in GOAL_LEVELS:
            level = "tactical"
        if len(self._goal_stack) >= self.cfg.max_goals:
            # Evict lowest-level goal (immediate first)
            for lvl in reversed(GOAL_LEVELS):
                candidates = [g for g in self._goal_stack if g.level == lvl]
                if candidates:
                    self._goal_stack.remove(candidates[-1])
                    break
        self._goal_stack.append(WMSlot(
            slot_type="goal", content=content, level=level,
            source=source, salience=1.0,
        ))

    def get_goals(self) -> list[WMSlot]:
        """Return goals ordered by level (strategic first)."""
        order = {lvl: i for i, lvl in enumerate(GOAL_LEVELS)}
        return sorted(self._goal_stack, key=lambda g: order.get(g.level, 99))

    def clear_goals(self, level: str | None = None) -> None:
        """Clear goals. If level given, only clear that level."""
        if level is None:
            self._goal_stack.clear()
        else:
            self._goal_stack = [
                g for g in self._goal_stack if g.level != level
            ]

    # ------------------------------------------------------------------
    # Prospective Memory (Intentions)
    # ------------------------------------------------------------------

    def add_intention(
        self, content: str, trigger: str, source: str = "system",
    ) -> None:
        """Add a deferred intention with a trigger condition."""
        if len(self._prospective) >= self.cfg.max_prospective:
            # Evict oldest intention
            self._prospective.pop(0)
        self._prospective.append(WMSlot(
            slot_type="intention", content=content, trigger=trigger,
            source=source, salience=0.9,
        ))

    def check_intentions(self, context: str) -> list[WMSlot]:
        """Check if any intentions match the current context.

        Returns triggered intentions and removes them from prospective.
        """
        if not context or not self._prospective:
            return []

        context_lower = context.lower()
        triggered: list[WMSlot] = []
        remaining: list[WMSlot] = []

        for intention in self._prospective:
            trigger_lower = intention.trigger.lower()
            matched = False

            if self.cfg.intention_check_method == "keyword":
                # Simple keyword matching: all words in trigger must appear
                keywords = trigger_lower.split()
                if keywords and all(kw in context_lower for kw in keywords):
                    matched = True
            else:
                # Substring match fallback
                if trigger_lower in context_lower:
                    matched = True

            if matched:
                intention.salience = 1.0  # boost to spotlight
                triggered.append(intention)
            else:
                remaining.append(intention)

        self._prospective = remaining
        return triggered

    def get_intentions(self) -> list[WMSlot]:
        """Return all pending intentions."""
        return list(self._prospective)

    # ------------------------------------------------------------------
    # Bulk query / mutation helpers (public API)
    # ------------------------------------------------------------------

    def get_goal_stack(self, limit: int | None = None) -> list[WMSlot]:
        """Return current goal stack, optionally capped to *limit*."""
        gs = self._goal_stack
        return gs[:limit] if limit else list(gs)

    def remove_goals_where(
        self, predicate: Callable[[WMSlot], bool],
    ) -> list[WMSlot]:
        """Remove goals matching *predicate*. Returns removed goals."""
        keep, removed = [], []
        for g in self._goal_stack:
            (removed if predicate(g) else keep).append(g)
        self._goal_stack = keep
        return removed

    def mutate_goals(
        self, fn: Callable[[WMSlot], None],
        predicate: Callable[[WMSlot], bool] | None = None,
    ) -> int:
        """Apply *fn* to each goal matching *predicate* (all if None).
        Returns count of mutated goals."""
        n = 0
        for g in self._goal_stack:
            if predicate is None or predicate(g):
                fn(g)
                n += 1
        return n

    def get_prospective(self, limit: int | None = None) -> list[WMSlot]:
        """Return pending intentions, optionally capped."""
        p = self._prospective
        return p[:limit] if limit else list(p)

    def remove_intentions_where(
        self, predicate: Callable[[WMSlot], bool],
    ) -> list[WMSlot]:
        """Remove intentions matching *predicate*. Returns removed."""
        keep, removed = [], []
        for p in self._prospective:
            (removed if predicate(p) else keep).append(p)
        self._prospective = keep
        return removed

    def get_slot_count(self) -> int:
        """Total number of non-goal slots (for cognitive load)."""
        return len(self._slots)

    def get_max_slots(self) -> int:
        """Configured max slot capacity."""
        return self.cfg.max_slots

    def get_avg_salience(self) -> float:
        """Average salience across non-goal slots (0.0 if empty)."""
        if not self._slots:
            return 0.0
        return sum(s.salience for s in self._slots) / len(self._slots)

    # ------------------------------------------------------------------
    # Attention Spotlight
    # ------------------------------------------------------------------

    def get_attention_window(self, k: int | None = None) -> list[WMSlot]:
        """Return top-k slots by salience for prompt injection.

        Merges general slots + goals + any recently triggered intentions.
        """
        k = k or self.cfg.attention_window_size
        all_active = self._slots + self._goal_stack
        all_active.sort(key=lambda s: s.salience, reverse=True)
        return all_active[:k]

    def decay_salience(self, dt: float = 1.0) -> None:
        """Decay salience on all slots. Called every heartbeat.

        Items below min_salience_evict are automatically removed.
        Instructions and consolidation slots are exempt from decay.
        """
        rate = self.cfg.salience_decay_rate * dt
        threshold = self.cfg.min_salience_evict

        for slot in self._slots:
            if _is_consolidation_slot(slot) or slot.slot_type in NO_DECAY_TYPES:
                continue
            slot_rate = rate * 3.0 if slot.slot_type in FAST_DECAY_TYPES else rate
            slot.salience = max(0.0, slot.salience - slot_rate)
        self._slots = [
            s for s in self._slots
            if s.salience >= threshold
            or _is_consolidation_slot(s)
            or s.slot_type in NO_DECAY_TYPES
        ]

        goal_rate = rate * 0.2
        for goal in self._goal_stack:
            goal.salience = max(0.3, goal.salience - goal_rate)

        # Instructions: no decay (task-scoped, cleared explicitly)

    # ------------------------------------------------------------------
    # Context String for Prompt Injection
    # ------------------------------------------------------------------

    def to_context_string(self) -> str:
        """Render working memory as a structured context block.

        Used by _format_prompt() and agentic hooks for injection.
        """
        window = self.get_attention_window()
        has_orch = bool(self._orch_teams or self._orch_decisions or self._orch_escalations)
        if (not window and not self._prospective
                and not self._instructions and not self._plan_position
                and not self._todo_board and not has_orch):
            return ""

        parts: list[str] = ["[WORKING MEMORY — your active cognitive workspace]"]

        # Instructions first (highest priority — task directives)
        if self._instructions:
            parts.append("Task Instructions:")
            for instr in self._instructions:
                parts.append(f"  ▶ {instr.content}")

        # Todo board snapshot (Kanban overview)
        if self._todo_board:
            parts.append(self._todo_board)

        # Plan position (sliding window — where you are in the plan)
        if self._plan_position:
            parts.append(self._plan_position)

        # Orchestration state (teams, escalations, decisions)
        orch_block = self._render_orch_block()
        if orch_block:
            parts.append(orch_block)

        # Goals
        goals = self.get_goals()
        if goals:
            parts.append("Goals:")
            for g in goals:
                marker = "★" if g.level == "strategic" else ("▸" if g.level == "tactical" else "○")
                parts.append(f"  {marker} [{g.level}] {g.content}")

        # Active facts / feelings / user_state / predictions
        by_type: dict[str, list[WMSlot]] = {}
        for slot in window:
            if slot.slot_type == "goal":
                continue  # already rendered
            by_type.setdefault(slot.slot_type, []).append(slot)

        type_labels = {
            "fact": "Active Facts",
            "credential": "Project Credentials",
            "feeling": "Felt State",
            "user_state": "User State",
            "prediction": "Predictions",
            "constraint": "Constraints",
            "schema": "Reasoning Patterns",
        }
        for stype, label in type_labels.items():
            slots = by_type.get(stype, [])
            if slots:
                parts.append(f"{label}:")
                for s in slots:
                    domain_tag = f" ({s.domain})" if s.domain else ""
                    parts.append(f"  • {s.content}{domain_tag}")

        # Credentials not in the attention window still get rendered
        cred_rendered = {s.domain for s in by_type.get("credential", [])}
        extra_creds = [
            s for s in self._slots
            if s.slot_type == "credential" and s.domain not in cred_rendered
        ]
        if extra_creds:
            if "credential" not in by_type:
                parts.append("Project Credentials:")
            for s in extra_creds:
                domain_tag = f" ({s.domain})" if s.domain else ""
                parts.append(f"  • {s.content}{domain_tag}")

        # Pending intentions (brief summary)
        if self._prospective:
            parts.append("Pending Intentions:")
            for intn in self._prospective[:3]:
                parts.append(f"  ⏰ when \"{intn.trigger}\" → {intn.content}")

        parts.append("[END WORKING MEMORY]")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Summary for Status API
    # ------------------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Return a summary dict for get_status() / WebSocket."""
        return {
            "slot_count": len(self._slots),
            "max_slots": self.cfg.max_slots,
            "goal_count": len(self._goal_stack),
            "intention_count": len(self._prospective),
            "instruction_count": len(self._instructions),
            "slots": [
                {
                    "type": s.slot_type,
                    "content": s.content[:160],
                    "salience": round(s.salience, 2),
                    "domain": s.domain,
                }
                for s in sorted(self._slots, key=lambda x: x.salience, reverse=True)
            ],
            "goals": [
                {"level": g.level, "content": g.content[:160]}
                for g in self.get_goals()
            ],
            "intentions": [
                {"trigger": i.trigger, "content": i.content[:160]}
                for i in self._prospective
            ],
            "instructions": [
                {
                    "content": i.content[:200],
                    "source": i.source,
                    "salience": round(i.salience, 2),
                }
                for i in self._instructions
            ],
            "plan_position": self._plan_position or None,
            "orch_teams": [
                {
                    "team_id": t.team_id,
                    "plan_id": t.plan_id,
                    "status": t.status,
                    "member_count": len(t.members),
                    "done_count": sum(1 for m in t.members if m.status == "done"),
                    "failed_count": sum(1 for m in t.members if m.status == "failed"),
                }
                for t in self._orch_teams.values()
            ],
            "orch_escalations": [
                {
                    "team_id": e.team_id,
                    "member_idx": e.member_idx,
                    "context": e.context[:120],
                    "age_seconds": int(time.time() - e.timestamp),
                }
                for e in self._orch_escalations
            ],
            "orch_decision_count": len(self._orch_decisions),
        }

    # ------------------------------------------------------------------
    # Sleep Cycle Support
    # ------------------------------------------------------------------

    def on_sleep(self) -> None:
        """Called when agent goes to sleep.

        Clears session-scoped tactical/immediate goals but preserves
        goals flagged as persistent (e.g., active project orchestration).
        Keeps strategic goals, intentions, and consolidation slots.
        Purges session-only slots and instructions (task-scoped).
        """
        # Consolidate orchestration patterns before clearing
        if self._orch_decisions or self._orch_teams:
            self._consolidate_orch_patterns()
        self.orch_clear()

        self._slots = [
            s for s in self._slots
            if _is_consolidation_slot(s)
            or s.slot_type == "credential"
            or not s.metadata.get("is_session_only", False)
        ]
        # §1.5 — Preserve tactical goals flagged persistent or project-scoped
        self._goal_stack = [
            g for g in self._goal_stack
            if g.level == "strategic"
            or g.metadata.get("persistent", False)
            or g.metadata.get("project_id")
        ]
        self.clear_instructions()
        # Keep plan_position on sleep — it's critical for multi-day projects

    def _count_effective_interventions(self, decisions: list[OrchDecision]) -> int:
        """Count interventions where the member eventually succeeded.

        Since ``intervened_*`` decisions are recorded with empty outcomes
        (the result isn't known yet), we cross-reference with member final
        status to see if the intervention led to completion.
        """
        count = 0
        for d in decisions:
            if d.outcome and ("success" in d.outcome.lower() or "completed" in d.outcome.lower()):
                count += 1
                continue
            team = self._orch_teams.get(d.team_id)
            if team is None or d.member_idx < 0:
                continue
            member = next((m for m in team.members if m.index == d.member_idx), None)
            if member is not None and member.status == "done":
                count += 1
        return count

    def _consolidate_orch_patterns(self) -> None:
        """Roll orchestration decisions + team outcomes into a consolidation slot.

        Writes directly to the ``Consolidation.OrchestrationPatterns``
        domain (appending with ``|`` separator, capped at
        ``_MAX_CONSOLIDATION_CHARS``), avoiding the ``consolidate_session``
        router which only targets the first three consolidation domains.
        """
        parts: list[str] = []

        for team in self._orch_teams.values():
            succeeded = sum(1 for m in team.members if m.status == "done")
            failed = sum(1 for m in team.members if m.status == "failed")
            total = len(team.members)
            line = f"Team {team.team_id[:8]} ({total} members): {succeeded} ok, {failed} failed"
            failures = [m.task_summary for m in team.members if m.status == "failed"]
            if failures:
                line += f" — failed: {', '.join(failures)}"
            intervened = [m for m in team.members if m.interventions > 0]
            if intervened:
                line += f" — {sum(m.interventions for m in intervened)} intervention(s)"
            parts.append(line)

        interventions = [d for d in self._orch_decisions if "intervene" in d.action]
        if interventions:
            extends = [d for d in interventions if "extend" in d.action]
            hints = [d for d in interventions if "hint" in d.action]
            terminates = [d for d in interventions if "terminate" in d.action]
            i_parts: list[str] = []
            if extends:
                worked = self._count_effective_interventions(extends)
                i_parts.append(f"{len(extends)} extend ({worked} helped)")
            if hints:
                worked = self._count_effective_interventions(hints)
                i_parts.append(f"{len(hints)} hint ({worked} helped)")
            if terminates:
                i_parts.append(f"{len(terminates)} terminate")
            parts.append("Interventions: " + ", ".join(i_parts))

        for d in self._orch_decisions:
            if d.outcome and "intervene" not in d.action and d.action not in ("escalation", "escalation_resolved"):
                parts.append(f"{d.action}: {d.outcome}")

        if not parts:
            return

        domain = CONSOLIDATION_DOMAINS[3]  # OrchestrationPatterns
        new_text = "; ".join(parts)
        existing = ""
        for s in self._slots:
            if s.domain == domain:
                existing = s.content
                break
        merged = (existing + " | " + new_text).strip(" |") if existing else new_text
        if len(merged) > _MAX_CONSOLIDATION_CHARS:
            merged = merged[-_MAX_CONSOLIDATION_CHARS:]
            cut = merged.find(" | ")
            if cut != -1 and cut < 200:
                merged = merged[cut + 3:]
        self.upsert_fact(domain=domain, content=merged, source="consolidation", salience=1.0)

    def on_wake(self) -> None:
        """Called when agent wakes up.

        Boost salience on surviving items (fresh start effect),
        and expire stale intentions older than 24 hours.
        """
        for slot in self._slots:
            slot.salience = min(1.0, slot.salience + 0.2)
        # Expire stale prospective intentions (IR-7.4)
        max_age = 86400  # 24 hours
        self._prospective = [
            i for i in self._prospective if i.age_seconds() < max_age
        ]

    # ------------------------------------------------------------------
    # Session Consolidation
    # ------------------------------------------------------------------

    def consolidate_session(self, summary: str) -> None:
        """Roll up operational context into protected consolidation slots.

        Accepts a pre-built summary string (produced by the caller — either
        a simple text append or an LLM-compressed digest).  The summary is
        split across the three consolidation domains:

        * **SessionProgress** — what was accomplished
        * **ActiveKnowledge** — key discoveries (paths, structures)
        * **TaskContext** — current/pending task state

        Each consolidation slot APPENDS new content (capped at
        ``_MAX_CONSOLIDATION_CHARS``), preserving earlier entries so the
        agent has a rolling record of the entire waking session.
        """
        if not summary:
            return

        summary = _strip_signal_tags(summary)

        progress_new = ""
        knowledge_new = ""
        context_new = ""

        lines = summary.strip().splitlines()
        bucket = "progress"
        for line in lines:
            ll = line.lower().strip()
            if ll.startswith("[knowledge]") or ll.startswith("knowledge:"):
                bucket = "knowledge"
                line = _extract_tag_content(line)
            elif ll.startswith("[context]") or ll.startswith("context:"):
                bucket = "context"
                line = _extract_tag_content(line)
            elif ll.startswith("[progress]") or ll.startswith("progress:"):
                bucket = "progress"
                line = _extract_tag_content(line)

            if line.strip():
                if bucket == "progress":
                    progress_new += line.strip() + " "
                elif bucket == "knowledge":
                    knowledge_new += line.strip() + " "
                else:
                    context_new += line.strip() + " "

        if not knowledge_new and not context_new:
            progress_new = summary.strip()

        for domain, new_text in [
            (CONSOLIDATION_DOMAINS[0], progress_new.strip()),
            (CONSOLIDATION_DOMAINS[1], knowledge_new.strip()),
            (CONSOLIDATION_DOMAINS[2], context_new.strip()),
        ]:
            if not new_text:
                continue
            existing = ""
            for s in self._slots:
                if s.domain == domain:
                    existing = s.content
                    break

            merged = (existing + " | " + new_text).strip(" |") if existing else new_text
            if len(merged) > _MAX_CONSOLIDATION_CHARS:
                merged = merged[-_MAX_CONSOLIDATION_CHARS:]
                cut = merged.find(" | ")
                if cut != -1 and cut < 200:
                    merged = merged[cut + 3:]

            self.upsert_fact(
                domain=domain,
                content=merged,
                source="consolidation",
                salience=1.0,
            )

    def get_consolidation_context(self) -> str:
        """Return the consolidation tier as a short text block."""
        parts: list[str] = []
        for domain in CONSOLIDATION_DOMAINS:
            for s in self._slots:
                if s.domain == domain:
                    label = domain.split(".")[-1]
                    parts.append(f"  [{label}] {s.content}")
                    break
        return "\n".join(parts) if parts else ""

    def replace_consolidation(self, compounded: str) -> None:
        """Replace consolidation slots with compounded content."""
        progress = ""
        knowledge = ""
        context = ""
        bucket = "progress"

        for line in compounded.strip().splitlines():
            ll = line.strip().lower()
            if ll.startswith("[sessionprogress]") or ll.startswith("[progress]"):
                bucket = "progress"
                line = line.split("]", 1)[-1].strip()
            elif ll.startswith("[activeknowledge]") or ll.startswith("[knowledge]"):
                bucket = "knowledge"
                line = line.split("]", 1)[-1].strip()
            elif ll.startswith("[taskcontext]") or ll.startswith("[context]"):
                bucket = "context"
                line = line.split("]", 1)[-1].strip()
            if line.strip():
                if bucket == "progress":
                    progress += line.strip() + " "
                elif bucket == "knowledge":
                    knowledge += line.strip() + " "
                else:
                    context += line.strip() + " "

        if not any([progress, knowledge, context]):
            progress = compounded.strip()

        for domain, text in [
            (CONSOLIDATION_DOMAINS[0], progress.strip()),
            (CONSOLIDATION_DOMAINS[1], knowledge.strip()),
            (CONSOLIDATION_DOMAINS[2], context.strip()),
        ]:
            if not text:
                continue
            self.upsert_fact(
                domain=domain,
                content=text,
                source="consolidation",
                salience=1.0,
            )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist working memory to disk."""
        path = Path(path)
        state = {
            "version": "1.3",
            "timestamp": time.time(),
            "slots": [s.to_dict() for s in self._slots],
            "goal_stack": [g.to_dict() for g in self._goal_stack],
            "prospective": [i.to_dict() for i in self._prospective],
            "instructions": [i.to_dict() for i in self._instructions],
            "plan_position": self._plan_position,
            "todo_board": self._todo_board,
            "orch_teams": {k: v.to_dict() for k, v in self._orch_teams.items()},
            "orch_decisions": [d.to_dict() for d in self._orch_decisions],
            "orch_escalations": [e.to_dict() for e in self._orch_escalations],
        }
        _atomic_write(path, state)

    def load(self, path: str | Path) -> bool:
        """Load working memory from disk."""
        path = Path(path)
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._slots = [
                WMSlot.from_dict(d) for d in state.get("slots", [])
            ]
            self._goal_stack = [
                WMSlot.from_dict(d) for d in state.get("goal_stack", [])
            ]
            self._prospective = [
                WMSlot.from_dict(d) for d in state.get("prospective", [])
            ]
            self._instructions = [
                WMSlot.from_dict(d) for d in state.get("instructions", [])
            ]
            self._plan_position = state.get("plan_position", "")
            self._todo_board = state.get("todo_board", "")
            # Orchestration state (v1.3+)
            self._orch_teams = {
                k: OrchTeamState.from_dict(v)
                for k, v in state.get("orch_teams", {}).items()
            }
            raw_decisions = state.get("orch_decisions", [])
            self._orch_decisions = collections.deque(
                (OrchDecision.from_dict(d) for d in raw_decisions),
                maxlen=8,
            )
            self._orch_escalations = [
                OrchDecision.from_dict(e) for e in state.get("orch_escalations", [])
            ]
            return True
        except (json.JSONDecodeError, OSError):
            return False


# -----------------------------------------------------------------------
# Common-slot domain prefixes — facts routed to the shared layer
# -----------------------------------------------------------------------

_COMMON_DOMAIN_PREFIXES = (
    "Agent.",
    "User.",
    "System.Config.",
)


def _is_common_domain(domain: str) -> bool:
    """Return True if *domain* belongs in the shared common layer."""
    if not domain:
        return False
    return any(domain.startswith(p) for p in _COMMON_DOMAIN_PREFIXES)


# -----------------------------------------------------------------------
# DualWorkingMemory
# -----------------------------------------------------------------------

class DualWorkingMemory:
    """Two parallel WM workspaces + a shared common layer.

    *Professional* holds operational context for user-initiated tasks
    (everything the user asks the agent to do).

    *Personal* holds operational context for agent-autonomous tasks
    (drive-initiated self-test, curiosity exploration, DMN).

    *Common* holds identity, preferences, accounts, and strategic
    goals — visible to both workspaces.

    ``activate(source)`` swaps which workspace is active based on
    whether the current task is user-initiated or autonomous.
    """

    _AUTONOMOUS_PREFIXES = ("drive:", "dmn", "autonomous", "background")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.common = WorkingMemory(config)
        self.professional = WorkingMemory(config)
        self.personal = WorkingMemory(config)
        self._active_name: str = "professional"
        self.active: WorkingMemory = self.professional

    # ------------------------------------------------------------------
    # Activation (workspace swap)
    # ------------------------------------------------------------------

    def activate(self, source: str) -> str:
        """Switch active workspace based on task source.

        Returns the name of the activated workspace ("professional"
        or "personal").
        """
        is_auto = any(
            source.startswith(p) for p in self._AUTONOMOUS_PREFIXES
        )
        name = "personal" if is_auto else "professional"
        self.active = self.personal if is_auto else self.professional
        self._active_name = name
        return name

    @property
    def active_name(self) -> str:
        return self._active_name

    # ------------------------------------------------------------------
    # Delegating API — routes to common or active
    # ------------------------------------------------------------------

    def upsert_fact(
        self, domain: str, content: str, source: str = "tool",
        salience: float = 0.9,
    ) -> None:
        """Route fact to common (identity/prefs) or active workspace."""
        target = self.common if _is_common_domain(domain) else self.active
        target.upsert_fact(domain=domain, content=content,
                           source=source, salience=salience)

    def add_fact(
        self, content: str, domain: str = "", source: str = "ans",
        salience: float = 0.8, metadata: dict[str, Any] | None = None,
    ) -> None:
        target = self.common if _is_common_domain(domain) else self.active
        target.add_fact(content=content, domain=domain, source=source,
                        salience=salience, metadata=metadata)

    def upsert_perception(
        self, domain: str, content: str, salience: float = 0.7,
    ) -> None:
        self.active.upsert_perception(domain=domain, content=content,
                                      salience=salience)

    def add_feeling(self, content: str, salience: float = 0.7) -> None:
        self.active.add_feeling(content=content, salience=salience)

    def add_user_state(self, content: str, salience: float = 0.6) -> None:
        self.common.add_user_state(content=content, salience=salience)

    def upsert_credential(
        self, domain: str, content: str, source: str = "user",
        salience: float = 1.0,
    ) -> None:
        """Route credential to the active workspace (project-scoped)."""
        self.active.upsert_credential(
            domain=domain, content=content, source=source, salience=salience,
        )

    def get_credentials(self) -> list[WMSlot]:
        """Return credentials from all workspaces."""
        return self.common.get_credentials() + self.active.get_credentials()

    def remove_by_domain(self, domain: str) -> int:
        """Remove from whichever workspace(s) hold the domain."""
        n = self.common.remove_by_domain(domain)
        n += self.active.remove_by_domain(domain)
        return n

    # Instructions, goals, intentions — always on active workspace
    def add_instruction(self, content: str, source: str = "task",
                        salience: float = 1.0) -> None:
        self.active.add_instruction(content=content, source=source,
                                    salience=salience)

    def get_instructions(self) -> list[WMSlot]:
        return self.active.get_instructions()

    def clear_instructions(self) -> None:
        self.active.clear_instructions()

    def update_instruction(self, index: int, content: str) -> bool:
        return self.active.update_instruction(index, content)

    def delete_instruction(self, index: int) -> bool:
        return self.active.delete_instruction(index)

    def set_plan_position(self, position: str) -> None:
        self.active.set_plan_position(position)

    def get_plan_position(self) -> str:
        return self.active.get_plan_position()

    def set_todo_board(self, board: str) -> None:
        self.active.set_todo_board(board)

    def get_todo_board(self) -> str:
        return self.active.get_todo_board()

    # Orchestration methods — always on active workspace
    def orch_update_team(self, team_id: str, plan_id: str = "",
                         status: str = "running",
                         members: list[dict[str, Any]] | None = None) -> None:
        self.active.orch_update_team(team_id, plan_id, status, members)

    def orch_update_member(self, team_id: str, member_idx: int, **kwargs) -> None:
        self.active.orch_update_member(team_id, member_idx, **kwargs)

    def orch_record_decision(self, action: str, context: str,
                             outcome: str = "", team_id: str = "",
                             member_idx: int = -1) -> None:
        self.active.orch_record_decision(action, context, outcome,
                                         team_id, member_idx)

    def orch_add_escalation(self, team_id: str, member_idx: int,
                            context: str) -> None:
        self.active.orch_add_escalation(team_id, member_idx, context)

    def orch_resolve_escalation(self, team_id: str, member_idx: int,
                                outcome: str) -> None:
        self.active.orch_resolve_escalation(team_id, member_idx, outcome)

    def orch_prune_stale_escalations(
        self, member_terminal: Callable[[str, int], bool],
    ) -> int:
        return self.active.orch_prune_stale_escalations(member_terminal)

    def orch_get_active_teams(self) -> list[OrchTeamState]:
        return self.active.orch_get_active_teams()

    def orch_get_pending_escalations(self) -> list[OrchDecision]:
        return self.active.orch_get_pending_escalations()

    def orch_clear(self) -> None:
        self.active.orch_clear()

    def add_goal(self, level: str, content: str,
                 source: str = "system") -> None:
        if level == "strategic":
            self.common.add_goal(level=level, content=content, source=source)
        else:
            self.active.add_goal(level=level, content=content, source=source)

    def get_goals(self) -> list[WMSlot]:
        return self.common.get_goals() + self.active.get_goals()

    def clear_goals(self, level: str | None = None) -> None:
        if level == "strategic" or level is None:
            self.common.clear_goals(level)
        if level != "strategic":
            self.active.clear_goals(level)

    def add_intention(self, content: str, trigger: str,
                      source: str = "system") -> None:
        self.common.add_intention(content=content, trigger=trigger,
                                  source=source)

    def check_intentions(self, context: str) -> list[WMSlot]:
        return self.common.check_intentions(context)

    def get_intentions(self) -> list[WMSlot]:
        return self.common.get_intentions()

    def add(self, slot: WMSlot) -> WMSlot | None:
        if _is_common_domain(slot.domain):
            return self.common.add(slot)
        return self.active.add(slot)

    def decay_salience(self, dt: float = 1.0) -> None:
        self.common.decay_salience(dt)
        self.active.decay_salience(dt)

    # ------------------------------------------------------------------
    # Bulk query / mutation helpers (delegating public API)
    # ------------------------------------------------------------------

    def get_goal_stack(self, limit: int | None = None) -> list[WMSlot]:
        merged = self.common._goal_stack + self.active._goal_stack
        return merged[:limit] if limit else merged

    def remove_goals_where(
        self, predicate: Callable[[WMSlot], bool],
    ) -> list[WMSlot]:
        r1 = self.common.remove_goals_where(predicate)
        r2 = self.active.remove_goals_where(predicate)
        return r1 + r2

    def mutate_goals(
        self, fn: Callable[[WMSlot], None],
        predicate: Callable[[WMSlot], bool] | None = None,
    ) -> int:
        n = self.common.mutate_goals(fn, predicate)
        n += self.active.mutate_goals(fn, predicate)
        return n

    def get_prospective(self, limit: int | None = None) -> list[WMSlot]:
        return self.common.get_prospective(limit)

    def remove_intentions_where(
        self, predicate: Callable[[WMSlot], bool],
    ) -> list[WMSlot]:
        return self.common.remove_intentions_where(predicate)

    def get_slot_count(self) -> int:
        return self.common.get_slot_count() + self.active.get_slot_count()

    def get_max_slots(self) -> int:
        return self.active.get_max_slots()

    def get_avg_salience(self) -> float:
        c_n = self.common.get_slot_count()
        a_n = self.active.get_slot_count()
        total = c_n + a_n
        if total == 0:
            return 0.0
        c_sum = sum(s.salience for s in self.common._slots)
        a_sum = sum(s.salience for s in self.active._slots)
        return (c_sum + a_sum) / total

    # ------------------------------------------------------------------
    # Context string — merges common + active
    # ------------------------------------------------------------------

    def to_context_string(self) -> str:
        """Render merged context: common layer + active workspace."""
        common_ctx = self.common.to_context_string()
        active_ctx = self.active.to_context_string()
        has_orch = bool(
            self.active._orch_teams
            or self.active._orch_decisions
            or self.active._orch_escalations
        )

        if not common_ctx and not active_ctx and not has_orch:
            return ""

        parts: list[str] = [
            "[WORKING MEMORY — your active cognitive workspace]",
        ]

        # Instructions from active workspace (highest priority)
        if self.active._instructions:
            parts.append("Task Instructions:")
            for instr in self.active._instructions:
                parts.append(f"  ▶ {instr.content}")

        # Plan position from active
        if self.active._plan_position:
            parts.append(self.active._plan_position)

        # Orchestration state (teams, escalations, decisions)
        orch_block = self.active._render_orch_block()
        if orch_block:
            parts.append(orch_block)

        # Goals: strategic from common + tactical/immediate from active
        all_goals = self.get_goals()
        if all_goals:
            parts.append("Goals:")
            for g in all_goals:
                marker = (
                    "★" if g.level == "strategic"
                    else ("▸" if g.level == "tactical" else "○")
                )
                parts.append(f"  {marker} [{g.level}] {g.content}")

        # Session Consolidation (protected tier — always shown first)
        consol_ctx = self.active.get_consolidation_context()
        if consol_ctx:
            parts.append("Session Consolidation (long-term context):")
            parts.append(consol_ctx)

        # Merge slots from both (common identity + active operational)
        merged_slots = sorted(
            self.common._slots + self.active._slots,
            key=lambda s: s.salience, reverse=True,
        )[:self.active.cfg.attention_window_size]

        by_type: dict[str, list[WMSlot]] = {}
        for slot in merged_slots:
            if slot.slot_type == "goal":
                continue
            if _is_consolidation_slot(slot):
                continue
            by_type.setdefault(slot.slot_type, []).append(slot)

        type_labels = {
            "fact": "Active Facts",
            "credential": "Project Credentials",
            "feeling": "Felt State",
            "user_state": "User State",
            "prediction": "Predictions",
            "constraint": "Constraints",
            "schema": "Reasoning Patterns",
            "perception": "Perceptions",
        }
        for stype, label in type_labels.items():
            slots = by_type.get(stype, [])
            if slots:
                parts.append(f"{label}:")
                for s in slots:
                    domain_tag = f" ({s.domain})" if s.domain else ""
                    parts.append(f"  • {s.content}{domain_tag}")

        # Credentials not in the attention window still get rendered
        all_slots = self.common._slots + self.active._slots
        cred_rendered = {s.domain for s in by_type.get("credential", [])}
        extra_creds = [
            s for s in all_slots
            if s.slot_type == "credential" and s.domain not in cred_rendered
        ]
        if extra_creds:
            if "credential" not in by_type:
                parts.append("Project Credentials:")
            for s in extra_creds:
                domain_tag = f" ({s.domain})" if s.domain else ""
                parts.append(f"  • {s.content}{domain_tag}")

        # Intentions from common
        if self.common._prospective:
            parts.append("Pending Intentions:")
            for intn in self.common._prospective[:3]:
                parts.append(
                    f"  ⏰ when \"{intn.trigger}\" → {intn.content}"
                )

        parts.append("[END WORKING MEMORY]")
        return "\n".join(parts)

    def get_attention_window(self, k: int | None = None) -> list[WMSlot]:
        k = k or self.active.cfg.attention_window_size
        merged = self.common._slots + self.active._slots
        merged.sort(key=lambda s: s.salience, reverse=True)
        return merged[:k]

    # ------------------------------------------------------------------
    # Session Consolidation
    # ------------------------------------------------------------------

    def consolidate_session(self, summary: str) -> None:
        """Consolidate operational context into the active workspace."""
        self.active.consolidate_session(summary)

    def get_consolidation_context(self) -> str:
        """Return consolidation context from the active workspace."""
        return self.active.get_consolidation_context()

    def replace_consolidation(self, compounded: str) -> None:
        """Forward replace_consolidation to the active workspace."""
        self.active.replace_consolidation(compounded)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Return a summary dict covering ALL workspaces (not just active).

        Used by day summary generation and the status API, so it must
        reflect the full breadth of stored context.
        """
        active_summary = self.active.get_summary()
        active_summary["active_workspace"] = self._active_name
        active_summary["common_slot_count"] = len(self.common._slots)

        other = (
            self.personal if self._active_name == "professional"
            else self.professional
        )
        other_name = (
            "personal" if self._active_name == "professional"
            else "professional"
        )
        other_summary = other.get_summary()
        if other_summary.get("slot_count", 0) > 0:
            active_summary[f"{other_name}_slots"] = other_summary["slots"]
            active_summary[f"{other_name}_slot_count"] = other_summary["slot_count"]
            active_summary[f"{other_name}_goals"] = other_summary.get("goals", [])

        consol = self.active.get_consolidation_context()
        if consol:
            active_summary["consolidation_context"] = consol

        return active_summary

    # ------------------------------------------------------------------
    # Sleep / Wake
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_wm_pair_domain_static(slot: Any) -> str:
        """Route a WM slot to the right expert domain for training.

        Heuristic based on slot domain path and content:
        - identity: agent/user names, accounts, self-description
        - relationships: APIs, services, databases, deployment, people
        - preferences: tech stack, architecture, workflow, project
        """
        return _classify_wm_pair_domain(slot)

    def generate_training_pairs(self) -> list[dict[str, str]]:
        """Extract WM content as Alpaca-format training pairs for sleep.

        Produces instruction/output dicts from:
        1. Consolidation slots (session progress, active knowledge, task context)
        2. Digest/tool/plan facts (operational knowledge about projects, code, etc.)
        3. Active goals (strategic context about what the agent is working on)

        Each pair includes a ``_domain`` hint for per-expert filtering:
        - ``identity``: agent/user self-facts, names, accounts, goals
        - ``relationships``: APIs, services, deployment, databases, people
        - ``preferences``: tech stack, architecture, workflow, project structure

        Called by sleep_scheduler before on_sleep clears session-scoped data.
        """
        pairs: list[dict[str, str]] = []

        for ws_name, ws in [("professional", self.professional),
                            ("personal", self.personal)]:
            for slot in ws._slots:
                if not slot.content or len(slot.content.strip()) < 15:
                    continue

                if _is_consolidation_slot(slot):
                    label = slot.domain.split(".")[-1]
                    pairs.append({
                        "instruction": f"What do you remember about {label} from recent sessions?",
                        "output": slot.content.strip(),
                        "_domain": _classify_wm_pair_domain(slot),
                    })

                elif slot.slot_type == "credential":
                    continue

                elif _slot_is_credential_by_domain(slot):
                    continue

                elif slot.slot_type == "fact" and slot.source in ("digest", "tool", "plan", "ans"):
                    domain_hint = slot.domain or slot.source
                    pairs.append({
                        "instruction": f"What do you know about {domain_hint}?",
                        "output": slot.content.strip(),
                        "_domain": _classify_wm_pair_domain(slot),
                    })

            for goal in ws._goal_stack:
                if goal.level == "strategic" and goal.content and len(goal.content.strip()) > 10:
                    pairs.append({
                        "instruction": "What are your current strategic goals?",
                        "output": goal.content.strip(),
                        "_domain": "identity",
                    })

        for slot in self.common._slots:
            if not slot.content or len(slot.content.strip()) < 15:
                continue
            if _is_consolidation_slot(slot):
                label = slot.domain.split(".")[-1]
                pairs.append({
                    "instruction": f"What do you remember about {label}?",
                    "output": slot.content.strip(),
                    "_domain": _classify_wm_pair_domain(slot),
                })
            elif slot.slot_type == "fact":
                domain_hint = slot.domain or "general knowledge"
                pairs.append({
                    "instruction": f"What do you know about {domain_hint}?",
                    "output": slot.content.strip(),
                    "_domain": _classify_wm_pair_domain(slot),
                })

        # Orchestration decision pairs — behavioral training data (→ preferences)
        for ws_name, ws in [("professional", self.professional),
                            ("personal", self.personal)]:
            for decision in ws._orch_decisions:
                if not decision.outcome or len(decision.outcome.strip()) < 10:
                    continue
                if "intervene" in decision.action:
                    pairs.append({
                        "instruction": (
                            f"A team member needed intervention "
                            f"({decision.action}). Context: {decision.context}"
                        ),
                        "output": decision.outcome,
                        "_domain": "preferences",
                    })
                elif decision.action == "launched_team":
                    pairs.append({
                        "instruction": (
                            f"How did the team launch go? "
                            f"Context: {decision.context}"
                        ),
                        "output": decision.outcome,
                        "_domain": "preferences",
                    })
                elif decision.action in ("team_completed", "member_failed"):
                    pairs.append({
                        "instruction": (
                            f"What happened when {decision.action}? "
                            f"Context: {decision.context}"
                        ),
                        "output": decision.outcome,
                        "_domain": "preferences",
                    })

            # Team-level outcome pairs (→ preferences)
            for team in ws._orch_teams.values():
                if team.status not in ("completed", "partial", "failed"):
                    continue
                succeeded = sum(1 for m in team.members if m.status == "done")
                failed_members = [m for m in team.members if m.status == "failed"]
                total = len(team.members)
                if total == 0:
                    continue
                member_desc = ", ".join(
                    m.task_summary for m in team.members[:4]
                )
                outcome_parts = [f"{succeeded}/{total} members succeeded"]
                for fm in failed_members[:2]:
                    outcome_parts.append(f"Failed: {fm.task_summary}")
                intervened = [m for m in team.members if m.interventions > 0]
                if intervened:
                    outcome_parts.append(
                        f"{len(intervened)} member(s) needed intervention"
                    )
                pairs.append({
                    "instruction": (
                        f"How should you structure a team with tasks: "
                        f"{member_desc}?"
                    ),
                    "output": ". ".join(outcome_parts),
                    "_domain": "preferences",
                })

        return pairs

    def on_sleep(self) -> None:
        """Consolidate operational knowledge before clearing session data.

        Each workspace gets a pre-sleep consolidation pass so that the
        protected consolidation slots capture what happened during this
        waking period.  Then ``WorkingMemory.on_sleep()`` runs on each
        workspace, which handles orchestration pattern consolidation
        (→ ``OrchestrationPatterns`` domain) and session data cleanup.
        """
        for ws in (self.professional, self.personal):
            digest_facts = [
                s for s in ws._slots
                if s.slot_type == "fact"
                and not _is_consolidation_slot(s)
                and s.source in ("digest", "tool", "plan")
            ]
            if digest_facts:
                progress = []
                knowledge = []
                for f in digest_facts[:10]:
                    snippet = _strip_signal_tags(f.content)[:200]
                    if f.source == "digest":
                        knowledge.append(snippet)
                    else:
                        progress.append(snippet)
                lines: list[str] = []
                if progress:
                    lines.append(f"[Progress] {' | '.join(progress)}")
                if knowledge:
                    lines.append(f"[Knowledge] {' | '.join(knowledge)}")
                ws.consolidate_session("\n".join(lines) if lines else "")

        self.common.on_sleep()
        self.professional.on_sleep()
        self.personal.on_sleep()

    def on_wake(self) -> None:
        self.common.on_wake()
        self.professional.on_wake()
        self.personal.on_wake()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, agent_dir: str | Path) -> None:
        """Persist all three WM instances to the agent directory."""
        agent_dir = Path(agent_dir)
        self.common.save(agent_dir / "wm_common.json")
        self.professional.save(agent_dir / "wm_professional.json")
        self.personal.save(agent_dir / "wm_personal.json")

    def load(self, agent_dir: str | Path) -> bool:
        """Load all three WM instances from the agent directory."""
        agent_dir = Path(agent_dir)
        c = self.common.load(agent_dir / "wm_common.json")
        p = self.professional.load(agent_dir / "wm_professional.json")
        a = self.personal.load(agent_dir / "wm_personal.json")
        # Also try loading legacy single-file format into professional
        if not p and not c:
            legacy = agent_dir / "working_memory_state.json"
            if legacy.exists():
                self.professional.load(legacy)
        return c or p or a
