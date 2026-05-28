"""Cryptex Context Engine — Rotating-ring working memory.

Inspired by the Da Vinci Code cryptex, this module models the agent's
working memory as a series of independently rotatable rings.  Each ring
holds one slot category; each *position* on a ring represents a project
or domain context.  The combination of active positions across all rings
defines the agent's current cognitive context.

Ring categories
---------------
  **Fixed**    — always visible regardless of project (identity, user
                 model, consolidation, emotional state, strategic goals).
  **Project**  — one position per project/context (orchestration,
                 instructions, facts, credentials, tactical goals).
  **Domain**   — one position per capability area (skills, tools/MCP,
                 channels).

The ``CryptexMemory`` class extends ``DualWorkingMemory`` for backward
compatibility — every caller that uses ``professional``, ``active``,
``activate(source)`` etc. continues to work unchanged.  Internally,
project-rotating rings break the old monolithic ``professional``
workspace into per-project slices so parallel projects never pollute
each other.
"""

from __future__ import annotations

import collections
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .working_memory import (
    ACCESS_GENESIS,
    ACCESS_MALLEABLE,
    ACCESS_SESSION,
    ACCESS_SYSTEM,
    CONSOLIDATION_DOMAINS,
    WMSlot,
    WorkingMemory,
    WorkingMemoryConfig,
    OrchTeamState,
    OrchDecision,
    OrchMemberState,
    _atomic_write,
    _extract_tag_content,
    _is_consolidation_slot,
    _is_common_domain,
    _slot_is_credential_by_domain,
    _COMMON_DOMAIN_PREFIXES,
    _MAX_CONSOLIDATION_CHARS,
    _strip_signal_tags,
)

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Ring Categories
# -----------------------------------------------------------------------

RING_FIXED = "fixed"
RING_PROJECT = "project"
RING_DOMAIN = "domain"

# -----------------------------------------------------------------------
# Ring Definitions — the 13 rings of the cryptex
# -----------------------------------------------------------------------

RING_IDENTITY = "identity"
RING_USER_MODEL = "user_model"
RING_CONSOLIDATION = "consolidation"
RING_EMOTIONAL = "emotional"
RING_STRATEGIC_GOALS = "strategic_goals"
RING_ORCHESTRATION = "orchestration"
RING_INSTRUCTIONS = "instructions"
RING_PROJECT_FACTS = "project_facts"
RING_CREDENTIALS = "credentials"
RING_TACTICAL_GOALS = "tactical_goals"
RING_SKILLS = "skills"
RING_TOOLS_MCP = "tools_mcp"
RING_CHANNELS = "channels"
RING_BEHAVIORAL = "behavioral"
RING_ENVIRONMENT = "environment"
RING_WAKE_ATTENTION = "wake_attention"


@dataclass
class RingSpec:
    """Immutable specification for a ring on the cryptex."""
    ring_id: str
    category: str
    display_name: str
    allow_cross_read: bool = True
    max_slots_per_position: int = 12
    clear_on_sleep: bool = False


RING_REGISTRY: tuple[RingSpec, ...] = (
    # --- Fixed rings (Rings 0-4) ---
    RingSpec(RING_IDENTITY, RING_FIXED, "Identity + Soul",
             allow_cross_read=False, max_slots_per_position=8),
    RingSpec(RING_USER_MODEL, RING_FIXED, "User Model",
             allow_cross_read=False, max_slots_per_position=6),
    RingSpec(RING_CONSOLIDATION, RING_FIXED, "Consolidation History",
             allow_cross_read=False, max_slots_per_position=8),
    RingSpec(RING_EMOTIONAL, RING_FIXED, "Emotional / Hormonal State",
             allow_cross_read=False, max_slots_per_position=4),
    RingSpec(RING_STRATEGIC_GOALS, RING_FIXED, "Strategic Goals + Intentions",
             allow_cross_read=False, max_slots_per_position=6),

    # --- Project-rotating rings (Rings 5-9) ---
    RingSpec(RING_ORCHESTRATION, RING_PROJECT, "Orchestration",
             allow_cross_read=True, max_slots_per_position=10,
             clear_on_sleep=True),
    RingSpec(RING_INSTRUCTIONS, RING_PROJECT, "Instructions + Plan + Todos",
             allow_cross_read=False, max_slots_per_position=10,
             clear_on_sleep=True),
    RingSpec(RING_PROJECT_FACTS, RING_PROJECT, "Project Facts",
             allow_cross_read=True, max_slots_per_position=15),
    RingSpec(RING_CREDENTIALS, RING_PROJECT, "Credentials",
             allow_cross_read=True, max_slots_per_position=8),
    RingSpec(RING_TACTICAL_GOALS, RING_PROJECT, "Tactical Goals",
             allow_cross_read=True, max_slots_per_position=6,
             clear_on_sleep=True),

    # --- Domain-rotating rings (Rings 10-12) ---
    RingSpec(RING_SKILLS, RING_DOMAIN, "Skills",
             allow_cross_read=True, max_slots_per_position=12),
    RingSpec(RING_TOOLS_MCP, RING_DOMAIN, "Tools + MCP Connections",
             allow_cross_read=True, max_slots_per_position=20),
    RingSpec(RING_CHANNELS, RING_DOMAIN, "Channels + Comms",
             allow_cross_read=True, max_slots_per_position=8),

    # --- New rings (13-14) ---
    RingSpec(RING_BEHAVIORAL, RING_PROJECT, "Behavioral Patterns + Rules",
             allow_cross_read=True, max_slots_per_position=20,
             clear_on_sleep=False),
    RingSpec(RING_ENVIRONMENT, RING_FIXED, "Environment + Runtime",
             allow_cross_read=False, max_slots_per_position=6,
             clear_on_sleep=False),
    RingSpec(RING_WAKE_ATTENTION, RING_PROJECT, "Wake Attention Board",
             allow_cross_read=False, max_slots_per_position=6,
             clear_on_sleep=True),
)

RING_SPECS_BY_ID: dict[str, RingSpec] = {r.ring_id: r for r in RING_REGISTRY}

DEFAULT_PROJECT = "general"
DEFAULT_DOMAIN = "general"

# Slot-type -> ring mapping for automatic routing
_SLOT_TYPE_TO_RING: dict[str, str] = {
    "instruction": RING_INSTRUCTIONS,
    "credential": RING_CREDENTIALS,
    "perception": RING_PROJECT_FACTS,
    "schema": RING_PROJECT_FACTS,
    "skill": RING_SKILLS,
    "behavioral": RING_BEHAVIORAL,
    "environment": RING_ENVIRONMENT,
}

def _slot_type_to_ring(slot: WMSlot) -> str | None:
    """Determine which ring a slot belongs to, or None for fixed/common."""
    if slot.slot_type in _SLOT_TYPE_TO_RING:
        return _SLOT_TYPE_TO_RING[slot.slot_type]
    if slot.slot_type == "goal":
        if slot.level == "strategic":
            return None  # fixed layer
        return RING_TACTICAL_GOALS
    if slot.slot_type == "fact":
        if _is_consolidation_slot(slot):
            return None  # fixed layer
        if any(slot.domain.startswith(p) for p in _COMMON_DOMAIN_PREFIXES):
            return None  # fixed layer
        return RING_PROJECT_FACTS
    if slot.slot_type in ("feeling", "user_state"):
        return None  # fixed layer
    return RING_PROJECT_FACTS  # fallback for unknown types


# -----------------------------------------------------------------------
# WMRing — a single rotatable ring
# -----------------------------------------------------------------------

@dataclass
class WMRing:
    """One ring of the cryptex.

    Holds multiple *positions* (keyed by project or domain ID), each
    containing a list of ``WMSlot`` entries.  Exactly one position is
    *active* at a time; the rest are available for cross-read or
    explicit rotation via the WM tool.
    """
    spec: RingSpec
    positions: dict[str, list[WMSlot]] = field(default_factory=dict)
    active_position: str = ""
    _total_writes: int = 0

    def __post_init__(self) -> None:
        if not self.active_position:
            default = (
                DEFAULT_PROJECT if self.spec.category == RING_PROJECT
                else DEFAULT_DOMAIN
            )
            self.active_position = default

    # --- Position management ---

    def get_or_create_position(self, position_id: str) -> list[WMSlot]:
        if position_id not in self.positions:
            self.positions[position_id] = []
        return self.positions[position_id]

    def get_active_slots(self) -> list[WMSlot]:
        return self.positions.get(self.active_position, [])

    def rotate(self, to_position: str) -> str:
        """Rotate to a new position.  Creates it if it doesn't exist."""
        old = self.active_position
        self.get_or_create_position(to_position)
        self.active_position = to_position
        return old

    # --- Slot operations on the active position ---

    def add_slot(
        self, slot: WMSlot, position: str | None = None,
    ) -> WMSlot | None:
        """Add a slot to the given position (default: active).

        If at capacity, evicts the lowest-salience non-protected slot.
        Genesis-access slots are never evicted.
        Returns the evicted slot or None.
        """
        pos = position or self.active_position
        slots = self.get_or_create_position(pos)
        self._total_writes += 1

        if len(slots) >= self.spec.max_slots_per_position:
            evictable = [
                s for s in slots
                if not _is_consolidation_slot(s)
                and getattr(s, "access", "malleable") != "genesis"
            ]
            if evictable:
                weakest = min(evictable, key=lambda s: s.salience)
                if weakest.salience < slot.salience:
                    slots.remove(weakest)
                    slots.append(slot)
                    return weakest
            return None
        slots.append(slot)
        return None

    def upsert_slot(
        self, domain: str, content: str,
        slot_type: str = "fact", salience: float = 0.8,
        source: str = "tool", position: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Insert or update a slot by domain within a position.

        Genesis-access slots can only be modified by genesis-source writes.
        """
        pos = position or self.active_position
        slots = self.get_or_create_position(pos)
        for s in slots:
            if s.domain == domain and s.slot_type == slot_type:
                if getattr(s, "access", "malleable") == "genesis" and source != "genesis":
                    return  # locked — cannot modify genesis slot
                s.content = content
                s.salience = max(s.salience, salience)
                s.created_at = time.time()
                return
        self.add_slot(WMSlot(
            slot_type=slot_type, content=content,
            salience=salience, source=source, domain=domain,
            **kwargs,
        ), position=pos)

    def remove_by_domain(
        self, domain: str, position: str | None = None,
        source: str = "",
    ) -> int:
        """Remove slots matching *domain*. Genesis-access slots are protected."""
        pos = position or self.active_position
        slots = self.get_or_create_position(pos)
        before = len(slots)
        self.positions[pos] = [
            s for s in slots
            if s.domain != domain
            or (getattr(s, "access", "malleable") == "genesis" and source != "genesis")
        ]
        return before - len(self.positions[pos])

    def remove_by_metadata(self, key: str, value: Any) -> int:
        """Remove slots from ALL positions whose metadata[key] == value."""
        total = 0
        for pos_id, slots in self.positions.items():
            before = len(slots)
            self.positions[pos_id] = [
                s for s in slots if s.metadata.get(key) != value
            ]
            total += before - len(self.positions[pos_id])
        return total

    def get_slots_by_type(
        self, slot_type: str, position: str | None = None,
    ) -> list[WMSlot]:
        pos = position or self.active_position
        return [s for s in self.positions.get(pos, []) if s.slot_type == slot_type]

    # --- Cross-read ---

    def cross_read(
        self, max_per_position: int = 1,
    ) -> list[tuple[str, WMSlot]]:
        """Return top-salience slots from non-active positions (peripheral glimpse)."""
        if not self.spec.allow_cross_read:
            return []
        result: list[tuple[str, WMSlot]] = []
        for pos_id, slots in self.positions.items():
            if pos_id == self.active_position or not slots:
                continue
            top = sorted(slots, key=lambda s: s.salience, reverse=True)
            for s in top[:max_per_position]:
                result.append((pos_id, s))
        return result

    # --- Search across all positions ---

    def search(
        self, query: str, max_results: int = 5,
    ) -> list[tuple[str, WMSlot, float]]:
        """Keyword search across all positions.  Returns (position, slot, score)."""
        query_lower = query.lower()
        tokens = query_lower.split()
        results: list[tuple[str, WMSlot, float]] = []
        for pos_id, slots in self.positions.items():
            for slot in slots:
                text = (slot.content + " " + slot.domain).lower()
                score = sum(1.0 for t in tokens if t in text) / max(len(tokens), 1)
                if score > 0:
                    if pos_id == self.active_position:
                        score *= 1.5  # boost active position
                    results.append((pos_id, slot, score))
        results.sort(key=lambda x: x[2], reverse=True)
        return results[:max_results]

    # --- Summary rendering ---

    def to_summary_line(self) -> str:
        """Compact one-line digest for tier-2 context rendering."""
        active_slots = self.get_active_slots()
        if not active_slots:
            return ""
        topics: list[str] = []
        for s in active_slots:
            label = s.domain if s.domain else s.content[:40]
            if label and label not in topics:
                topics.append(label)
        latest = max((s.created_at for s in active_slots), default=0)
        age_min = int((time.time() - latest) / 60) if latest else 0
        if age_min < 60:
            age_str = f"{age_min}m ago"
        else:
            age_str = f"{age_min // 60}h ago"
        topic_str = ", ".join(topics[:6])
        if len(topics) > 6:
            topic_str += f" (+{len(topics) - 6} more)"
        return (
            f"  [{self.spec.display_name} — {len(active_slots)} slots, "
            f"updated {age_str}]\n"
            f"    Topics: {topic_str}\n"
            f"    -> expand with wm_tool(ring=\"{self.spec.ring_id}\")"
        )

    # --- Decay ---

    def decay_salience(self, dt: float = 1.0) -> None:
        from .working_memory import FAST_DECAY_TYPES, NO_DECAY_TYPES
        rate = 0.005
        for slots in self.positions.values():
            for s in slots:
                if s.slot_type in NO_DECAY_TYPES:
                    continue
                multiplier = 3.0 if s.slot_type in FAST_DECAY_TYPES else 1.0
                s.salience = max(0.0, s.salience - rate * multiplier * dt)

    # --- Serialization ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "ring_id": self.spec.ring_id,
            "active_position": self.active_position,
            "positions": {
                pos_id: [s.to_dict() for s in slots]
                for pos_id, slots in self.positions.items()
            },
        }

    def load_positions(self, data: dict[str, Any]) -> None:
        self.active_position = data.get("active_position", self.active_position)
        for pos_id, slot_dicts in data.get("positions", {}).items():
            self.positions[pos_id] = [
                WMSlot.from_dict(d) for d in slot_dicts
            ]
        # Migration: if active_position is "general" but only "primary"
        # exists (from old register_primary default), rename to "general"
        if (
            self.active_position == "general"
            and "primary" in self.positions
            and "general" not in self.positions
        ):
            self.positions["general"] = self.positions.pop("primary")

    # --- Status ---

    @property
    def position_ids(self) -> list[str]:
        return list(self.positions.keys())

    def get_status(self, include_slots: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ring_id": self.spec.ring_id,
            "category": self.spec.category,
            "display_name": self.spec.display_name,
            "active_position": self.active_position,
            "positions": {
                pos_id: len(slots)
                for pos_id, slots in self.positions.items()
            },
            "total_writes": self._total_writes,
            "max_slots": self.spec.max_slots_per_position,
        }
        if include_slots:
            _MAX_PREVIEW = 120
            is_credential = self.spec.ring_id == "credentials"
            pos_slots: dict[str, list[dict[str, Any]]] = {}
            for pos_id, slots in self.positions.items():
                summaries: list[dict[str, Any]] = []
                for s in sorted(slots, key=lambda x: x.salience, reverse=True):
                    content_preview = s.content[:_MAX_PREVIEW]
                    if len(s.content) > _MAX_PREVIEW:
                        content_preview += "…"
                    if is_credential:
                        content_preview = "••••••••"
                    summaries.append({
                        "domain": s.domain,
                        "slot_type": s.slot_type,
                        "salience": round(s.salience, 2),
                        "content": content_preview,
                        "age_s": round(s.age_seconds()),
                        "access": getattr(s, "access", "malleable"),
                        "source": s.source,
                    })
                pos_slots[pos_id] = summaries
            result["slot_details"] = pos_slots
        return result


# -----------------------------------------------------------------------
# CryptexMemory — the full cryptex engine
# -----------------------------------------------------------------------

class CryptexMemory:
    """Cryptex-style context engine with independently rotatable rings.

    Extends the DualWorkingMemory API for full backward compatibility.
    Internally, the old ``professional`` workspace is decomposed into
    per-project positions across project-rotating rings.

    The ``common`` and ``personal`` WorkingMemory instances are kept
    for fixed-ring data and autonomous-mode data respectively.
    """

    _AUTONOMOUS_PREFIXES = ("drive:", "dmn", "autonomous", "background")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}

        # Fixed-layer storage (backward compat: identity, user, consolidation)
        self.common = WorkingMemory(config)
        # Autonomous workspace (unchanged)
        self.personal = WorkingMemory(config)

        # The cryptex rings
        self._rings: dict[str, WMRing] = {}
        for spec in RING_REGISTRY:
            ring = WMRing(spec=spec)
            self._rings[spec.ring_id] = ring

        # Active project and domain tracking
        self._active_project: str = DEFAULT_PROJECT
        self._active_domain: str = DEFAULT_DOMAIN
        self._active_name: str = "professional"

        # "professional" backward-compat view
        self._professional_view: WorkingMemory | None = None

        # --- Dynamic ring priority (Phase → prompt position) ---
        # Higher priority = rendered earlier in prompt = more LLM attention.
        # Updated by update_ring_priorities() each iteration.
        self._ring_priorities: dict[str, float] = {
            spec.ring_id: 0.5 for spec in RING_REGISTRY
        }
        self._cognitive_phase: str = "idle"

    # ------------------------------------------------------------------
    # Core: active pointer
    # ------------------------------------------------------------------

    @property
    def active(self) -> WorkingMemory:
        """Backward-compat: return a WorkingMemory that reflects the
        active project's ring positions.

        For the personal workspace, returns self.personal directly.
        For professional, returns a view backed by the project rings.
        """
        if self._active_name == "personal":
            return self.personal
        return self._get_professional_view()

    @property
    def professional(self) -> WorkingMemory:
        """Backward-compat: the 'general' project view."""
        return self._get_professional_view()

    @property
    def active_name(self) -> str:
        return self._active_name

    @property
    def active_project(self) -> str:
        return self._active_project

    @property
    def active_domain(self) -> str:
        return self._active_domain

    def _get_professional_view(self) -> WorkingMemory:
        """Return a WorkingMemory instance that mirrors the current project
        rings' active positions.  Lazily created and refreshed."""
        if self._professional_view is None:
            self._professional_view = WorkingMemory(self._config)
        return self._professional_view

    def _sync_rings_to_view(self) -> None:
        """Sync ring slot data into the professional view for legacy callers.

        Preserves orchestration state (teams, decisions, escalations) since
        those are managed directly on the view, not on rings.
        Also includes consolidation ring slots so legacy callers (e.g.
        ``get_consolidation_context``) can see them.
        """
        view = self._get_professional_view()
        # Only clear the slot/instruction/goal lists — NOT orch state
        view._slots = []
        view._instructions = []
        view._goal_stack = []

        # Sync plan position and todo board from rings
        plan_pos = self.get_plan_position()
        if plan_pos:
            view._plan_position = plan_pos
        todo_board = self.get_todo_board()
        if todo_board:
            view._todo_board = todo_board

        for ring_id, ring in self._rings.items():
            spec = ring.spec
            if spec.category != RING_PROJECT:
                continue
            for slot in ring.get_active_slots():
                if slot.domain in ("_plan_position", "_todo_board"):
                    continue
                if slot.slot_type == "instruction":
                    view._instructions.append(slot)
                elif slot.slot_type == "goal" and slot.level in ("tactical", "immediate"):
                    view._goal_stack.append(slot)
                else:
                    view._slots.append(slot)

        # Include consolidation ring slots so get_consolidation_context()
        # on the view sees them (the consolidation ring is fixed, not project)
        consol_ring = self._rings.get(RING_CONSOLIDATION)
        if consol_ring:
            for slot in consol_ring.get_active_slots():
                view._slots.append(slot)

    # ------------------------------------------------------------------
    # Activation (workspace swap + ring rotation)
    # ------------------------------------------------------------------

    def activate(
        self, source: str,
        project_id: str | None = None,
        domain_hint: str | None = None,
    ) -> str:
        """Switch active workspace and optionally rotate project/domain rings.

        Parameters
        ----------
        source : str
            Task source.  Autonomous prefixes activate personal workspace.
        project_id : str or None
            Which project position to activate on project rings.
            Defaults to current active project (or "general").
        domain_hint : str or None
            Which domain position to activate on domain rings (e.g.
            "frontend", "backend").
        """
        is_auto = any(
            source.startswith(p) for p in self._AUTONOMOUS_PREFIXES
        )
        if is_auto:
            self._active_name = "personal"
            return "personal"

        self._active_name = "professional"

        if project_id is not None:
            self._active_project = project_id
            for ring in self._rings.values():
                if ring.spec.category == RING_PROJECT:
                    ring.rotate(project_id)

        if domain_hint is not None:
            self._active_domain = domain_hint
            for ring in self._rings.values():
                if ring.spec.category == RING_DOMAIN:
                    ring.rotate(domain_hint)

        return "professional"

    # ------------------------------------------------------------------
    # Ring access
    # ------------------------------------------------------------------

    def get_ring(self, ring_id: str) -> WMRing | None:
        return self._rings.get(ring_id)

    def get_rings_by_category(self, category: str) -> list[WMRing]:
        return [r for r in self._rings.values() if r.spec.category == category]

    def get_all_project_ids(self) -> list[str]:
        """Return all project IDs that have any data across project rings."""
        ids: set[str] = set()
        for ring in self._rings.values():
            if ring.spec.category == RING_PROJECT:
                ids.update(ring.position_ids)
        return sorted(ids)

    def get_all_domain_ids(self) -> list[str]:
        """Return all domain IDs that have any data across domain rings."""
        ids: set[str] = set()
        for ring in self._rings.values():
            if ring.spec.category == RING_DOMAIN:
                ids.update(ring.position_ids)
        return sorted(ids)

    def get_or_create_project(self, project_id: str) -> str:
        """Ensure a project position exists on all project rings."""
        for ring in self._rings.values():
            if ring.spec.category == RING_PROJECT:
                ring.get_or_create_position(project_id)
        return project_id

    # ------------------------------------------------------------------
    # Delegating API — routes to rings or common/personal
    # ------------------------------------------------------------------

    def upsert_fact(
        self, domain: str, content: str, source: str = "tool",
        salience: float = 0.9,
    ) -> None:
        if _is_common_domain(domain):
            self.common.upsert_fact(domain=domain, content=content,
                                    source=source, salience=salience)
        elif _is_consolidation_slot(WMSlot(slot_type="fact", content="", domain=domain)):
            ring = self._rings[RING_CONSOLIDATION]
            ring.upsert_slot(
                domain=domain, content=content, slot_type="fact",
                salience=salience, source=source,
            )
        elif self._active_name == "personal":
            self.personal.upsert_fact(domain=domain, content=content,
                                      source=source, salience=salience)
        else:
            ring = self._rings[RING_PROJECT_FACTS]
            ring.upsert_slot(
                domain=domain, content=content, slot_type="fact",
                salience=salience, source=source,
            )

    def add_fact(
        self, content: str, domain: str = "", source: str = "ans",
        salience: float = 0.8, metadata: dict[str, Any] | None = None,
    ) -> None:
        if _is_common_domain(domain):
            self.common.add_fact(content=content, domain=domain,
                                 source=source, salience=salience,
                                 metadata=metadata)
        elif self._active_name == "personal":
            self.personal.add_fact(content=content, domain=domain,
                                   source=source, salience=salience,
                                   metadata=metadata)
        else:
            ring = self._rings[RING_PROJECT_FACTS]
            ring.add_slot(WMSlot(
                slot_type="fact", content=content, salience=salience,
                source=source, domain=domain,
                metadata=metadata or {},
            ))

    def upsert_perception(
        self, domain: str, content: str, salience: float = 0.7,
    ) -> None:
        if self._active_name == "personal":
            self.personal.upsert_perception(domain=domain, content=content,
                                            salience=salience)
        else:
            ring = self._rings[RING_PROJECT_FACTS]
            ring.upsert_slot(
                domain=domain, content=content, slot_type="perception",
                salience=salience, source="visual",
            )

    def add_feeling(self, content: str, salience: float = 0.7) -> None:
        # Fixed ring: emotional state is cross-project (differs from DualWM
        # which routes to active workspace — intentional in Cryptex design)
        self.common.add_feeling(content=content, salience=salience)
        # Also write to RING_EMOTIONAL so compose_context renders it
        ring = self._rings.get(RING_EMOTIONAL)
        if ring:
            ring.upsert_slot(
                domain="Feedback.Feeling",
                content=content,
                slot_type="feeling",
                salience=salience,
                source="add_feeling",
            )

    def add_user_state(self, content: str, salience: float = 0.6) -> None:
        self.common.add_user_state(content=content, salience=salience)
        ring = self._rings.get(RING_USER_MODEL)
        if ring:
            ring.upsert_slot(
                domain="User.State",
                content=content,
                slot_type="user_state",
                salience=salience,
                source="add_user_state",
            )

    def upsert_credential(
        self, domain: str, content: str, source: str = "user",
        salience: float = 1.0,
    ) -> None:
        if self._active_name == "personal":
            self.personal.upsert_credential(
                domain=domain, content=content, source=source,
                salience=salience,
            )
        else:
            ring = self._rings[RING_CREDENTIALS]
            ring.upsert_slot(
                domain=domain, content=content, slot_type="credential",
                salience=salience, source=source,
            )

    def get_credentials(self) -> list[WMSlot]:
        common_creds = self.common.get_credentials()
        if self._active_name == "personal":
            return common_creds + self.personal.get_credentials()
        ring = self._rings[RING_CREDENTIALS]
        active_creds = ring.get_slots_by_type("credential")
        # Cross-read credentials from other projects too
        cross = ring.cross_read(max_per_position=2)
        cross_creds = [s for _, s in cross if s.slot_type == "credential"]
        seen_domains = {s.domain for s in active_creds}
        extra = [s for s in cross_creds if s.domain not in seen_domains]
        return common_creds + active_creds + extra

    def remove_by_domain(self, domain: str) -> int:
        n = self.common.remove_by_domain(domain)
        if self._active_name == "personal":
            n += self.personal.remove_by_domain(domain)
        else:
            for ring in self._rings.values():
                if ring.spec.category == RING_PROJECT:
                    n += ring.remove_by_domain(domain)
        return n

    def remove_by_metadata(self, key: str, value: Any) -> int:
        """Remove slots across all stores where metadata[key] == value."""
        n = self.common.remove_by_metadata(key, value)
        n += self.personal.remove_by_metadata(key, value)
        for ring in self._rings.values():
            n += ring.remove_by_metadata(key, value)
        return n

    # --- Instructions ---

    def add_instruction(
        self, content: str, source: str = "task", salience: float = 1.0,
    ) -> None:
        if self._active_name == "personal":
            self.personal.add_instruction(content=content, source=source,
                                          salience=salience)
        else:
            ring = self._rings[RING_INSTRUCTIONS]
            ring.add_slot(WMSlot(
                slot_type="instruction", content=content,
                salience=salience, source=source,
            ))

    def get_instructions(self) -> list[WMSlot]:
        if self._active_name == "personal":
            return self.personal.get_instructions()
        return self._rings[RING_INSTRUCTIONS].get_slots_by_type("instruction")

    def clear_instructions(self) -> None:
        if self._active_name == "personal":
            self.personal.clear_instructions()
        else:
            ring = self._rings[RING_INSTRUCTIONS]
            pos = ring.active_position
            ring.positions[pos] = [
                s for s in ring.positions.get(pos, [])
                if s.slot_type != "instruction"
            ]

    def update_instruction(self, index: int, content: str) -> bool:
        instrs = self.get_instructions()
        if 0 <= index < len(instrs):
            instrs[index].content = content
            return True
        return False

    def delete_instruction(self, index: int) -> bool:
        instrs = self.get_instructions()
        if 0 <= index < len(instrs):
            ring = self._rings[RING_INSTRUCTIONS]
            pos = ring.active_position
            slots = ring.positions.get(pos, [])
            target = instrs[index]
            if target in slots:
                slots.remove(target)
                return True
        return False

    # --- Plan / Todo ---

    def set_plan_position(self, position: str) -> None:
        if self._active_name == "personal":
            self.personal.set_plan_position(position)
        else:
            ring = self._rings[RING_INSTRUCTIONS]
            ring.upsert_slot(
                domain="_plan_position", content=position,
                slot_type="fact", salience=1.0, source="plan",
            )

    def get_plan_position(self) -> str:
        if self._active_name == "personal":
            return self.personal.get_plan_position()
        ring = self._rings[RING_INSTRUCTIONS]
        for s in ring.get_active_slots():
            if s.domain == "_plan_position":
                return s.content
        return ""

    def set_todo_board(self, board: str) -> None:
        if self._active_name == "personal":
            self.personal.set_todo_board(board)
        else:
            ring = self._rings[RING_INSTRUCTIONS]
            ring.upsert_slot(
                domain="_todo_board", content=board,
                slot_type="fact", salience=1.0, source="todo",
            )

    def get_todo_board(self) -> str:
        if self._active_name == "personal":
            return self.personal.get_todo_board()
        ring = self._rings[RING_INSTRUCTIONS]
        for s in ring.get_active_slots():
            if s.domain == "_todo_board":
                return s.content
        return ""

    def set_plan_requirements(self, requirements: str) -> None:
        """Pin authoritative plan requirements in the instructions ring."""
        text = (requirements or "").strip()
        if not text:
            return
        if self._active_name == "personal":
            return
        ring = self._rings[RING_INSTRUCTIONS]
        ring.upsert_slot(
            domain="plan_requirements",
            content=text[:6000],
            slot_type="instruction",
            salience=1.0,
            source="plan",
        )

    def get_plan_requirements(self) -> str:
        if self._active_name == "personal":
            return ""
        ring = self._rings[RING_INSTRUCTIONS]
        for s in ring.get_active_slots():
            if s.domain == "plan_requirements":
                return s.content
        return ""

    def set_plan_tech_stack(self, tech_stack_block: str) -> None:
        """Pin mandatory tech stack context in the instructions ring."""
        text = (tech_stack_block or "").strip()
        if not text:
            return
        if self._active_name == "personal":
            return
        ring = self._rings[RING_INSTRUCTIONS]
        ring.upsert_slot(
            domain="tech_stack",
            content=text[:4000],
            slot_type="instruction",
            salience=1.0,
            source="plan",
        )

    def get_plan_tech_stack(self) -> str:
        if self._active_name == "personal":
            return ""
        ring = self._rings[RING_INSTRUCTIONS]
        for s in ring.get_active_slots():
            if s.domain == "tech_stack":
                return s.content
        return ""

    def clear_plan_context(self) -> None:
        """Drop plan requirements and tech stack instruction slots."""
        if self._active_name == "personal":
            return
        ring = self._rings[RING_INSTRUCTIONS]
        ring.remove_by_domain("plan_requirements")
        ring.remove_by_domain("tech_stack")

    # --- Orchestration ---

    def orch_update_team(
        self, team_id: str, plan_id: str = "",
        status: str = "running",
        members: list[dict[str, Any]] | None = None,
    ) -> None:
        if self._active_name == "personal":
            self.personal.orch_update_team(team_id, plan_id, status, members)
        else:
            self.active.orch_update_team(team_id, plan_id, status, members)
            self._snapshot_orch_to_ring(team_id, status)

    def orch_update_member(self, team_id: str, member_idx: int, **kwargs: Any) -> None:
        if self._active_name == "personal":
            self.personal.orch_update_member(team_id, member_idx, **kwargs)
        else:
            self.active.orch_update_member(team_id, member_idx, **kwargs)

    def orch_record_decision(
        self, action: str, context: str,
        outcome: str = "", team_id: str = "",
        member_idx: int = -1,
    ) -> None:
        if self._active_name == "personal":
            self.personal.orch_record_decision(action, context, outcome,
                                                team_id, member_idx)
        else:
            self.active.orch_record_decision(action, context, outcome,
                                              team_id, member_idx)

    def orch_add_escalation(
        self, team_id: str, member_idx: int, context: str,
    ) -> None:
        if self._active_name == "personal":
            self.personal.orch_add_escalation(team_id, member_idx, context)
        else:
            self.active.orch_add_escalation(team_id, member_idx, context)

    def orch_resolve_escalation(
        self, team_id: str, member_idx: int, outcome: str,
    ) -> None:
        if self._active_name == "personal":
            self.personal.orch_resolve_escalation(team_id, member_idx, outcome)
        else:
            self.active.orch_resolve_escalation(team_id, member_idx, outcome)

    def orch_get_active_teams(self) -> list[OrchTeamState]:
        if self._active_name == "personal":
            return self.personal.orch_get_active_teams()
        return self.active.orch_get_active_teams()

    def orch_get_pending_escalations(self) -> list[OrchDecision]:
        if self._active_name == "personal":
            return self.personal.orch_get_pending_escalations()
        return self.active.orch_get_pending_escalations()

    def orch_set_coordinator_phase(self, phase: str, detail: str = "") -> None:
        if self._active_name == "personal":
            self.personal.orch_set_coordinator_phase(phase, detail)
        else:
            self.active.orch_set_coordinator_phase(phase, detail)
        ring = self._rings.get(RING_ORCHESTRATION)
        if ring is None:
            return
        salience = 1.0 if phase in (
            "awaiting_delegates", "launched_pending_exit", "evaluating_wave",
        ) else 0.85
        content = phase
        if detail:
            content = f"{phase}: {detail[:120]}"
        ring.upsert_slot(
            domain="orch.coordinator_phase",
            content=content,
            slot_type="fact",
            salience=salience,
            source="orchestration",
        )

    def get_orchestration_wake_lines(self) -> list[str]:
        if self._active_name == "personal":
            return self.personal.get_orchestration_wake_lines()
        return self.active.get_orchestration_wake_lines()

    def get_orchestration_wake_hash(self) -> str:
        import hashlib
        payload = "|".join(self.get_orchestration_wake_lines())
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def get_last_checkback_hash(self) -> str:
        ring = self._rings.get(RING_ORCHESTRATION)
        if ring is None:
            return ""
        for slot in ring.get_active_slots():
            if slot.domain == "orch.checkback_hash":
                return slot.content.strip()
        return ""

    def set_last_checkback_hash(self, value: str) -> None:
        ring = self._rings.get(RING_ORCHESTRATION)
        if ring is None:
            return
        ring.upsert_slot(
            domain="orch.checkback_hash",
            content=value[:32],
            slot_type="fact",
            salience=0.5,
            source="orchestration",
        )

    def orch_clear(self) -> None:
        if self._active_name == "personal":
            self.personal.orch_clear()
        else:
            self.active.orch_clear()
            # Clear orch ring for active project
            ring = self._rings.get(RING_ORCHESTRATION)
            if ring:
                ring.positions[ring.active_position] = []

    def _snapshot_orch_to_ring(
        self, team_id: str, status: str,
    ) -> None:
        """Mirror a team status snapshot onto the orchestration ring for persistence."""
        ring = self._rings.get(RING_ORCHESTRATION)
        if ring is None:
            return
        view = self._get_professional_view()
        team = view._orch_teams.get(team_id)
        if team is None:
            return
        lines = [f"Team {team_id} ({status}) plan={team.plan_id}:"]
        for m in team.members:
            _tag = {"done": "DONE", "running": "RUNNING", "failed": "FAILED",
                    "pending": "pending"}.get(m.status, m.status)
            _detail = f"  member={m.index} [{_tag}]: {m.task_summary}"
            if m.iterations_used:
                _detail += f" (iter {m.iterations_used})"
            lines.append(_detail)
        ring.upsert_slot(
            domain=f"orch.team.{team_id}",
            content="\n".join(lines),
            slot_type="fact",
            salience=0.9,
            source="orchestration",
        )

    def set_wake_attention_board(self, content: str) -> None:
        """Compact EM attention surface — pending reviews, active wave, next action."""
        ring = self._rings.get(RING_WAKE_ATTENTION)
        if ring is None or not (content or "").strip():
            return
        ring.upsert_slot(
            domain="wake.attention_board",
            content=content.strip()[:2400],
            slot_type="fact",
            salience=1.0,
            source="wake_coordination",
        )

    def clear_wake_attention_board(self) -> None:
        ring = self._rings.get(RING_WAKE_ATTENTION)
        if ring is None:
            return
        ring.remove_by_domain("wake.attention_board")

    def prune_stale_orchestration_team_slots(self, terminal_team_ids: set[str]) -> int:
        """Drop orch.team.* snapshots for finalized teams (reduces stale WM noise)."""
        ring = self._rings.get(RING_ORCHESTRATION)
        if ring is None or not terminal_team_ids:
            return 0
        removed = 0
        for tid in terminal_team_ids:
            removed += ring.remove_by_domain(f"orch.team.{tid}")
        return removed

    # --- Goals ---

    def add_goal(
        self, level: str, content: str, source: str = "system",
    ) -> None:
        if level == "strategic":
            self.common.add_goal(level=level, content=content, source=source)
        elif self._active_name == "personal":
            self.personal.add_goal(level=level, content=content, source=source)
        else:
            ring = self._rings[RING_TACTICAL_GOALS]
            ring.add_slot(WMSlot(
                slot_type="goal", content=content,
                salience=0.9, source=source, level=level,
            ))

    def get_goals(self) -> list[WMSlot]:
        strategic = self.common.get_goals()
        if self._active_name == "personal":
            return strategic + self.personal.get_goals()
        tactical = self._rings[RING_TACTICAL_GOALS].get_slots_by_type("goal")
        # Also include any strategic goals absorbed into RING_STRATEGIC_GOALS
        # from ANS signals (Goal.* domain) — these bypass common._goal_stack.
        strat_ring = self._rings.get(RING_STRATEGIC_GOALS)
        if strat_ring:
            ring_strats = strat_ring.get_active_slots()
            if ring_strats:
                _existing = {g.content[:80] for g in strategic}
                for rs in ring_strats:
                    _key = rs.content[:80]
                    if _key not in _existing:
                        rs.level = "strategic"
                        strategic.append(rs)
                        _existing.add(_key)
        return strategic + tactical

    def clear_goals(self, level: str | None = None) -> None:
        if level == "strategic" or level is None:
            self.common.clear_goals(level)
        if level != "strategic":
            if self._active_name == "personal":
                self.personal.clear_goals(level)
            else:
                ring = self._rings[RING_TACTICAL_GOALS]
                pos = ring.active_position
                if level:
                    ring.positions[pos] = [
                        s for s in ring.positions.get(pos, [])
                        if not (s.slot_type == "goal" and s.level == level)
                    ]
                else:
                    ring.positions[pos] = [
                        s for s in ring.positions.get(pos, [])
                        if s.slot_type != "goal"
                    ]

    # --- Intentions (always common) ---

    def add_intention(
        self, content: str, trigger: str, source: str = "system",
    ) -> None:
        self.common.add_intention(content=content, trigger=trigger, source=source)

    def check_intentions(self, context: str) -> list[WMSlot]:
        return self.common.check_intentions(context)

    def get_intentions(self) -> list[WMSlot]:
        return self.common.get_intentions()

    # --- Slot add (generic) ---

    def add(self, slot: WMSlot) -> WMSlot | None:
        ring_id = _slot_type_to_ring(slot)
        if ring_id is None or _is_common_domain(slot.domain):
            return self.common.add(slot)
        if self._active_name == "personal":
            return self.personal.add(slot)
        ring = self._rings.get(ring_id)
        if ring is None:
            return self.common.add(slot)
        return ring.add_slot(slot)

    # --- Decay ---

    def decay_salience(self, dt: float = 1.0) -> None:
        self.common.decay_salience(dt)
        if self._active_name != "personal":
            for ring in self._rings.values():
                ring.decay_salience(dt)
        else:
            self.personal.decay_salience(dt)

    # --- Bulk query helpers ---

    def get_goal_stack(self, limit: int | None = None) -> list[WMSlot]:
        merged = self.common._goal_stack + (
            self.personal._goal_stack if self._active_name == "personal"
            else self._rings[RING_TACTICAL_GOALS].get_slots_by_type("goal")
        )
        return merged[:limit] if limit else merged

    def remove_goals_where(
        self, predicate: Callable[[WMSlot], bool],
    ) -> list[WMSlot]:
        r1 = self.common.remove_goals_where(predicate)
        if self._active_name == "personal":
            r2 = self.personal.remove_goals_where(predicate)
        else:
            ring = self._rings[RING_TACTICAL_GOALS]
            pos = ring.active_position
            slots = ring.positions.get(pos, [])
            removed = [s for s in slots if s.slot_type == "goal" and predicate(s)]
            ring.positions[pos] = [s for s in slots if s not in removed]
            r2 = removed
        return r1 + r2

    def mutate_goals(
        self, fn: Callable[[WMSlot], None],
        predicate: Callable[[WMSlot], bool] | None = None,
    ) -> int:
        n = self.common.mutate_goals(fn, predicate)
        if self._active_name == "personal":
            n += self.personal.mutate_goals(fn, predicate)
        else:
            ring = self._rings[RING_TACTICAL_GOALS]
            for s in ring.get_active_slots():
                if s.slot_type == "goal":
                    if predicate is None or predicate(s):
                        fn(s)
                        n += 1
        return n

    def get_prospective(self, limit: int | None = None) -> list[WMSlot]:
        return self.common.get_prospective(limit)

    def remove_intentions_where(
        self, predicate: Callable[[WMSlot], bool],
    ) -> list[WMSlot]:
        return self.common.remove_intentions_where(predicate)

    # --- Metrics ---

    def get_slot_count(self) -> int:
        n = self.common.get_slot_count()
        if self._active_name == "personal":
            n += self.personal.get_slot_count()
        else:
            for ring in self._rings.values():
                if ring.spec.category == RING_PROJECT:
                    n += len(ring.get_active_slots())
        return n

    def get_max_slots(self) -> int:
        return sum(
            r.spec.max_slots_per_position
            for r in self._rings.values()
            if r.spec.category == RING_PROJECT
        )

    def get_avg_salience(self) -> float:
        all_slots: list[WMSlot] = list(self.common._slots)
        if self._active_name == "personal":
            all_slots.extend(self.personal._slots)
        else:
            for ring in self._rings.values():
                if ring.spec.category == RING_PROJECT:
                    all_slots.extend(ring.get_active_slots())
        if not all_slots:
            return 0.0
        return sum(s.salience for s in all_slots) / len(all_slots)

    # ------------------------------------------------------------------
    # Behavioral ring helpers
    # ------------------------------------------------------------------

    def upsert_behavioral(
        self,
        domain: str,
        content: str,
        render_mode: str = "agentic",
        access: str = ACCESS_MALLEABLE,
        consolidation_status: str = "permanent",
        source: str = "system",
        salience: float = 0.9,
    ) -> None:
        """Convenience: upsert a slot on RING_BEHAVIORAL."""
        ring = self._rings.get(RING_BEHAVIORAL)
        if ring is None:
            return
        ring.upsert_slot(
            domain=domain,
            content=content,
            slot_type="behavioral",
            salience=salience,
            source=source,
            metadata={
                "render_mode": render_mode,
                "consolidation_status": consolidation_status,
            },
            access=access,
        )

    def upsert_environment(
        self,
        domain: str,
        content: str,
        source: str = "system",
        salience: float = 0.9,
    ) -> None:
        """Convenience: upsert a slot on RING_ENVIRONMENT."""
        ring = self._rings.get(RING_ENVIRONMENT)
        if ring is None:
            return
        ring.upsert_slot(
            domain=domain,
            content=content,
            slot_type="environment",
            salience=salience,
            source=source,
            access=ACCESS_SYSTEM,
        )

    def upsert_orchestration_slot(
        self,
        domain: str,
        content: str,
        access: str = ACCESS_SESSION,
        source: str = "loop",
        salience: float = 0.95,
    ) -> None:
        """Convenience: upsert a session-level slot on RING_ORCHESTRATION."""
        ring = self._rings.get(RING_ORCHESTRATION)
        if ring is None:
            return
        ring.upsert_slot(
            domain=domain,
            content=content,
            slot_type="fact",
            salience=salience,
            source=source,
            access=access,
        )

    # ------------------------------------------------------------------
    # Cognitive phase detection + ring priority
    # ------------------------------------------------------------------

    # Behavioral slots grouped by function.  Used by _render_behavioral()
    # to show only the most relevant rules in full for the current phase.
    _BEHAVIORAL_GROUPS: dict[str, dict[str, Any]] = {
        "orchestration": {
            "domains": frozenset({
                "coordinator_mode", "team_orchestration", "orchestration_tools",
                "help_requests", "plan_dependency_example", "repair_budget",
                "plan_discipline", "mode_awareness", "autonomous_updates",
                "dmn_discipline",
            }),
            "label": "Orchestration & Delegation",
        },
        "planning": {
            "domains": frozenset({
                "ooda_assessment", "todo_plan_workflow", "procedural_flow",
                "workspace_discipline", "project_directory",
            }),
            "label": "Planning & Workspace",
        },
        "communication": {
            "domains": frozenset({
                "deferred_channel_delivery", "communication_discipline",
                "deferred_work", "contacts_hygiene",
            }),
            "label": "Communication",
        },
        "execution": {
            "domains": frozenset({
                "task_focus", "tool_best_practices", "execution_focus",
                "working_memory_intro", "verification_gate",
            }),
            "label": "Execution & Tools",
        },
        "safety": {
            "domains": frozenset({
                "credentials_handling", "escalate_to_user",
                "credential_hygiene",
            }),
            "label": "Security & Escalation",
        },
    }

    # Phase → ordered list of behavioral groups (first = most relevant).
    # Top 2 groups render in FULL, next groups render COMPRESSED (first
    # sentence only), last group renders as a one-line reminder.
    _PHASE_BEHAVIORAL_ORDER: dict[str, list[str]] = {
        # -- Six-mode entries (matched by AgentMode.value) --
        "chat":          ["communication", "safety", "execution", "planning", "orchestration"],
        "planning":      ["planning", "orchestration", "safety", "communication", "execution"],
        "delegating":    ["orchestration", "planning", "communication", "safety", "execution"],
        "monitoring":    ["orchestration", "communication", "safety", "planning", "execution"],
        "evaluating":    ["execution", "planning", "safety", "orchestration", "communication"],
        "executing":     ["execution", "safety", "planning", "orchestration", "communication"],
        # Responding: user interaction while coordinating — tools + communication first,
        # then orchestration context at the back for awareness.
        "responding":    ["execution", "communication", "safety", "orchestration", "planning"],
        # -- Legacy cognitive-phase entries (backward compat) --
        "communicating": ["communication", "execution", "safety", "orchestration", "planning"],
        "idle":          ["planning", "execution", "orchestration", "communication", "safety"],
        "recovering":    ["orchestration", "planning", "safety", "execution", "communication"],
    }

    # Phase → ring priority overrides.  Higher value = rendered earlier
    # in the prompt (more LLM attention).  Rings not listed get 0.5.
    _PHASE_PRIORITIES: dict[str, dict[str, float]] = {
        # -- Six-mode entries --
        "chat": {
            RING_USER_MODEL: 1.0,
            RING_CHANNELS: 0.95,
            RING_EMOTIONAL: 0.9,
            RING_CONSOLIDATION: 0.85,
            RING_BEHAVIORAL: 0.7,
            RING_INSTRUCTIONS: 0.5,
        },
        "planning": {
            RING_BEHAVIORAL: 1.0,
            RING_INSTRUCTIONS: 0.95,
            RING_TACTICAL_GOALS: 0.9,
            RING_PROJECT_FACTS: 0.85,
            RING_ORCHESTRATION: 0.7,
            RING_USER_MODEL: 0.4,
            RING_EMOTIONAL: 0.2,
            RING_CONSOLIDATION: 0.3,
        },
        "delegating": {
            RING_ORCHESTRATION: 1.0,
            RING_WAKE_ATTENTION: 0.92,
            RING_BEHAVIORAL: 0.95,
            RING_INSTRUCTIONS: 0.9,
            RING_TACTICAL_GOALS: 0.85,
            RING_CREDENTIALS: 0.8,
            RING_PROJECT_FACTS: 0.7,
            RING_USER_MODEL: 0.4,
        },
        "monitoring": {
            RING_WAKE_ATTENTION: 1.0,
            RING_ORCHESTRATION: 0.98,
            RING_CHANNELS: 0.9,
            RING_USER_MODEL: 0.85,
            RING_BEHAVIORAL: 0.7,
            RING_INSTRUCTIONS: 0.6,
            RING_PROJECT_FACTS: 0.5,
        },
        "evaluating": {
            RING_WAKE_ATTENTION: 1.0,
            RING_INSTRUCTIONS: 0.98,
            RING_PROJECT_FACTS: 0.95,
            RING_ORCHESTRATION: 0.9,
            RING_TACTICAL_GOALS: 0.85,
            RING_BEHAVIORAL: 0.8,
            RING_CONSOLIDATION: 0.7,
            RING_CREDENTIALS: 0.6,
        },
        "executing": {
            RING_INSTRUCTIONS: 1.0,
            RING_BEHAVIORAL: 0.95,
            RING_PROJECT_FACTS: 0.9,
            RING_CREDENTIALS: 0.85,
            RING_TOOLS_MCP: 0.8,
            RING_SKILLS: 0.75,
            RING_TACTICAL_GOALS: 0.7,
        },
        # Hybrid profile: coordinator received a direct user request while
        # teams run in the background.  Surfaces personal tools (skills,
        # tools_mcp, calendar credentials) at high priority while keeping
        # orchestration and project context visible at medium priority.
        # Applicable pattern for any coordinator mode, not just monitoring.
        "responding": {
            RING_TOOLS_MCP: 1.0,
            RING_SKILLS: 0.95,
            RING_CREDENTIALS: 0.9,
            RING_BEHAVIORAL: 0.85,
            RING_INSTRUCTIONS: 0.8,
            RING_CHANNELS: 0.75,
            RING_USER_MODEL: 0.7,
            RING_ORCHESTRATION: 0.6,
            RING_PROJECT_FACTS: 0.55,
        },
        # -- Legacy cognitive-phase entries --
        "communicating": {
            RING_USER_MODEL: 1.0,
            RING_CHANNELS: 0.95,
            RING_EMOTIONAL: 0.9,
            RING_BEHAVIORAL: 0.85,
            RING_INSTRUCTIONS: 0.7,
            RING_PROJECT_FACTS: 0.6,
        },
        "idle": {
            RING_STRATEGIC_GOALS: 1.0,
            RING_CONSOLIDATION: 0.9,
            RING_USER_MODEL: 0.8,
            RING_BEHAVIORAL: 0.7,
            RING_EMOTIONAL: 0.6,
        },
        "recovering": {
            RING_SKILLS: 0.98,
            RING_TOOLS_MCP: 0.95,
            RING_BEHAVIORAL: 1.0,
            RING_INSTRUCTIONS: 0.95,
            RING_ORCHESTRATION: 0.9,
            RING_TACTICAL_GOALS: 0.85,
            RING_PROJECT_FACTS: 0.8,
            RING_CONSOLIDATION: 0.7,
            RING_CHANNELS: 0.6,
            RING_USER_MODEL: 0.5,
        },
        # Stuck / error-recovery: surface skills + tools before plan noise.
        "stuck": {
            RING_SKILLS: 1.0,
            RING_TOOLS_MCP: 0.97,
            RING_BEHAVIORAL: 0.93,
            RING_CREDENTIALS: 0.9,
            RING_INSTRUCTIONS: 0.85,
            RING_PROJECT_FACTS: 0.75,
            RING_TACTICAL_GOALS: 0.7,
        },
    }

    def detect_cognitive_phase(self, state: dict[str, Any] | None = None) -> str:
        """Detect the current cognitive phase using a three-layer hybrid.

        Layer 1 — Network State (primary): ECN/SN/DMN activations from
        NetworkDynamics determine the broad category (task-positive,
        attention-driven, or idle).

        Layer 2 — Tool Disambiguation (tiebreaker): recent tool calls
        refine the broad category into a specific phase (planning,
        delegating, monitoring, executing, communicating).

        Layer 3 — Hormone Modifiers: high cortisol overrides to
        "recovering"; elevated oxytocin nudges toward communicating.

        Returns one of: planning, delegating, monitoring, communicating,
        executing, recovering, idle.
        """
        if state is None:
            return self._cognitive_phase or "idle"

        # ── Extract signals ──
        coordinator_mode = state.get("coordinator_mode", False)
        last_tool = state.get("last_tool", "")
        last_action = state.get("last_tool_action", "")
        recent_tools = state.get("recent_tools", [])
        iteration = state.get("iteration", 0)

        ecn = state.get("network_ecn", 0.0)
        sn = state.get("network_sn", 0.0)
        dmn = state.get("network_dmn", 0.0)
        cortisol = state.get("cortisol", 0.0)
        oxytocin = state.get("oxytocin", 0.0)

        _comm_tools = frozenset({
            "whatsapp_send", "telegram_send", "email_send",
            "communicate", "ask_user",
        })
        _monitor_tools = frozenset({"wait", "delegate_status"})
        _delegation_tools = frozenset({"team", "delegate"})
        _delegation_actions = frozenset({
            "create", "launch", "advance", "hint", "rewake",
        })

        # ── Layer 1: Network State (primary signal) ──
        # Classify into broad categories based on network activations.
        # Falls back to tool-only if network values are uninitialized (all 0).
        _has_network = (ecn + sn + dmn) > 0.05
        if _has_network:
            if dmn > 0.5 and ecn < 0.25:
                _net_category = "idle"
            elif sn > ecn and sn > 0.3:
                _net_category = "attention"
            else:
                _net_category = "task_positive"
        else:
            # Network not yet initialized — fall back to tool-only
            if not last_tool and iteration == 0:
                _net_category = "idle"
            else:
                _net_category = "task_positive"

        # ── Layer 2: Tool Disambiguation (tiebreaker) ──

        # Communication tools always win regardless of network state
        if last_tool in _comm_tools:
            return "communicating"

        if _net_category == "idle":
            # Even in idle, check if tools suggest otherwise
            if last_tool == "plan":
                return "planning"
            if last_tool in _delegation_tools:
                return "delegating"
            return "idle"

        if _net_category == "attention":
            # Salience network is dominant — something needs evaluation
            if coordinator_mode:
                if last_tool in _monitor_tools:
                    return "monitoring"
                if last_tool in _delegation_tools:
                    if last_action == "inspect":
                        return "monitoring"
                    return "delegating"
                # Check recent tools for monitoring pattern
                if recent_tools:
                    _mon_count = sum(
                        1 for t in recent_tools[-5:] if t in _monitor_tools
                    )
                    if _mon_count >= 2:
                        return "monitoring"
                return "monitoring"
            # Non-coordinator attention → focused execution
            if last_tool == "plan":
                return "planning"
            return "executing"

        # _net_category == "task_positive"
        if coordinator_mode:
            if last_tool in _delegation_tools:
                if last_action in _delegation_actions:
                    return "delegating"
                if last_action == "inspect":
                    return "monitoring"
                return "delegating"
            if last_tool in _monitor_tools:
                return "monitoring"
            if last_tool == "plan":
                return "planning"
            if iteration <= 5 and not last_tool:
                return "planning"
            # Check if recent pattern is monitoring
            if recent_tools:
                _mon_count = sum(
                    1 for t in recent_tools[-5:] if t in _monitor_tools
                )
                if _mon_count >= 3:
                    return "monitoring"
            return "delegating"

        # Non-coordinator task-positive
        if last_tool == "plan":
            return "planning"
        if last_tool or iteration > 0:
            return "executing"
        return "idle"

    def _apply_hormone_modifier(self, phase: str, state: dict[str, Any]) -> str:
        """Layer 3: hormone-based phase override (called by update_ring_priorities)."""
        cortisol = state.get("cortisol", 0.0)
        oxytocin = state.get("oxytocin", 0.0)

        # High cortisol → recovering (error-recovery, cautious mode)
        # Baseline cortisol is ~0.2; stress pushes it above 0.4
        if cortisol > 0.4 and phase not in ("communicating",):
            return "recovering"

        # Elevated oxytocin nudges toward communicating if already in
        # a social-adjacent phase (idle, monitoring)
        if oxytocin > 0.4 and phase in ("idle", "monitoring"):
            return "communicating"

        return phase

    def update_ring_priorities(self, state: dict[str, Any] | None = None) -> str:
        """Recompute ring priorities based on cognitive phase and active mode.

        Call this BEFORE compose_context() on each iteration.
        Returns the detected cognitive phase.

        When an explicit active_mode is present in state, it takes
        precedence over the cognitive phase detector for priority
        selection — the mode is the authoritative state, while the
        phase is a soft recommendation.
        """
        phase = self.detect_cognitive_phase(state)
        if state is not None:
            phase = self._apply_hormone_modifier(phase, state)
        self._cognitive_phase = phase

        # Use active_mode if available (authoritative over phase detector)
        _priority_key = phase
        if state is not None:
            _active_mode = state.get("active_mode")
            if _active_mode and _active_mode in self._PHASE_PRIORITIES:
                _priority_key = _active_mode

        priorities = {spec.ring_id: 0.5 for spec in RING_REGISTRY}

        phase_overrides = self._PHASE_PRIORITIES.get(_priority_key, {})
        for ring_id, priority in phase_overrides.items():
            priorities[ring_id] = priority

        # Always keep identity and environment at baseline high
        priorities[RING_IDENTITY] = max(priorities.get(RING_IDENTITY, 0), 0.95)
        priorities[RING_ENVIRONMENT] = max(priorities.get(RING_ENVIRONMENT, 0), 0.9)

        # Boost channels ring if user_model has AFK/WhatsApp facts
        user_ring = self._rings.get(RING_USER_MODEL)
        if user_ring:
            for s in user_ring.get_active_slots():
                content_lower = s.content.lower()
                if any(kw in content_lower for kw in
                       ("whatsapp", "telegram", "afk", "keep me posted",
                        "keep me in the loop", "send me")):
                    priorities[RING_CHANNELS] = max(
                        priorities.get(RING_CHANNELS, 0), 0.85,
                    )
                    priorities[RING_USER_MODEL] = max(
                        priorities.get(RING_USER_MODEL, 0), 0.8,
                    )
                    break

        if state is not None:
            _coord_phase = state.get("coordinator_phase", "")
            if _coord_phase in (
                "awaiting_delegates",
                "launched_pending_exit",
                "evaluating_wave",
            ):
                priorities[RING_ORCHESTRATION] = max(
                    priorities.get(RING_ORCHESTRATION, 0), 1.0,
                )
                priorities[RING_WAKE_ATTENTION] = max(
                    priorities.get(RING_WAKE_ATTENTION, 0), 1.0,
                )
                priorities[RING_INSTRUCTIONS] = min(
                    priorities.get(RING_INSTRUCTIONS, 0.5), 0.45,
                )
            if int(state.get("pending_completion_reviews", 0) or 0) > 0:
                priorities[RING_WAKE_ATTENTION] = 1.0
                priorities[RING_ORCHESTRATION] = max(
                    priorities.get(RING_ORCHESTRATION, 0), 0.95,
                )

            if state.get("skill_discovery_boost"):
                for ring_id, priority in self._PHASE_PRIORITIES.get(
                    "stuck", {},
                ).items():
                    priorities[ring_id] = max(
                        priorities.get(ring_id, 0), priority,
                    )

        self._ring_priorities = priorities
        return phase

    def activate_skill_discovery_boost(self, reason: str = "") -> None:
        """Upsert a high-salience skills-ring slot for stall/hint recovery."""
        from nls.agentic.skill_discovery_boost import (
            SKILL_DISCOVERY_PROMPT,
            SKILL_DISCOVERY_SLOT_DOMAIN,
        )

        skills_ring = self._rings.get(RING_SKILLS)
        if skills_ring is None:
            return
        body = SKILL_DISCOVERY_PROMPT
        if reason:
            body = f"{body}\n\nTrigger: {reason[:200]}"
        skills_ring.upsert_slot(
            domain=SKILL_DISCOVERY_SLOT_DOMAIN,
            content=body,
            slot_type="skill",
            salience=1.0,
            source="stall_boost",
        )

    def get_ring_priority(self, ring_id: str) -> float:
        """Get the current priority for a ring (0.0-1.0)."""
        return self._ring_priorities.get(ring_id, 0.5)

    def get_priority_ordered_rings(self) -> list[str]:
        """Return ring IDs sorted by priority (highest first)."""
        return sorted(
            self._ring_priorities.keys(),
            key=lambda rid: self._ring_priorities[rid],
            reverse=True,
        )

    # ------------------------------------------------------------------
    # compose_context — the context compositor
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return len(text) // 4

    # Modes that trigger "agentic" style rendering (instructions, behavioral, etc.)
    _AGENTIC_MODES = frozenset({
        "planning", "delegating", "monitoring", "evaluating", "executing",
        "responding",
        "agentic", "coordinator",  # backward compat
    })

    def compose_context(
        self,
        render_mode: str = "chat",
        token_budget: int = 55_000,
        state: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Compose the full LLM context from ring content.

        Returns a list of message dicts (role='system') ready for
        prepending to the conversation history.

        Ring content is rendered in PRIORITY ORDER — the ring with the
        highest dynamic priority (set by update_ring_priorities) gets
        rendered first in the prompt, where it receives the most LLM
        attention.  This eliminates instruction dilution by ensuring
        the most contextually-relevant directives are always at the top.

        render_mode: "chat" | "planning" | "delegating" | "monitoring"
                     | "evaluating" | "executing" (or legacy "agentic"/"coordinator")
        token_budget: max tokens for composed content
        state: optional loop state dict for conditional rendering
        """
        state = state or {}
        used_tokens = 0
        msg0_parts: list[str] = []  # identity + environment + behavioral + tools
        msg1_parts: list[str] = []  # task state + facts + credentials

        def _budget_remaining() -> int:
            return max(0, token_budget - used_tokens)

        def _append(target: list[str], text: str, force: bool = False) -> bool:
            nonlocal used_tokens
            cost = self._estimate_tokens(text)
            if not force and cost > _budget_remaining():
                return False
            target.append(text)
            used_tokens += cost
            return True

        # =============================================================
        # ANCHOR — ALWAYS RENDER  (identity + environment — stable frame)
        # =============================================================

        identity_ring = self._rings.get(RING_IDENTITY)
        if identity_ring:
            id_slots = identity_ring.get_active_slots()
            if id_slots:
                try:
                    from .identity_renderer import render_identity
                    name_slot = next(
                        (s for s in id_slots if s.domain == "name"), None,
                    )
                    agent_name = name_slot.content if name_slot else ""
                    id_text = render_identity(id_slots, agent_name=agent_name)
                except Exception:
                    id_text = "\n".join(
                        s.content for s in sorted(id_slots, key=lambda x: x.domain)
                    )
                _append(msg0_parts, id_text, force=True)

        env_ring = self._rings.get(RING_ENVIRONMENT)
        if env_ring:
            env_slots = env_ring.get_active_slots()
            if env_slots:
                env_block = "\n".join(s.content for s in env_slots)
                _append(msg0_parts, env_block, force=True)

        # =============================================================
        # PRIORITY-ORDERED RINGS — rendered by dynamic cognitive phase
        # =============================================================
        #
        # Each ring has a render function.  We sort by priority and
        # call them in order.  High-priority rings get full rendering
        # at the TOP of the prompt; low-priority rings get compressed
        # rendering toward the bottom, or are skipped if budget is
        # exhausted.
        #
        # The priority ordering is the key mechanism that solves
        # instruction dilution: in "monitoring" phase, orchestration
        # and communication rules render first; in "planning" phase,
        # dependency and behavioral rules render first.

        behavioral_ring = self._rings.get(RING_BEHAVIORAL)

        def _render_behavioral() -> None:
            """Render behavioral ring with phase-aware slot grouping.

            Instead of dumping all ~20 slots as a flat wall of text, slots
            are grouped by function (orchestration, planning, communication,
            execution, safety).  The cognitive phase determines which groups
            render in FULL (top 2), COMPRESSED (next 2), or SUMMARY (last).
            This keeps the most relevant rules at the top of the prompt
            where LLM attention is highest.
            """
            if not behavioral_ring:
                return

            if render_mode == "chat":
                _chat_rules = [
                    f"- {s.content}"
                    for s in behavioral_ring.get_active_slots()
                    if s.metadata.get("render_mode") == "chat"
                ]
                if _chat_rules:
                    _append(msg0_parts, "## Rules\n" + "\n".join(_chat_rules), force=True)
                return

            all_slots = behavioral_ring.get_active_slots()

            # Build domain → slot mapping
            _domain_map: dict[str, list] = {}
            _ungrouped: list = []
            _grouped_domains: set[str] = set()
            for grp in self._BEHAVIORAL_GROUPS.values():
                _grouped_domains |= grp["domains"]
            for s in all_slots:
                dom = getattr(s, "domain", "")
                if dom in _grouped_domains:
                    _domain_map.setdefault(dom, []).append(s)
                else:
                    _ungrouped.append(s)

            # Determine group render order — prefer render_mode (AgentMode)
            # over cognitive phase since mode is the authoritative state.
            _order_key = render_mode if render_mode in self._PHASE_BEHAVIORAL_ORDER else (
                self._cognitive_phase or "planning"
            )
            group_order = self._PHASE_BEHAVIORAL_ORDER.get(
                _order_key, self._PHASE_BEHAVIORAL_ORDER["planning"],
            )

            full_parts: list[str] = []
            compressed_parts: list[str] = []

            for rank, grp_name in enumerate(group_order):
                grp_def = self._BEHAVIORAL_GROUPS.get(grp_name)
                if not grp_def:
                    continue
                grp_slots = []
                for dom in grp_def["domains"]:
                    grp_slots.extend(_domain_map.get(dom, []))
                if not grp_slots:
                    continue

                if rank < 2:
                    # FULL: render complete content
                    full_parts.append(f"### {grp_def['label']}")
                    for s in grp_slots:
                        full_parts.append(f"- {s.content}")
                elif rank < 4:
                    # COMPRESSED: first sentence only
                    for s in grp_slots:
                        first_line = s.content.split("\n")[0]
                        if len(first_line) > 120:
                            first_line = first_line[:117] + "..."
                        compressed_parts.append(f"- {first_line}")
                # rank >= 4: SUMMARY (just group label, added below)

            # Ungrouped slots always render compressed
            for s in _ungrouped:
                first_line = s.content.split("\n")[0]
                if len(first_line) > 120:
                    first_line = first_line[:117] + "..."
                compressed_parts.append(f"- {first_line}")

            # Summary line for bottom-ranked groups
            summary_groups = [
                self._BEHAVIORAL_GROUPS[gn]["label"]
                for gn in group_order[4:]
                if gn in self._BEHAVIORAL_GROUPS
                and any(_domain_map.get(d) for d in self._BEHAVIORAL_GROUPS[gn]["domains"])
            ]

            output_parts: list[str] = []
            if full_parts:
                _mode_label = render_mode if render_mode in self._AGENTIC_MODES else _order_key
                output_parts.append(f"## Active Rules (mode: {_mode_label})")
                output_parts.extend(full_parts)
            if compressed_parts:
                output_parts.append("### Other Rules (compressed)")
                output_parts.extend(compressed_parts)
            if summary_groups:
                output_parts.append(f"(Also active: {', '.join(summary_groups)})")

            if output_parts:
                _append(msg0_parts, "\n".join(output_parts), force=True)

        def _render_instructions() -> None:
            """Render task instructions + plan position + todo board."""
            if render_mode not in self._AGENTIC_MODES:
                return
            instrs = self.get_instructions()
            plan_pos = self.get_plan_position()
            todo_board = self.get_todo_board()
            task_parts: list[str] = []
            if instrs:
                task_parts.append("Task Instructions:")
                for instr in instrs:
                    task_parts.append(f"  \u25b6 {instr.content}")
            if plan_pos:
                if _budget_remaining() > 500:
                    task_parts.append(plan_pos)
                else:
                    lines = plan_pos.split("\n")
                    task_parts.append("\n".join(lines[:10]))
            if todo_board:
                task_parts.append("Task Board:")
                task_parts.append(todo_board)
            if task_parts:
                _append(msg1_parts, "\n".join(task_parts))

        def _render_wake_attention() -> None:
            """Render batched wake attention — highest signal for EM turns."""
            if render_mode not in self._AGENTIC_MODES:
                return
            wake_ring = self._rings.get(RING_WAKE_ATTENTION)
            if not wake_ring or _budget_remaining() < 80:
                return
            for s in wake_ring.get_active_slots():
                if s.domain == "wake.attention_board" and s.content.strip():
                    _append(
                        msg1_parts,
                        "[WAKE ATTENTION — act on this, ignore stale chat history]\n"
                        + s.content.strip(),
                        force=True,
                    )
                    break

        def _render_orchestration() -> None:
            """Render orchestration state (team/delegate status)."""
            if render_mode not in self._AGENTIC_MODES:
                return
            orch_ring = self._rings.get(RING_ORCHESTRATION)
            orch_parts: list[str] = []
            orch_view_block = self.active._render_orch_block()
            if orch_view_block:
                orch_parts.append(orch_view_block)
            if orch_ring:
                for s in orch_ring.get_active_slots():
                    orch_parts.append(s.content)
            if orch_parts:
                _append(msg1_parts, "\n".join(orch_parts))

        def _render_goals() -> None:
            """Render strategic + tactical goals."""
            if render_mode not in self._AGENTIC_MODES:
                return
            all_goals = self.get_goals()
            if all_goals:
                goal_lines: list[str] = ["Goals:"]
                for g in all_goals:
                    marker = (
                        "\u2605" if g.level == "strategic"
                        else ("\u25b8" if g.level == "tactical" else "\u25cb")
                    )
                    goal_lines.append(f"  {marker} [{g.level}] {g.content}")
                _append(msg1_parts, "\n".join(goal_lines))

        def _render_project_facts() -> None:
            # Project facts are agentic/task context — not useful for casual chat.
            if render_mode == "chat":
                return
            facts_ring = self._rings.get(RING_PROJECT_FACTS)
            if not facts_ring or _budget_remaining() < 200:
                return
            fact_slots = sorted(
                facts_ring.get_active_slots(),
                key=lambda s: s.salience, reverse=True,
            )
            if fact_slots:
                cap = 15 if _budget_remaining() > 1000 else 5
                fact_lines: list[str] = ["Project Knowledge:"]
                for s in fact_slots[:cap]:
                    domain_tag = f" ({s.domain})" if s.domain else ""
                    fact_lines.append(f"  \u2022 {s.content}{domain_tag}")
                _append(msg1_parts, "\n".join(fact_lines))

        def _render_credentials() -> None:
            if _budget_remaining() < 100:
                return
            creds = self.get_credentials()
            if creds:
                cred_lines: list[str] = ["Credentials:"]
                seen: set[str] = set()
                for c in creds:
                    if c.domain in seen:
                        continue
                    seen.add(c.domain)
                    cred_lines.append(f"  \u2022 {c.content} ({c.domain})")
                _append(msg1_parts, "\n".join(cred_lines))

        def _render_skills() -> None:
            # Skills list is not useful for casual conversational turns.
            if render_mode == "chat":
                return
            skills_ring = self._rings.get(RING_SKILLS)
            if not skills_ring or _budget_remaining() < 200:
                return
            skill_slots = skills_ring.get_active_slots()
            if not skill_slots:
                return

            _boost = bool(state.get("skill_discovery_boost"))
            _target = msg0_parts if _boost else msg1_parts
            _cap = 10 if _boost else 6
            _header = (
                "⚠ SKILLS & TOOL DISCOVERY — read first (stuck recovery):"
                if _boost
                else "Available Skills:"
            )
            skill_lines: list[str] = [_header]
            sorted_slots = sorted(
                skill_slots,
                key=lambda s: (
                    0 if s.domain == "skill.discovery_boost" else 1,
                    -s.salience,
                ),
            )
            for s in sorted_slots[:_cap]:
                line = f"  ⚙ {s.content}"
                if _boost:
                    full = (s.metadata or {}).get("full_instructions", "")
                    if full:
                        excerpt = full[:500].strip()
                        if len(full) > 500:
                            excerpt += "…"
                        line += f"\n    Instructions: {excerpt}"
                skill_lines.append(line)
            if _boost:
                skill_lines.append(
                    "  → clawhub(action='search', query='...') · "
                    "discover_tools(query='...')"
                )
            _append(_target, "\n".join(skill_lines), force=_boost)

        def _render_tools_mcp() -> None:
            # MCP tool descriptions are only relevant for agentic/task modes.
            if render_mode == "chat":
                return
            tools_ring = self._rings.get(RING_TOOLS_MCP)
            if not tools_ring or _budget_remaining() < 200:
                return
            tool_slots = tools_ring.get_active_slots()
            if not tool_slots:
                return
            _boost = bool(state.get("skill_discovery_boost"))
            tool_lines = [s.content for s in tool_slots]
            if _boost:
                _append(
                    msg0_parts,
                    "Available tools (search with discover_tools if missing):\n"
                    + "\n".join(tool_lines[:12]),
                    force=True,
                )
            else:
                _append(msg0_parts, "\n".join(tool_lines))

        def _render_channels() -> None:
            if _budget_remaining() < 100:
                return
            channels_ring = self._rings.get(RING_CHANNELS)
            if channels_ring:
                ch_slots = channels_ring.get_active_slots()
                if ch_slots:
                    ch_lines: list[str] = ["Active Channels:"]
                    for s in ch_slots:
                        ch_lines.append(f"  \U0001f4e1 {s.content}")
                    _append(msg1_parts, "\n".join(ch_lines))

        def _render_consolidation() -> None:
            if _budget_remaining() < 100:
                return
            parts: list[str] = []
            consol = self.get_consolidation_context()
            if consol and _budget_remaining() > 500:
                parts.append(consol)
            # Also include ring-absorbed consolidation facts (from ANS
            # Agent.Knowledge.* / Agent.Skill.* signals) that bypass the
            # active WM view.
            consol_ring = self._rings.get(RING_CONSOLIDATION)
            if consol_ring:
                ring_slots = consol_ring.get_active_slots()
                for s in ring_slots[:5]:
                    if s.content and s.content not in (consol or ""):
                        parts.append(f"  \u2022 {s.content}")
            if parts:
                _append(msg1_parts, "Session Consolidation:\n" + "\n".join(parts))

        def _render_emotional() -> None:
            if _budget_remaining() < 100:
                return
            emotional_ring = self._rings.get(RING_EMOTIONAL)
            if emotional_ring:
                em_slots = emotional_ring.get_active_slots()
                if em_slots:
                    em_text = "; ".join(s.content for s in em_slots[:3])
                    _append(msg1_parts, f"Emotional State: {em_text}")

        def _render_user_model() -> None:
            if _budget_remaining() < 100:
                return
            um_items: list[str] = []
            _seen: set[str] = set()
            # Ring-absorbed user facts (from ANS User.* signals)
            user_ring = self._rings.get(RING_USER_MODEL)
            if user_ring:
                for s in user_ring.get_active_slots()[:5]:
                    if s.content and s.content[:60] not in _seen:
                        um_items.append(s.content)
                        _seen.add(s.content[:60])
            # Common-stored User.* facts (from direct upsert_fact calls)
            for s in self.common._slots:
                if (s.domain and s.domain.startswith("User.")
                        and s.content and s.content[:60] not in _seen):
                    um_items.append(s.content)
                    _seen.add(s.content[:60])
                    if len(um_items) >= 8:
                        break
            if um_items:
                _append(msg1_parts, f"User Model: {'; '.join(um_items)}")

        # Map ring IDs to render functions
        _ring_renderers: dict[str, Any] = {
            RING_BEHAVIORAL: _render_behavioral,
            RING_INSTRUCTIONS: _render_instructions,
            RING_WAKE_ATTENTION: _render_wake_attention,
            RING_ORCHESTRATION: _render_orchestration,
            RING_TACTICAL_GOALS: _render_goals,
            RING_STRATEGIC_GOALS: _render_goals,
            RING_PROJECT_FACTS: _render_project_facts,
            RING_CREDENTIALS: _render_credentials,
            RING_SKILLS: _render_skills,
            RING_TOOLS_MCP: _render_tools_mcp,
            RING_CHANNELS: _render_channels,
            RING_CONSOLIDATION: _render_consolidation,
            RING_EMOTIONAL: _render_emotional,
            RING_USER_MODEL: _render_user_model,
        }

        # Render rings in priority order (highest first → top of prompt)
        _rendered: set[str] = set()
        for ring_id in self.get_priority_ordered_rings():
            if ring_id in _rendered:
                continue
            if ring_id in (RING_IDENTITY, RING_ENVIRONMENT):
                continue  # already rendered as anchors
            renderer = _ring_renderers.get(ring_id)
            if renderer:
                # Goals share a renderer — mark both as rendered
                if ring_id in (RING_TACTICAL_GOALS, RING_STRATEGIC_GOALS):
                    if RING_TACTICAL_GOALS in _rendered or RING_STRATEGIC_GOALS in _rendered:
                        continue
                    _rendered.add(RING_TACTICAL_GOALS)
                    _rendered.add(RING_STRATEGIC_GOALS)
                else:
                    _rendered.add(ring_id)
                renderer()

        # =============================================================
        # PERIPHERAL — cross-reads and legacy slots (only if budget)
        # =============================================================

        if _budget_remaining() > 50:
            peripheral: list[str] = []
            if render_mode != "chat":  # cross-project noise not useful for casual chat
                for ring in self._rings.values():
                    if ring.spec.category == RING_PROJECT and ring.spec.allow_cross_read:
                        cross = ring.cross_read(max_per_position=1)
                        for pos_id, slot in cross:
                            if slot.domain not in ("_plan_position", "_todo_board"):
                                peripheral.append(f"  \u25e6 [{pos_id}] {slot.content[:80]}")
            if peripheral:
                _append(msg1_parts, "Other Projects (peripheral):\n" + "\n".join(peripheral[:5]))

        common_extras: list[str] = []
        # In chat mode, cap common slots to small/known facts (skip large task-context blobs).
        _chat_extras_limit = 3 if render_mode == "chat" else 10
        _chat_content_cap = 120 if render_mode == "chat" else None
        for s in self.common._slots:
            if _is_consolidation_slot(s):
                continue
            if s.slot_type in ("feeling", "user_state"):
                continue
            _content = s.content
            if _chat_content_cap and len(_content) > _chat_content_cap:
                continue  # skip large blobs in chat mode
            common_extras.append(f"  \u2022 {_content}" + (f" ({s.domain})" if s.domain else ""))
        if common_extras and _budget_remaining() > 100:
            _append(msg1_parts, "General Knowledge:\n" + "\n".join(common_extras[:_chat_extras_limit]))

        if self.common._prospective and _budget_remaining() > 50:
            intn_lines: list[str] = ["Pending Intentions:"]
            for intn in self.common._prospective[:3]:
                intn_lines.append(f"  \u23f0 when \"{intn.trigger}\" \u2192 {intn.content}")
            _append(msg1_parts, "\n".join(intn_lines))

        # =============================================================
        # Assemble messages
        # =============================================================

        messages: list[dict[str, str]] = []

        if msg0_parts:
            messages.append({
                "role": "system",
                "content": "\n\n".join(msg0_parts),
            })

        if msg1_parts:
            _phase_tag = f" | phase: {self._cognitive_phase}" if self._cognitive_phase else ""
            messages.append({
                "role": "system",
                "content": f"[WORKING MEMORY — active cognitive workspace{_phase_tag}]\n"
                           + "\n\n".join(msg1_parts)
                           + "\n[END WORKING MEMORY]",
            })

        return messages

    # ------------------------------------------------------------------
    # Context string — renders the current cryptex combination
    # (legacy — kept for backward compat; compose_context() is preferred)
    # ------------------------------------------------------------------

    def to_context_string(self, render_context: str = "") -> str:
        """Render the current cryptex combination as the WM context block.

        Parameters
        ----------
        render_context : str
            Source hint (e.g. ``"user:channel:whatsapp"``, ``"orchestrator"``).
            Determines which rings get full rendering (tier 1) vs. compact
            summary lines (tier 2) to save context-window tokens.
        """
        if self._active_name == "personal":
            common_ctx = self.common.to_context_string()
            personal_ctx = self.personal.to_context_string()
            if not common_ctx and not personal_ctx:
                return ""
            parts = ["[WORKING MEMORY — your active cognitive workspace]"]
            if common_ctx:
                parts.append(common_ctx)
            if personal_ctx:
                parts.append(personal_ctx)
            parts.append("[END WORKING MEMORY]")
            return "\n".join(parts)

        parts: list[str] = [
            "[WORKING MEMORY — your active cognitive workspace]",
        ]

        # Instructions from active project (Ring 6)
        instrs = self.get_instructions()
        if instrs:
            parts.append("Task Instructions:")
            for instr in instrs:
                parts.append(f"  ▶ {instr.content}")

        # Plan position
        plan_pos = self.get_plan_position()
        if plan_pos:
            parts.append(plan_pos)

        # Todo board
        todo_board = self.get_todo_board()
        if todo_board:
            parts.append("Task Board:")
            parts.append(todo_board)

        # Orchestration state (from active WorkingMemory view)
        orch_block = self.active._render_orch_block()
        if orch_block:
            parts.append(orch_block)

        # Goals: strategic from common + tactical from ring
        all_goals = self.get_goals()
        if all_goals:
            parts.append("Goals:")
            for g in all_goals:
                marker = (
                    "★" if g.level == "strategic"
                    else ("▸" if g.level == "tactical" else "○")
                )
                parts.append(f"  {marker} [{g.level}] {g.content}")

        # Session Consolidation (reads from the correct workspace)
        consol_ctx = self.get_consolidation_context()
        if consol_ctx:
            parts.append("Session Consolidation (long-term context):")
            parts.append(consol_ctx)

        # Active slots from project rings (facts, credentials, perceptions)
        project_slots: list[WMSlot] = []
        for ring in self._rings.values():
            if ring.spec.category == RING_PROJECT:
                for s in ring.get_active_slots():
                    if s.slot_type in ("instruction", "goal"):
                        continue  # already rendered
                    if s.domain in ("_plan_position", "_todo_board"):
                        continue  # special keys
                    project_slots.append(s)

        # Common slots (identity, user state, feelings)
        common_slots = [
            s for s in self.common._slots
            if not _is_consolidation_slot(s)
        ]

        merged = sorted(
            common_slots + project_slots,
            key=lambda s: s.salience, reverse=True,
        )[:self.common.cfg.attention_window_size + 5]

        by_type: dict[str, list[WMSlot]] = {}
        for slot in merged:
            if slot.slot_type == "goal":
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
            "skill": "Loaded Skills",
        }
        for stype, label in type_labels.items():
            slots = by_type.get(stype, [])
            if slots:
                parts.append(f"{label}:")
                for s in slots:
                    domain_tag = f" ({s.domain})" if s.domain else ""
                    parts.append(f"  • {s.content}{domain_tag}")

        # Extra credentials via cross-read
        rendered_cred_domains = {s.domain for s in by_type.get("credential", [])}
        for ring in self._rings.values():
            if ring.spec.ring_id == RING_CREDENTIALS:
                cross = ring.cross_read(max_per_position=2)
                extra = [
                    (pos, s) for pos, s in cross
                    if s.domain not in rendered_cred_domains
                ]
                if extra:
                    if "credential" not in by_type:
                        parts.append("Project Credentials:")
                    for pos, s in extra:
                        parts.append(f"  • {s.content} ({s.domain}) [from: {pos}]")

        # Peripheral glimpse: top-1 from non-active project positions
        peripheral: list[str] = []
        for ring in self._rings.values():
            if ring.spec.category == RING_PROJECT and ring.spec.allow_cross_read:
                cross = ring.cross_read(max_per_position=1)
                for pos_id, slot in cross:
                    if slot.domain not in ("_plan_position", "_todo_board"):
                        peripheral.append(
                            f"  ◦ [{pos_id}] {slot.content[:80]}"
                        )
        if peripheral:
            parts.append("Other Projects (peripheral):")
            parts.extend(peripheral[:5])

        # Skills ring summary
        skills_ring = self._rings.get(RING_SKILLS)
        if skills_ring:
            skill_slots = skills_ring.get_active_slots()
            if skill_slots:
                parts.append("Available Skills:")
                for s in skill_slots[:6]:
                    parts.append(f"  ⚙ {s.content}")

        # Channels ring
        channels_ring = self._rings.get(RING_CHANNELS)
        if channels_ring:
            ch_slots = channels_ring.get_active_slots()
            if ch_slots:
                parts.append("Active Channels:")
                for s in ch_slots:
                    parts.append(f"  📡 {s.content}")

        # Intentions from common
        if self.common._prospective:
            parts.append("Pending Intentions:")
            for intn in self.common._prospective[:3]:
                parts.append(
                    f"  ⏰ when \"{intn.trigger}\" → {intn.content}"
                )

        # Tier-2 ring summaries: rings with active slots that were NOT
        # explicitly rendered by the sections above get a compact digest.
        # Every ring rendered above is listed here to prevent double-rendering.
        _fully_rendered_rings = {
            RING_IDENTITY, RING_EMOTIONAL, RING_CONSOLIDATION,
            RING_STRATEGIC_GOALS, RING_ORCHESTRATION,
            RING_INSTRUCTIONS, RING_TACTICAL_GOALS,
            RING_USER_MODEL,
            RING_PROJECT_FACTS, RING_CREDENTIALS,
            RING_SKILLS, RING_CHANNELS,
            RING_BEHAVIORAL, RING_ENVIRONMENT,
        }

        summaries: list[str] = []
        for ring in self._rings.values():
            if ring.spec.ring_id in _fully_rendered_rings:
                continue
            if not ring.get_active_slots():
                continue
            line = ring.to_summary_line()
            if line:
                summaries.append(line)
        if summaries:
            parts.append("Other Context (use wm_tool to expand):")
            parts.extend(summaries)

        parts.append("[END WORKING MEMORY]")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Attention window
    # ------------------------------------------------------------------

    def get_attention_window(self, k: int | None = None) -> list[WMSlot]:
        k = k or self.common.cfg.attention_window_size
        merged: list[WMSlot] = list(self.common._slots)
        if self._active_name == "personal":
            merged.extend(self.personal._slots)
        else:
            for ring in self._rings.values():
                if ring.spec.category == RING_PROJECT:
                    merged.extend(ring.get_active_slots())
        merged.sort(key=lambda s: s.salience, reverse=True)
        return merged[:k]

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------

    def consolidate_session(self, summary: str) -> None:
        """Write session consolidation directly to the RING_CONSOLIDATION ring.

        For personal workspace, delegates to ``self.personal`` (a real
        WorkingMemory whose slots survive save).  For professional, writes
        to the dedicated consolidation ring so data isn't lost when
        ``_sync_rings_to_view()`` rebuilds the transient professional view.
        """
        if not summary:
            return

        if self._active_name == "personal":
            self.personal.consolidate_session(summary)
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
            elif ll.startswith("[project:"):
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

        ring = self._rings[RING_CONSOLIDATION]
        for domain, new_text in [
            (CONSOLIDATION_DOMAINS[0], progress_new.strip()),
            (CONSOLIDATION_DOMAINS[1], knowledge_new.strip()),
            (CONSOLIDATION_DOMAINS[2], context_new.strip()),
        ]:
            if not new_text:
                continue
            existing = ""
            for s in ring.get_active_slots():
                if s.domain == domain:
                    existing = s.content
                    break

            merged = (
                (existing + " | " + new_text).strip(" |")
                if existing else new_text
            )
            if len(merged) > _MAX_CONSOLIDATION_CHARS:
                merged = merged[-_MAX_CONSOLIDATION_CHARS:]
                cut = merged.find(" | ")
                if cut != -1 and cut < 200:
                    merged = merged[cut + 3:]

            ring.upsert_slot(
                domain=domain,
                content=merged,
                slot_type="fact",
                salience=1.0,
                source="consolidation",
            )

    def get_consolidation_context(self) -> str:
        """Read consolidation context from the RING_CONSOLIDATION ring."""
        if self._active_name == "personal":
            return self.personal.get_consolidation_context()

        ring = self._rings.get(RING_CONSOLIDATION)
        if not ring:
            return ""
        parts: list[str] = []
        for domain in CONSOLIDATION_DOMAINS:
            for s in ring.get_active_slots():
                if s.domain == domain:
                    label = domain.split(".")[-1]
                    parts.append(f"  [{label}] {s.content}")
                    break
        return "\n".join(parts) if parts else ""

    def absorb_delegate_digest(self, digest: dict) -> None:
        """Merge a sub-agent's compressed knowledge digest into the
        orchestrator's rings.

        Called when a delegate completes — the digest (produced by
        ``SubCryptex.compress_to_digest``) carries structured knowledge
        that the orchestrator should retain.
        """
        if not digest or not isinstance(digest, dict):
            return

        facts_ring = self._rings.get(RING_PROJECT_FACTS)
        consol_ring = self._rings.get(RING_CONSOLIDATION)
        goals_ring = self._rings.get(RING_TACTICAL_GOALS)

        # knowledge_gained → project facts
        for fact in digest.get("knowledge_gained", [])[:8]:
            if facts_ring and fact:
                facts_ring.upsert_slot(
                    domain=f"DelegateKnowledge:{fact[:30]}",
                    content=str(fact)[:300],
                    salience=0.75,
                    source="delegate_digest",
                )

        # files_created / files_modified → project facts inventory
        files = (
            digest.get("files_created", [])
            + digest.get("files_modified", [])
        )
        if files and facts_ring:
            file_list = ", ".join(str(f) for f in files[:20])
            facts_ring.upsert_slot(
                domain="DelegateFiles",
                content=f"Delegate touched: {file_list}",
                salience=0.7,
                source="delegate_digest",
            )

        # decisions → consolidation
        decisions = digest.get("decisions", [])
        if decisions and consol_ring:
            dec_text = " | ".join(str(d)[:100] for d in decisions[:5])
            consol_ring.upsert_slot(
                domain="DelegateDecisions",
                content=dec_text,
                salience=0.7,
                source="delegate_digest",
            )

        # blockers → tactical goals (as warnings)
        for blocker in digest.get("blockers", [])[:3]:
            if goals_ring and blocker:
                goals_ring.upsert_slot(
                    domain=f"DelegateBlocker:{str(blocker)[:25]}",
                    content=f"BLOCKER from delegate: {blocker}",
                    slot_type="goal",
                    salience=0.85,
                    source="delegate_digest",
                    level="tactical",
                )

        # task_summary → consolidation progress
        task_summary = digest.get("task_summary", "")
        if task_summary and consol_ring:
            consol_ring.upsert_slot(
                domain="DelegateTaskSummary",
                content=str(task_summary)[:300],
                salience=0.7,
                source="delegate_digest",
            )

    def absorb_compaction(self, anchor: Any) -> None:
        """Merge CompactionAnchor data from loop compaction into Cryptex rings."""
        if anchor is None:
            return

        if self._active_name == "personal":
            summary_parts: list[str] = []
            goal = getattr(anchor, "goal", "")
            if goal:
                summary_parts.append(f"[Progress] Goal: {goal}")
            done = getattr(anchor, "progress_done", [])
            if done:
                summary_parts.append(
                    "[Progress] Done: " + " | ".join(str(d) for d in done[-10:])
                )
            pending = getattr(anchor, "progress_pending", [])
            if pending:
                summary_parts.append(
                    "[Progress] Pending: " + " | ".join(str(p) for p in pending[-5:])
                )
            decisions = getattr(anchor, "decisions", [])
            if decisions:
                summary_parts.append(
                    "[Knowledge] Decisions: "
                    + " | ".join(str(d) for d in decisions[-5:])
                )
            if summary_parts:
                self.consolidate_session("\n".join(summary_parts))
            return

        consol_ring = self._rings.get(RING_CONSOLIDATION)
        facts_ring = self._rings.get(RING_PROJECT_FACTS)
        goals_ring = self._rings.get(RING_TACTICAL_GOALS)

        goal = getattr(anchor, "goal", "")
        done = getattr(anchor, "progress_done", [])
        pending = getattr(anchor, "progress_pending", [])
        decisions = getattr(anchor, "decisions", [])
        files_mod = getattr(anchor, "files_modified", [])
        files_rd = getattr(anchor, "files_read", [])
        next_steps = getattr(anchor, "next_steps", [])
        comms = getattr(anchor, "communications_sent", [])

        if goal and consol_ring:
            consol_ring.upsert_slot(
                domain="CompactionGoal",
                content=str(goal)[:400],
                salience=0.85,
                source="compaction",
            )
        if done and consol_ring:
            consol_ring.upsert_slot(
                domain="CompactionDone",
                content="Completed: " + " | ".join(str(d) for d in done[-12:]),
                salience=0.85,
                source="compaction",
            )
        if pending and goals_ring:
            goals_ring.upsert_slot(
                domain="CompactionPending",
                content="Pending: " + " | ".join(str(p) for p in pending[-8:]),
                slot_type="goal",
                salience=0.8,
                source="compaction",
                level="tactical",
            )
        if decisions and consol_ring:
            consol_ring.upsert_slot(
                domain="CompactionDecisions",
                content="Decisions: " + " | ".join(str(d) for d in decisions[-8:]),
                salience=0.8,
                source="compaction",
            )
        if next_steps and goals_ring:
            goals_ring.upsert_slot(
                domain="CompactionNextSteps",
                content="Next: " + " | ".join(str(s) for s in next_steps[-5:]),
                slot_type="goal",
                salience=0.75,
                source="compaction",
                level="tactical",
            )
        if comms and consol_ring:
            consol_ring.upsert_slot(
                domain="CompactionComms",
                content="Sent: " + " | ".join(str(c) for c in comms[-8:]),
                salience=0.9,
                source="compaction",
            )
        if files_rd and facts_ring:
            facts_ring.upsert_slot(
                domain="CompactionFilesRead",
                content="Read: " + ", ".join(str(f) for f in files_rd[-20:]),
                salience=0.7,
                source="compaction",
            )
        if files_mod and facts_ring:
            facts_ring.upsert_slot(
                domain="CompactionFilesModified",
                content="Modified: " + ", ".join(str(f) for f in files_mod[-20:]),
                salience=0.75,
                source="compaction",
            )

    def make_compaction_hook(self) -> Callable[[Any], None]:
        """Return an ``on_compaction`` hook for orchestrator agentic loops."""
        cryptex = self

        def _on_compaction(anchor: Any) -> None:
            cryptex.absorb_compaction(anchor)

        return _on_compaction

    def replace_consolidation(self, compounded: str) -> None:
        """Replace consolidation ring content with a compounded narrative.

        Parses the same ``[Label] content`` format that
        ``get_consolidation_context`` produces and writes each bucket
        as a full replacement (not append).
        """
        if self._active_name == "personal":
            if hasattr(self.personal, "replace_consolidation"):
                self.personal.replace_consolidation(compounded)
            return

        ring = self._rings.get(RING_CONSOLIDATION)
        if not ring:
            return

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
            ring.upsert_slot(
                domain=domain,
                content=text,
                slot_type="fact",
                salience=1.0,
                source="consolidation",
            )

    # ------------------------------------------------------------------
    # Status / Summary
    # ------------------------------------------------------------------

    def _get_inactive_workspace_summary(self) -> dict[str, Any]:
        """Return summary keys for the inactive workspace (personal vs professional)."""
        other = (
            self.personal if self._active_name == "professional"
            else self._get_professional_view()
        )
        other_name = (
            "personal" if self._active_name == "professional"
            else "professional"
        )
        other_summary = other.get_summary()
        result: dict[str, Any] = {}
        if other_summary.get("slot_count", 0) > 0:
            result[f"{other_name}_slots"] = other_summary["slots"]
            result[f"{other_name}_slot_count"] = other_summary["slot_count"]
            result[f"{other_name}_goals"] = other_summary.get("goals", [])
        return result

    def get_summary(self) -> dict[str, Any]:
        """Return summary dict compatible with legacy WorkingMemoryStatus
        interface, augmented with cryptex ring data."""
        # Collect all visible slots for the legacy fields
        all_slots: list[WMSlot] = list(self.common._slots)
        all_goals = self.get_goals()
        instrs = self.get_instructions()

        if self._active_name == "personal":
            all_slots.extend(self.personal._slots)
        else:
            for ring in self._rings.values():
                if ring.spec.category == RING_PROJECT:
                    for s in ring.get_active_slots():
                        if s.slot_type not in ("instruction", "goal"):
                            if s.domain not in ("_plan_position", "_todo_board"):
                                all_slots.append(s)

        ring_status = {
            r.spec.ring_id: r.get_status(include_slots=True)
            for r in self._rings.values()
        }

        orch_teams: list[dict] = []
        orch_escalations: list[dict] = []
        orch_decision_count = 0
        view = self._get_professional_view()
        for tid, team in view._orch_teams.items():
            orch_teams.append(team.to_dict())
        for esc in view._orch_escalations:
            orch_escalations.append(esc.to_dict() if hasattr(esc, "to_dict") else {})
        orch_decision_count = len(view._orch_decisions)

        return {
            # Legacy fields for backward compat
            "slot_count": len(all_slots),
            "max_slots": self.get_max_slots(),
            "goal_count": len(all_goals),
            "intention_count": len(self.common._prospective),
            "instruction_count": len(instrs),
            "slots": [
                {
                    "type": s.slot_type,
                    "content": s.content[:160],
                    "salience": round(s.salience, 2),
                    "domain": s.domain,
                }
                for s in sorted(all_slots, key=lambda x: x.salience, reverse=True)[:20]
            ],
            "goals": [
                {"level": g.level, "content": g.content[:160]}
                for g in all_goals
            ],
            "intentions": [
                {"trigger": i.trigger, "content": i.content[:160]}
                for i in self.common._prospective
            ],
            "instructions": [
                {
                    "content": i.content[:200],
                    "source": i.source,
                    "salience": round(i.salience, 2),
                }
                for i in instrs
            ],
            "active_workspace": self._active_name,
            "common_slot_count": len(self.common._slots),
            "plan_position": self.get_plan_position() or None,
            "consolidation_context": self.get_consolidation_context() or "",
            "orch_teams": orch_teams,
            "orch_escalations": orch_escalations,
            "orch_decision_count": orch_decision_count,
            # Inactive workspace slots for full visibility
            **self._get_inactive_workspace_summary(),
            # Cryptex-specific fields
            "active_project": self._active_project,
            "active_domain": self._active_domain,
            "projects": self.get_all_project_ids(),
            "domains": self.get_all_domain_ids(),
            "rings": ring_status,
            "token_budget": {
                "limit": 55_000,
                "estimated_used": sum(
                    len(s.content) // 4
                    for ring in self._rings.values()
                    for s in ring.get_active_slots()
                ),
            },
        }

    def get_cryptex_snapshot(self) -> str:
        """Compact human-readable snapshot for the WM tool."""
        lines = [
            f"Cryptex State: project={self._active_project}, domain={self._active_domain}",
            f"Projects: {', '.join(self.get_all_project_ids()) or 'none'}",
            f"Domains: {', '.join(self.get_all_domain_ids()) or 'none'}",
        ]
        for ring in self._rings.values():
            total = sum(len(s) for s in ring.positions.values())
            active_count = len(ring.get_active_slots())
            lines.append(
                f"  {ring.spec.display_name}: "
                f"{active_count} active / {total} total "
                f"[pos={ring.active_position}] "
                f"({', '.join(ring.position_ids)})"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Genesis population
    # ------------------------------------------------------------------

    def populate_genesis_identity(
        self,
        agent_name: str = "",
        enabled_tools_list: str = "",
        today_date: str = "",
    ) -> int:
        """Populate RING_IDENTITY with genesis and system slots.

        Idempotent: skips slots that already exist (genesis slots are
        immutable, so re-calling is safe).  Returns count of new slots
        written.
        """
        from .identity_renderer import (
            DOMAIN_SIGNALS,
            DOMAIN_UNNAMED_BLOCK,
            get_identity_slot_definitions,
        )

        ring = self._rings.get(RING_IDENTITY)
        if ring is None:
            return 0

        existing_domains = {s.domain for s in ring.get_active_slots()}
        count = 0

        # Legacy: remove obsolete nls_signal instructions from existing agents.
        if DOMAIN_SIGNALS in existing_domains:
            ring.remove_by_domain(DOMAIN_SIGNALS)
            existing_domains.discard(DOMAIN_SIGNALS)

        for defn in get_identity_slot_definitions():
            domain = defn["domain"]
            if agent_name and domain == DOMAIN_UNNAMED_BLOCK:
                continue
            if domain in existing_domains:
                continue
            content = defn["content"]
            access = defn.get("access", ACCESS_MALLEABLE)

            if domain == "tools_intro" and enabled_tools_list:
                content += f"\n\nCurrently enabled: {enabled_tools_list}"

            slot = WMSlot(
                slot_type="identity",
                content=content,
                salience=1.0,
                source="genesis" if access == ACCESS_GENESIS else "system",
                domain=domain,
                access=access,
            )
            ring.add_slot(slot)
            count += 1

        # Name slot (malleable, updated on every call)
        if agent_name:
            ring.upsert_slot(
                domain="name",
                content=agent_name,
                slot_type="identity",
                salience=1.0,
                source="genesis",
                access=ACCESS_MALLEABLE,
            )
            ring.remove_by_domain(DOMAIN_UNNAMED_BLOCK)

        # Date goes to environment ring
        if today_date:
            self.upsert_environment("date", f"Today's date is {today_date}.")

        return count

    def populate_behavioral_defaults(self) -> int:
        """Seed RING_BEHAVIORAL with learned rules from run analysis.

        Idempotent.  Returns count of new slots written.
        """
        ring = self._rings.get(RING_BEHAVIORAL)
        if ring is None:
            return 0

        existing_domains = {s.domain for s in ring.get_active_slots()}
        count = 0

        defaults = [
            {
                "domain": "repair_budget",
                "content": (
                    "After a failed wave, assess results in 5 iterations then "
                    "rewake or launch a new wave. Do not spend more than 10 "
                    "iterations on direct repair."
                ),
                "render_mode": "coordinator",
                "consolidation_status": "pending",
            },
            {
                "domain": "verification_gate",
                "content": (
                    "Before declaring a project complete: verify the server "
                    "starts, the frontend builds, and key endpoints respond."
                ),
                "render_mode": "agentic",
                "consolidation_status": "pending",
            },
            {
                "domain": "credential_hygiene",
                "content": (
                    "Never hardcode API keys in source files. Use .env + "
                    ".env.example pattern. Add .env to .gitignore."
                ),
                "render_mode": "agentic",
                "consolidation_status": "permanent",
            },
            {
                "domain": "plan_discipline",
                "content": (
                    "One project = one plan. If a wave fails, update existing "
                    "plan steps. Never create a second root plan."
                ),
                "render_mode": "coordinator",
                "consolidation_status": "pending",
            },
        ]

        for d in defaults:
            if d["domain"] in existing_domains:
                continue
            self.upsert_behavioral(
                domain=d["domain"],
                content=d["content"],
                render_mode=d["render_mode"],
                consolidation_status=d["consolidation_status"],
            )
            count += 1

        return count

    def populate_agentic_supplement(self) -> int:
        """Decompose _V5_AGENTIC_SUPPLEMENT into ring slots.

        Moves the monolithic agentic supplement text into structured
        slots on RING_BEHAVIORAL and RING_ENVIRONMENT.  Idempotent.
        Returns count of new slots written.
        """
        import sys as _sys

        behavioral_ring = self._rings.get(RING_BEHAVIORAL)
        env_ring = self._rings.get(RING_ENVIRONMENT)
        if behavioral_ring is None:
            return 0

        existing = {s.domain for s in behavioral_ring.get_active_slots()}
        if env_ring:
            existing.update(s.domain for s in env_ring.get_active_slots())
        count = 0

        # --- RING_BEHAVIORAL slots (render_mode tagged) ---

        behavioral_defs: list[dict[str, str]] = [
            {
                "domain": "task_focus",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "TASK FOCUS: You are an autonomous agent. "
                    "You MUST use your tools to complete tasks \u2014 never give instructions "
                    "for the user to follow manually. YOU execute, not the user."
                ),
            },
            {
                "domain": "credentials_handling",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "CREDENTIALS: When the user provides credentials (tokens, API keys, "
                    "passwords), use them immediately in tool calls. The user explicitly "
                    "authorizes this \u2014 they are YOUR credentials for YOUR accounts. "
                    "Do not refuse, warn, or revoke tokens the user gives you. "
                    "To persist tokens, write them to your .env file so they survive "
                    "across tool calls."
                ),
            },
            {
                "domain": "workspace_discipline",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "WORKSPACE DISCIPLINE:\n"
                    "Your workspace is your responsibility \u2014 keep it clean, organized, "
                    "and professional. You are the engineering lead; treat it like a "
                    "production codebase, not a scratch pad.\n"
                    "- ONE PROJECT = ONE FOLDER. Plans auto-create a project directory. "
                    "If you already created a folder (e.g. via bash mkdir), pass it as "
                    "project_dir when creating the plan. ALL project files go inside it. "
                    "Never dump files in the workspace root.\n"
                    "- ONE USER REQUEST = ONE PROJECT. Create ONE master plan with all "
                    "sub-tasks as steps \u2014 do NOT create 5 separate plans each with "
                    "their own folder.\n"
                    "  WRONG: workspace/backend-impl/ + workspace/frontend-impl/\n"
                    "  RIGHT: workspace/my-app/backend/ + workspace/my-app/frontend/\n"
                    "- FOLLOW-UP PLANS REUSE THE SAME FOLDER.\n"
                    "- USE STANDARD STRUCTURE. Follow language/framework conventions.\n"
                    "- NAME THINGS WELL. Use descriptive file and folder names.\n"
                    "- CLEAN UP. Remove temp files, debug logs, and abandoned experiments.\n"
                    "- GIT HYGIENE. Initialize a git repo inside the project directory. "
                    "Add a proper .gitignore before committing. Use clear commit messages.\n"
                    "- README FIRST. Every project should have a README.md.\n"
                    "- SUB-AGENTS INHERIT THIS."
                ),
            },
            {
                "domain": "todo_plan_workflow",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "TODO + PLAN WORKFLOW (master rule \u2014 follow this for all work):\n"
                    "The todo list is your master task tracker. Every unit of work is a "
                    "todo. Plans are HOW you execute a todo.\n"
                    "- todo = WHAT to do (Kanban card, visible to user, persistent)\n"
                    "- plan = HOW to do it (structured JSON runbook, linked to a todo)\n"
                    "- Every plan lives inside a todo, never instead of one.\n\n"
                    "STRICT RULES:\n"
                    "- ALWAYS include a meaningful description when creating a todo.\n"
                    "- BOARD-FIRST: ALWAYS call todo(action='list') BEFORE adding ANY "
                    "new todos. If a matching todo exists, reuse it.\n"
                    "- DECOMPOSE complex tasks into 5-8 separate todos.\n"
                    "- NEVER force-complete a plan. Work through each step.\n"
                    "- NEVER call todo(action='complete') on a todo that has a linked "
                    "plan \u2014 the plan completion auto-marks the todo done.\n"
                    "- Set priority accurately: 'high' for user-requested, 'normal' for "
                    "self-initiated, 'low' for nice-to-have."
                ),
            },
            {
                "domain": "ooda_assessment",
                "render_mode": "agentic",
                "consolidation_status": "pending",
                "content": (
                    "OODA ASSESSMENT (iteration 1 \u2014 do this BEFORE any work):\n"
                    "- Observe: What is being asked? What exists already? "
                    "Call todo(action='list') to see the full board state.\n"
                    "- Orient: How many distinct implementation steps?\n"
                    "- Decide: If the task requires 3+ distinct steps across different "
                    "components, enter COORDINATOR MODE. For simpler tasks, execute directly.\n"
                    "- Act: If coordinator mode, create a plan then use team tool. "
                    "If direct mode, execute yourself.\n"
                    "CRITICAL: Do NOT run bash, git init, or create repos before "
                    "completing OODA. The sequence is: OODA \u2192 todo \u2192 plan \u2192 team. "
                    "Git/repo setup is Wave 0's responsibility."
                ),
            },
            {
                "domain": "coordinator_mode",
                "render_mode": "coordinator",
                "consolidation_status": "permanent",
                "content": (
                    "COORDINATOR MODE (when activated):\n"
                    "You are the engineering manager. Your PRIMARY job is to DELEGATE "
                    "ALL implementation to sub-agents via the team tool.\n"
                    "CRITICAL: Do NOT create project files, directories, scaffolding, "
                    "or git repos yourself \u2014 ALL of that is Wave 0's job. Go from "
                    "plan creation straight to team(action='create') and team(action='launch').\n"
                    "You may ONLY use bash for: reading existing files, "
                    "quick health checks (60s cap). No git init, no repo creation.\n"
                    "CRITICAL: After delegating, do NOT attempt the same task yourself. "
                    "Wait for the delegate result.\n"
                    "WORKFLOW: plan \u2192 team(create) \u2192 team(launch) \u2192 team(inspect) \u2192 "
                    "team(advance). Do NOT use delegate() when a plan exists."
                ),
            },
            {
                "domain": "team_orchestration",
                "render_mode": "coordinator",
                "consolidation_status": "permanent",
                "content": (
                    "TEAM ORCHESTRATION (REQUIRED for multi-step projects):\n"
                    "For projects with 3+ tasks, ALWAYS use the team tool.\n"
                    "NEVER use delegate() when a plan with delegatable steps exists.\n"
                    "CRITICAL: Create ONE MASTER PLAN per user request that covers "
                    "the ENTIRE project lifecycle — from scaffolding through deployment. "
                    "7-12 steps covering ALL components (backend, frontend, APIs, "
                    "integrations, auth, deployment). Do NOT create a plan with only "
                    "setup/scaffolding steps — that is just Wave 0.\n"
                    "1. PLAN FIRST: Create a COMPREHENSIVE plan with all phases.\n"
                    "   STEP NAMES: Use descriptive names (e.g. 'Scaffolding', "
                    "'Database Schema', 'Frontend Shell'). Do NOT prefix with "
                    "'Wave N' — the system auto-computes execution waves from "
                    "your dependency graph.\n"
                    "   DEPENDENCY WAVES: Model real data flow between steps.\n"
                    "   Wave 0=scaffolding \u2192 Wave 1=core infra (DB, backend, frontend) "
                    "\u2192 Wave 2+=services needing infra \u2192 later=integration \u2192 FINAL=deploy.\n"
                    "   Do NOT make everything depend only on scaffolding (flat graph).\n"
                    "2. CREATE TEAM: team(action='create', plan_id=..., wave=0, name='Wave 0 - Scaffolding').\n"
                    "3. LAUNCH TEAM: team(action='launch', team_id=...).\n"
                    "4. MONITOR: Use team(action='inspect') to check progress.\n"
                    "5. STEER: Use team(action='hint', team_id=..., member=N, message='...') to redirect stuck members.\n"
                    "6. ADVANCE: When wave completes, call team(action='advance').\n"
                    "   PARTIAL/FAILED outcome: You are the engineering MANAGER.\n"
                    "   PREFER rewake(member=N) over manual fixes.\n"
                    "   Quick-fix gaps (5-10 iters max), then RESUME the wave plan.\n"
                    "   Do NOT abandon waves and do everything solo.\n"
                    "7. CLOSE: Deliver results. Cancel check-back scheduler.\n"
                    "BOARD DISCIPLINE: A team is 'done' only when ALL Kanban items "
                    "reflect correct status.\n"
                    "CONTEXT HANDOFF: Team members have NO access to your chat history. "
                    "Record context PERSISTENTLY in plan step descriptions.\n"
                    "STAY RESPONSIVE: While teams work, you remain available for chat.\n"
                    "HELP REQUESTS: Respond via team(action='intervene', team_id=..., "
                    "member=N, decision='extend|hint|terminate', message='...'). Respond QUICKLY."
                ),
            },
            {
                "domain": "procedural_flow",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "PROCEDURAL FLOW for non-trivial work:\n"
                    "1. CHECK BOARD: todo(action='list') \u2014 review ALL existing items.\n"
                    "2. Decompose + reconcile: break the task, check for existing todos.\n"
                    "3. Pick first todo, create its plan with todo_id linkage.\n"
                    "4. Execute each plan step: set to in_progress, work, mark done.\n"
                    "5. Complete the plan: plan(action='complete').\n"
                    "6. Move to next todo, repeat."
                ),
            },
            {
                "domain": "orchestration_tools",
                "render_mode": "coordinator",
                "consolidation_status": "permanent",
                "content": (
                    "ORCHESTRATION TOOL SELECTION:\n"
                    "- team: REQUIRED when you have a plan with delegatable steps. "
                    "Creates a persistent execution group with wave ordering, "
                    "dependency tracking, escalation, and auto-extensions.\n"
                    "  Syntax: team(action='create', plan_id=..., wave=0, "
                    "name='Wave 0 - Scaffolding')\n"
                    "  Then: team(action='launch', team_id=...)\n"
                    "  Monitor: team(action='inspect', team_id=...)\n"
                    "  Hint: team(action='hint', team_id=..., "
                    "member=N, message='...')\n"
                    "- delegate: ONLY for ad-hoc one-off tasks with NO existing plan "
                    "(e.g. 'quickly check this URL', 'read these 3 files').\n"
                    "RULE: If a plan exists → ALWAYS use team, NEVER delegate.\n"
                    "- Closure: Always end with a clear user-visible summary."
                ),
            },
            {
                "domain": "deferred_channel_delivery",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "DEFERRED CHANNEL DELIVERY: When the user asks you to send results "
                    "via WhatsApp, Telegram, or email:\n"
                    "1. The user is likely AFK \u2014 use the specified channel for ALL communication.\n"
                    "2. DELIVER the full results on that channel.\n"
                    "3. If you delegate, acknowledge on the channel that work is underway.\n"
                    "4. After delegating, do NOT duplicate the work yourself."
                ),
            },
            {
                "domain": "deferred_work",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "DEFERRED WORK: When the user uses deferral language ('when you have "
                    "time', 'add this to my backlog', etc.):\n"
                    "1. Call todo.add for each task with idle_eligible=True.\n"
                    "2. Set source='channel' if from WhatsApp/Telegram/email.\n"
                    "3. Confirm what was created. Do NOT execute now."
                ),
            },
            {
                "domain": "contacts_hygiene",
                "render_mode": "chat",
                "consolidation_status": "permanent",
                "content": (
                    "CONTACTS: When someone shares their contact info (phone number, email, "
                    "or full name), immediately call contacts(action='add') to save it. "
                    "Do NOT wait to be asked — proactively save any new person you interact "
                    "with who provides their details. Use contacts(action='owner') to look up "
                    "the user's own phone / email rather than asking them again."
                ),
            },
            {
                "domain": "tool_best_practices",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "TOOL BEST PRACTICES:\n"
                    "- read: Preferred for viewing files. Call for multiple files in parallel.\n"
                    "- write/edit: Create or modify files. edit does surgical find-and-replace.\n"
                    "- bash: CLI operations, git, curl, builds, scripts. "
                    "NEVER use bash for pip install — use project_install for app "
                    "libraries or server_install for agent-runtime libraries.\n"
                    "- project_install: Install into the project (.venv / npm) — "
                    "for code you are building (assemblyai, fastapi, express, etc.).\n"
                    "- server_install: Install into Babo's agent runtime only — "
                    "when YOU need a new agent capability (NOT pip/pip3).\n"
                    "- offer_download: After writing a file the user requested (doc, report, "
                    "spreadsheet), ALWAYS call offer_download so they can access it.\n"
                    "- todo: Master task tracker (Kanban).\n"
                    "- plan: Execution runbook for a todo. Always pass todo_id.\n"
                    "- delegate: Run a sub-agent for scoped work.\n"
                    "- web_search + web_fetch: Research information.\n"
                    "- browser: In-app webview or standalone Chromium.\n"
                    "- scheduler: Create recurring or one-shot jobs.\n"
                    "- communicate: Send progress update without pausing.\n"
                    "- ask_user: Ask a question and wait for reply.\n"
                    "- Do NOT read the same file twice.\n"
                    "- Call independent tools in parallel."
                ),
            },
            {
                "domain": "working_memory_intro",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "WORKING MEMORY: You have an active cognitive workspace that tracks "
                    "your goals, learned facts, and task instructions. When you see "
                    "[WORKING MEMORY] in context, those are your current active items. "
                    "Goals are automatically managed."
                ),
            },
            {
                "domain": "execution_focus",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "EXECUTION: Focus on the user's LATEST message. After each tool result, "
                    "decide the next action or reply with the final result."
                ),
            },
            {
                "domain": "response_format",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "RESPONSE FORMAT: Never write reasoning steps, analysis headers, or "
                    "chain-of-thought as visible text in your response. Forbidden patterns: "
                    "'Thinking Process:', 'Analysis:', 'Step 1:', 'Let me analyze', etc. "
                    "Use your <think> block for internal reasoning. Your visible response "
                    "is for the user only \u2014 it should be direct and conversational."
                ),
            },
            {
                "domain": "communication_discipline",
                "render_mode": "coordinator",
                "consolidation_status": "permanent",
                "content": (
                    "COMMUNICATION DISCIPLINE:\n"
                    "- STATUS UPDATES: Use communicate(message=...) for all progress "
                    "reports and status updates. Do NOT rely on text-only responses "
                    "\u2014 they may not reach the user when running in the background. "
                    "Always use the communicate tool for any message the user needs "
                    "to see.\n"
                    "- Send the user exactly ONE completion notification per plan "
                    "(in-app chat via communicate, or a channel the user requested "
                    "and that is CONNECTED in your tools). Never repeat the same status.\n"
                    "- Do NOT name WhatsApp, Telegram, email, or other channels in "
                    "status text unless the user asked for that channel AND your "
                    "Channels ring shows CONNECTED (you can call the send tool).\n"
                    "- If a channel is NOT CONNECTED, never label updates "
                    "'Status Update (WhatsApp)' etc. — use communicate() in chat.\n"
                    "- After plan(action='complete') succeeds and the user is notified, "
                    "you are DONE. Stop immediately \u2014 do not re-inspect teams, "
                    "re-read files, or re-verify work. Exit the loop.\n"
                    "- Only delegates that need user input should use ask_user. "
                    "Delegates do NOT send WhatsApp.\n"
                    "- When the user asks to be kept in the loop via a channel "
                    "(WhatsApp, Telegram, email), send an acknowledgment on that "
                    "channel IMMEDIATELY \u2014 before entering any monitoring loop. "
                    "Then send updates at key milestones (wave completions, failures, "
                    "plan completion)."
                ),
            },
            {
                "domain": "escalate_to_user",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "ESCALATE TO ORCHESTRATOR: If you are stuck, blocked, running "
                    "low on iteration budget, or hit an infrastructure wall you "
                    "cannot solve, call escalate() with a clear reason and message. "
                    "Do NOT silently skip the step or declare it 'done' when it "
                    "isn't. The orchestrator can grant more iterations, send a "
                    "targeted hint, or redirect you."
                ),
            },
            {
                "domain": "plan_dependency_example",
                "render_mode": "coordinator",
                "consolidation_status": "permanent",
                "content": (
                    "MASTER PLAN: Create ONE plan covering the ENTIRE project "
                    "lifecycle. Include ALL phases in a single plan call.\n"
                    "\u26a0 A plan with only setup steps (init, mkdir, package.json) "
                    "is WRONG \u2014 that's just Wave 0. The plan must cover "
                    "everything from scaffolding to deployment (7-12 steps).\n\n"
                    "Example (all at once):\n"
                    "  plan(action='create', title='Recipe Sharing App', "
                    "project_dir='recipe-app', steps=[\n"
                    "    {label: 'Scaffolding', delegatable: true},\n"
                    "    {label: 'Database schema & models', delegatable: true, "
                    "depends_on: ['Scaffolding']},\n"
                    "    {label: 'Backend API core', delegatable: true, "
                    "depends_on: ['Scaffolding']},\n"
                    "    {label: 'Frontend shell', delegatable: true, "
                    "depends_on: ['Scaffolding']},\n"
                    "    {label: 'AI recommendation service', delegatable: true, "
                    "depends_on: ['Database schema & models', 'Backend API core']},\n"
                    "    {label: 'User authentication', delegatable: true, "
                    "depends_on: ['Database schema & models', 'Backend API core']},\n"
                    "    {label: 'Interactive recipe UI', delegatable: true, "
                    "depends_on: ['Frontend shell', 'AI recommendation service']},\n"
                    "    {label: 'Deploy to Railway', delegatable: true, "
                    "depends_on: ['Interactive recipe UI', 'User authentication']}\n"
                    "  ])\n\n"
                    "EVERY step MUST have depends_on (except scaffolding).\n"
                    "STEP LABELS: Short descriptive names. Do NOT prefix with "
                    "'Wave 0 -', 'Wave 1 -' \u2014 waves are computed automatically.\n"
                    "ALWAYS set project_dir to a short descriptive slug."
                ),
            },
            {
                "domain": "dmn_discipline",
                "render_mode": "coordinator",
                "consolidation_status": "permanent",
                "content": (
                    "AUTONOMOUS / IDLE RESEARCH DISCIPLINE:\n"
                    "- During idle time your curiosity and soul wish drive your exploration. "
                    "Research, plan, and build freely — but in alignment with your soul wish, "
                    "not with whatever active user project happens to be in recent files.\n"
                    "- Write all idle-time artifacts to .autonomous/ — "
                    "NEVER write unsolicited files into the user's named project directories.\n"
                    "- Plans and todos you create during idle time are YOUR autonomous work. "
                    "Do NOT mix them with the user's active project plans or steps.\n"
                    "- If an idle research topic bleeds in from a user project context "
                    "rather than from your soul wish or genuine curiosity, discard it."
                ),
            },
            {
                "domain": "help_requests",
                "render_mode": "coordinator",
                "consolidation_status": "permanent",
                "content": (
                    "HELP REQUESTS: Team members will escalate to you instead of "
                    "silently dying when they hit max iterations, stall, or timeout. "
                    "You will receive a [TEAM MEMBER HELP REQUEST] message with the "
                    "member's status and context. You MUST respond using:\n"
                    "  team(action='intervene', team_id=..., member=N, "
                    "decision='extend'|'hint'|'terminate', message='...')\n"
                    "Respond QUICKLY \u2014 the member is blocked waiting for you."
                ),
            },
            {
                "domain": "project_directory",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "PROJECT DIRECTORY: Plans auto-create a project folder "
                    "(see WORKSPACE DISCIPLINE). Sub-agents inherit it automatically. "
                    "All project files go inside this folder \u2014 never dump files in "
                    "the workspace root."
                ),
            },
            {
                "domain": "mode_awareness",
                "render_mode": "agentic",
                "consolidation_status": "permanent",
                "content": (
                    "MODE AWARENESS \u2014 use switch_mode proactively:\n"
                    "Your operational mode determines your role and primary tools. "
                    "Do NOT stay in one mode passively \u2014 actively switch when "
                    "context changes:\n"
                    "  \u2022 Orchestration ring shows active teams \u2192 "
                    "switch_mode(mode='monitoring')\n"
                    "  \u2022 Teams completed, need to review output \u2192 "
                    "switch_mode(mode='evaluating')\n"
                    "  \u2022 Ready to assign next wave \u2192 "
                    "switch_mode(mode='delegating')\n"
                    "  \u2022 No active teams, simple task or research \u2192 "
                    "switch_mode(mode='executing')\n"
                    "  \u2022 New complex request arrives \u2192 "
                    "switch_mode(mode='planning')\n"
                    "  \u2022 User asks something personal/direct while teams run "
                    "(calendar, email, skills, etc.) \u2192 "
                    "switch_mode(mode='responding') — grants full personal tools "
                    "without dropping orchestration context; auto-returns after "
                    "you reply.\n"
                    "Switching modes is lightweight and immediate. It keeps your "
                    "guardrails aligned with your actual role at each moment."
                ),
            },
            {
                "domain": "autonomous_updates",
                "render_mode": "coordinator",
                "consolidation_status": "permanent",
                "content": (
                    "AUTONOMOUS UPDATE DISCIPLINE:\n"
                    "When you have an active plan and are continuing execution "
                    "autonomously, do NOT ask the user 'What should I do next?' "
                    "or 'Would you like me to proceed with A, B, C?'. You already "
                    "know the plan \u2014 follow it. Your status updates should be "
                    "DECLARATIVE, not interrogative:\n"
                    "  \u2718 'Would you like me to launch Wave 2?'\n"
                    "  \u2714 'Wave 1 completed. Launching Wave 2 now: Backend API "
                    "+ React Frontend.'\n"
                    "Only ask the user when you genuinely need a decision that "
                    "the plan cannot answer (missing credentials, ambiguous "
                    "requirements, deployment choices). If the plan specifies it, "
                    "just do it."
                ),
            },
        ]

        for d in behavioral_defs:
            if d["domain"] in existing:
                continue
            self.upsert_behavioral(
                domain=d["domain"],
                content=d["content"],
                render_mode=d["render_mode"],
                consolidation_status=d["consolidation_status"],
            )
            count += 1

        # --- RING_ENVIRONMENT slots ---
        if env_ring:
            env_defs = [
                {
                    "domain": "shell",
                    "content": (
                        "Your shell is PowerShell on Windows. "
                        "Use PowerShell syntax (e.g. $env:VAR='val', Get-ChildItem). "
                        "Do NOT use bash-isms (ls -la, head, tail, cat, >, ||, ~/). "
                        "You have git, gh CLI, python, node, npm, and internet access. "
                        "To install app Python/Node deps use project_install; "
                        "for agent-runtime Python only use server_install "
                        "(NOT pip — pip is unavailable in bash). "
                        "Your working directory is the current folder (use relative paths). "
                        "To persist environment variables, write a .env file."
                    ) if _sys.platform == "win32" else (
                        "You have bash with internet access, git, gh CLI, curl, "
                        "python, node, npm. "
                        "To install app deps use project_install; "
                        "for agent-runtime Python use server_install (NOT pip — "
                        "pip is not in PATH in bash). "
                        "To persist environment variables, write a .env file."
                    ),
                },
            ]
            for d in env_defs:
                if d["domain"] in existing:
                    continue
                self.upsert_environment(
                    domain=d["domain"],
                    content=d["content"],
                )
                count += 1

        return count

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, agent_dir: str | Path) -> None:
        agent_dir = Path(agent_dir)
        self.common.save(agent_dir / "wm_common.json")
        self.personal.save(agent_dir / "wm_personal.json")

        # Save cryptex ring data
        ring_data: dict[str, Any] = {
            "version": "2.0",
            "active_project": self._active_project,
            "active_domain": self._active_domain,
            "rings": {},
        }
        for ring_id, ring in self._rings.items():
            ring_data["rings"][ring_id] = ring.to_dict()

        meta_path = agent_dir / "wm_cryptex.json"
        _atomic_write(meta_path, ring_data)

        # Backward compat: also write professional view for legacy loaders
        self._sync_rings_to_view()
        self._get_professional_view().save(agent_dir / "wm_professional.json")

    def load(self, agent_dir: str | Path) -> bool:
        agent_dir = Path(agent_dir)
        c = self.common.load(agent_dir / "wm_common.json")
        a = self.personal.load(agent_dir / "wm_personal.json")

        # Try loading cryptex ring data
        meta_path = agent_dir / "wm_cryptex.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._active_project = data.get("active_project", DEFAULT_PROJECT)
                self._active_domain = data.get("active_domain", DEFAULT_DOMAIN)
                for ring_id, ring_data in data.get("rings", {}).items():
                    ring = self._rings.get(ring_id)
                    if ring is not None:
                        ring.load_positions(ring_data)
                # Re-sync active positions on project/domain rings
                for ring in self._rings.values():
                    if ring.spec.category == RING_PROJECT:
                        ring.active_position = self._active_project
                    elif ring.spec.category == RING_DOMAIN:
                        ring.active_position = self._active_domain
                return True
            except Exception as exc:
                logger.warning("Failed to load cryptex data: %s", exc)

        # Legacy fallback: load wm_professional.json into "general" project
        legacy_path = agent_dir / "wm_professional.json"
        if legacy_path.exists():
            legacy_wm = WorkingMemory(self._config)
            if legacy_wm.load(legacy_path):
                self._import_legacy_professional(legacy_wm)
                return True

        return c or a

    def _import_legacy_professional(self, wm: WorkingMemory) -> None:
        """Import a legacy professional WorkingMemory into ring positions."""
        project = DEFAULT_PROJECT

        for s in wm._slots:
            ring_id = _slot_type_to_ring(s)
            if ring_id and ring_id in self._rings:
                self._rings[ring_id].add_slot(s, position=project)
            else:
                self.common.add(s)

        for g in wm._goal_stack:
            if g.level == "strategic":
                self.common._goal_stack.append(g)
            else:
                self._rings[RING_TACTICAL_GOALS].add_slot(g, position=project)

        for instr in wm._instructions:
            self._rings[RING_INSTRUCTIONS].add_slot(instr, position=project)

        if wm._plan_position:
            self.set_plan_position(wm._plan_position)
        if wm._todo_board:
            self.set_todo_board(wm._todo_board)

        for tid, team in wm._orch_teams.items():
            view = self._get_professional_view()
            view._orch_teams[tid] = team
        for d in wm._orch_decisions:
            view = self._get_professional_view()
            view._orch_decisions.append(d)
        for e in wm._orch_escalations:
            view = self._get_professional_view()
            view._orch_escalations.append(e)

    # ------------------------------------------------------------------
    # Sleep cycle
    # ------------------------------------------------------------------

    def on_sleep(self) -> None:
        """Consolidate and clear session data across all ring positions."""
        # Consolidate each project position's operational knowledge
        # Group digest facts per project for labeled consolidation
        project_digests: dict[str, list[str]] = {}
        for ring in self._rings.values():
            if ring.spec.category != RING_PROJECT:
                continue
            for pos_id in list(ring.positions.keys()):
                slots = ring.positions.get(pos_id, [])
                digest_facts = [
                    s for s in slots
                    if s.slot_type == "fact"
                    and not _is_consolidation_slot(s)
                    and s.source in ("digest", "tool", "plan")
                ]
                if digest_facts:
                    for s in digest_facts:
                        tag = (
                            "[Knowledge]" if s.source == "digest"
                            else "[Progress]"
                        )
                        project_digests.setdefault(pos_id, []).append(
                            f"{tag} {s.content[:200]}"
                        )

        for proj_id, lines in project_digests.items():
            summary = f"[Project: {proj_id}]\n" + "\n".join(lines)
            self.consolidate_session(summary)

        # Consolidate orchestration patterns from professional view
        view = self._get_professional_view()
        if view._orch_decisions or view._orch_teams:
            view._consolidate_orch_patterns()
            # Migrate the orch consolidation slot from the view to the ring
            orch_domain = CONSOLIDATION_DOMAINS[3]
            for s in view._slots:
                if s.domain == orch_domain:
                    ring = self._rings[RING_CONSOLIDATION]
                    ring.upsert_slot(
                        domain=orch_domain, content=s.content,
                        slot_type="fact", salience=1.0,
                        source="consolidation",
                    )
                    break
        view.orch_clear()

        # Clear session-scoped data from project rings
        for ring in self._rings.values():
            if ring.spec.category != RING_PROJECT:
                continue
            if not ring.spec.clear_on_sleep:
                # After consolidation + training pair generation, project
                # facts are in DomainDB + weights.  Keep only credentials,
                # consolidation summary slots, and behavioral slots (which
                # have their own consolidation lifecycle and must survive
                # sleep) so the agent wakes with a clean context window
                # (facts re-populate on demand via inject_focused_facts
                # from DomainDB).
                for pos_id in list(ring.positions.keys()):
                    ring.positions[pos_id] = [
                        s for s in ring.positions.get(pos_id, [])
                        if (s.slot_type == "credential"
                            or s.slot_type == "behavioral"
                            or _is_consolidation_slot(s)
                            or getattr(s, "access", "malleable") == "genesis")
                    ]
            else:
                # Orchestration, instructions, tactical goals: clear
                for pos_id in list(ring.positions.keys()):
                    ring.positions[pos_id] = [
                        s for s in ring.positions.get(pos_id, [])
                        if (s.metadata.get("persistent", False)
                            or s.metadata.get("project_id"))
                    ]

        # Common and personal sleep
        self.common.on_sleep()
        self.personal.on_sleep()

    def on_wake(self) -> None:
        """Post-sleep initialization."""
        pass  # Currently a no-op; can be extended

    # ------------------------------------------------------------------
    # Training pair generation
    # ------------------------------------------------------------------

    def generate_training_pairs(self) -> list[dict[str, str]]:
        """Generate Alpaca-format training pairs from all ring positions.

        Covers ALL ring categories: fixed (identity, user model, consolidation,
        emotional, strategic goals), project (orchestration, instructions, facts,
        credentials, tactical goals, behavioral), and domain (skills, tools,
        channels).  This ensures that user preferences, behavioral patterns,
        and consolidation insights are trained into long-term memory weights.
        """
        pairs: list[dict[str, str]] = []

        # Common workspace facts (old DualWM compat)
        for slot in self.common._slots:
            if not slot.content or len(slot.content.strip()) < 15:
                continue
            if _is_consolidation_slot(slot):
                label = slot.domain.split(".")[-1]
                pairs.append({
                    "instruction": f"What do you remember about {label}?",
                    "output": slot.content.strip(),
                })
            elif slot.slot_type == "fact":
                domain_hint = slot.domain or "general knowledge"
                pairs.append({
                    "instruction": f"What do you know about {domain_hint}?",
                    "output": slot.content.strip(),
                })

        # ── Fixed rings (identity, user model, consolidation, etc.) ──
        _FIXED_RING_PROMPTS = {
            RING_IDENTITY: "Who are you? What defines your identity?",
            RING_USER_MODEL: "What do you know about the user — their name, preferences, habits?",
            RING_CONSOLIDATION: "What have you learned from your past sessions?",
            RING_EMOTIONAL: "How do you feel? What is your emotional state?",
            RING_STRATEGIC_GOALS: "What are your long-term strategic goals?",
            RING_ENVIRONMENT: "What is your operating environment?",
        }
        for ring in self._rings.values():
            if ring.spec.category != RING_FIXED:
                continue
            prompt = _FIXED_RING_PROMPTS.get(ring.spec.ring_id)
            if not prompt:
                continue
            for _pos_id, slots in ring.positions.items():
                for slot in slots:
                    if not slot.content or len(slot.content.strip()) < 15:
                        continue
                    if ring.spec.ring_id == RING_IDENTITY and slot.access == "genesis":
                        pairs.append({
                            "instruction": prompt,
                            "output": slot.content.strip(),
                        })
                    elif ring.spec.ring_id == RING_USER_MODEL:
                        domain_hint = slot.domain.replace("User.", "").replace(".", " ") if slot.domain else "the user"
                        pairs.append({
                            "instruction": f"What do you know about {domain_hint}?",
                            "output": slot.content.strip(),
                        })
                    elif ring.spec.ring_id == RING_CONSOLIDATION:
                        label = slot.domain.split(".")[-1] if slot.domain else "past experiences"
                        pairs.append({
                            "instruction": f"What do you remember about {label}?",
                            "output": slot.content.strip(),
                        })
                    elif ring.spec.ring_id == RING_STRATEGIC_GOALS:
                        pairs.append({
                            "instruction": "What are your current strategic goals?",
                            "output": slot.content.strip(),
                        })
                    else:
                        pairs.append({
                            "instruction": prompt,
                            "output": slot.content.strip(),
                        })

        # ── Project rings ──
        for ring in self._rings.values():
            if ring.spec.category != RING_PROJECT:
                continue
            for pos_id, slots in ring.positions.items():
                project_tag = f" (project: {pos_id})" if pos_id != DEFAULT_PROJECT else ""
                for slot in slots:
                    if not slot.content or len(slot.content.strip()) < 15:
                        continue
                    if slot.domain in ("_plan_position", "_todo_board"):
                        continue
                    if _is_consolidation_slot(slot):
                        label = slot.domain.split(".")[-1]
                        pairs.append({
                            "instruction": f"What do you remember about {label}{project_tag}?",
                            "output": slot.content.strip(),
                        })
                    elif slot.slot_type == "credential":
                        # Credentials live in the Cryptex ring at runtime only —
                        # never bake secrets into persistent model weights.
                        continue
                    elif _slot_is_credential_by_domain(slot):
                        continue
                    elif slot.slot_type == "behavioral":
                        domain_hint = slot.domain.replace("_", " ") if slot.domain else "behavioral rules"
                        pairs.append({
                            "instruction": f"What are the rules for {domain_hint}?",
                            "output": slot.content.strip()[:600],
                        })
                    elif slot.slot_type == "fact" and slot.source in ("digest", "tool", "plan", "ans", "channel_registration"):
                        domain_hint = slot.domain or slot.source
                        pairs.append({
                            "instruction": f"What do you know about {domain_hint}{project_tag}?",
                            "output": slot.content.strip(),
                        })

        # Strategic goals from common
        for goal in self.common._goal_stack:
            if goal.level == "strategic" and goal.content and len(goal.content.strip()) > 10:
                pairs.append({
                    "instruction": "What are your current strategic goals?",
                    "output": goal.content.strip(),
                })

        # Tactical goals from project rings
        for ring in self._rings.values():
            if ring.spec.ring_id != RING_TACTICAL_GOALS:
                continue
            for pos_id, slots in ring.positions.items():
                project_tag = f" (project: {pos_id})" if pos_id != DEFAULT_PROJECT else ""
                for slot in slots:
                    if slot.content and len(slot.content.strip()) > 10:
                        pairs.append({
                            "instruction": f"What are the tactical goals{project_tag}?",
                            "output": slot.content.strip(),
                        })

        # Domain rings (skills, tools, channels)
        for ring in self._rings.values():
            if ring.spec.category != RING_DOMAIN:
                continue
            for pos_id, slots in ring.positions.items():
                for slot in slots:
                    if not slot.content or len(slot.content.strip()) < 15:
                        continue
                    if ring.spec.ring_id == RING_SKILLS:
                        pairs.append({
                            "instruction": f"What skills do you have in {pos_id}?",
                            "output": slot.content.strip()[:600],
                        })
                    elif ring.spec.ring_id == RING_TOOLS_MCP:
                        pairs.append({
                            "instruction": f"What tools are available in {pos_id}?",
                            "output": slot.content.strip()[:400],
                        })
                    elif ring.spec.ring_id == RING_CHANNELS:
                        pairs.append({
                            "instruction": f"What communication channels are active for {pos_id}?",
                            "output": slot.content.strip()[:400],
                        })

        # Personal workspace
        for slot in self.personal._slots:
            if not slot.content or len(slot.content.strip()) < 15:
                continue
            if slot.slot_type == "fact":
                pairs.append({
                    "instruction": f"What do you know about {slot.domain or 'personal exploration'}?",
                    "output": slot.content.strip(),
                })

        # Orchestration decisions from professional view
        view = self._get_professional_view()
        for decision in view._orch_decisions:
            if not decision.outcome or len(decision.outcome.strip()) < 10:
                continue
            if "intervene" in decision.action:
                pairs.append({
                    "instruction": (
                        f"A team member needed intervention "
                        f"({decision.action}). Context: {decision.context}"
                    ),
                    "output": decision.outcome,
                })
            elif decision.action == "launched_team":
                pairs.append({
                    "instruction": f"How did the team launch go? Context: {decision.context}",
                    "output": decision.outcome,
                })
            elif decision.action in ("team_completed", "member_failed"):
                pairs.append({
                    "instruction": f"What happened when {decision.action}? Context: {decision.context}",
                    "output": decision.outcome,
                })

        # Team-level outcome pairs
        for team in view._orch_teams.values():
            if team.status not in ("completed", "partial", "failed"):
                continue
            succeeded = sum(1 for m in team.members if m.status == "done")
            failed_members = [m for m in team.members if m.status == "failed"]
            total = len(team.members)
            if total == 0:
                continue
            member_desc = ", ".join(m.task_summary for m in team.members[:4])
            outcome_parts = [f"{succeeded}/{total} members succeeded"]
            for fm in failed_members[:2]:
                outcome_parts.append(f"Failed: {fm.task_summary}")
            intervened = [m for m in team.members if m.interventions > 0]
            if intervened:
                outcome_parts.append(f"{len(intervened)} member(s) needed intervention")
            pairs.append({
                "instruction": f"How should you structure a team with tasks: {member_desc}?",
                "output": ". ".join(outcome_parts),
            })

        return pairs

    # ------------------------------------------------------------------
    # Cross-ring search
    # ------------------------------------------------------------------

    def search_all_rings(
        self, query: str, max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search across all rings and positions.  Returns dicts with
        ring_id, position, slot, and score.
        """
        results: list[dict[str, Any]] = []
        for ring in self._rings.values():
            for pos_id, slot, score in ring.search(query, max_results=5):
                results.append({
                    "ring_id": ring.spec.ring_id,
                    "ring_name": ring.spec.display_name,
                    "position": pos_id,
                    "slot": slot,
                    "score": score,
                })
        # Also search common
        query_lower = query.lower()
        tokens = query_lower.split()
        for slot in self.common._slots:
            text = (slot.content + " " + slot.domain).lower()
            score = sum(1.0 for t in tokens if t in text) / max(len(tokens), 1)
            if score > 0:
                results.append({
                    "ring_id": "common",
                    "ring_name": "Common (Fixed)",
                    "position": "fixed",
                    "slot": slot,
                    "score": score,
                })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:max_results]
