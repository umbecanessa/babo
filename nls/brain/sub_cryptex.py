"""SubCryptex — lightweight per-delegate ring memory with rotation.

Each sub-agent (delegate) gets its own SubCryptex at spawn time,
pre-populated with relevant slices of the orchestrator's CryptexMemory.

Ring Rotation
~~~~~~~~~~~~~

Unlike the original flat-list design, rings now use WMRing's native
**position** system.  Each ring can hold multiple positions keyed by
file path, domain, or task area.  Only the *active* position renders
in full; dormant positions appear as compact cross-read summaries.
This keeps the total system-message cost **fixed** regardless of how
many files the agent has touched.

  * **Knowledge ring** rotates by file — reading ``EvaluationView.jsx``
    rotates to that file's position.  Other files peek through as
    1-line cross-read summaries.
  * **Progress ring** rotates by area (frontend / backend / general).
  * **Task ring** uses positions to separate the locked instructions
    from the compressible manifest and briefing.
  * **Goals ring** separates ``active`` goals from ``inherited`` ones
    that decay over time.

Budget Envelope
~~~~~~~~~~~~~~~

Each ring gets a proportional token allocation derived from its
priority weight.  Empty rings donate their budget to others.  The
total system message never exceeds the configured budget.
"""

from __future__ import annotations

import logging
import json
from copy import deepcopy
from typing import Any, Callable

from .working_memory import WMSlot
from .cryptex import (
    RingSpec,
    WMRing,
    RING_FIXED,
    RING_PROJECT_FACTS,
    RING_CREDENTIALS,
    RING_SKILLS,
    RING_TACTICAL_GOALS,
    RING_USER_MODEL,
)

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# SubCryptex ring IDs
# -----------------------------------------------------------------------

SUB_RING_TASK = "task"
SUB_RING_PROGRESS = "progress"
SUB_RING_KNOWLEDGE = "knowledge"
SUB_RING_ORCHESTRATOR = "orchestrator"

# Position names for structured rings
_POS_INSTRUCTIONS = "instructions"
_POS_TECH_STACK = "tech_stack"
_POS_FILE_OWNERSHIP = "file_ownership"
_POS_FILE_CONTEXT = "file_context"
_POS_MANIFEST = "manifest"
_POS_BRIEFING = "briefing"
_POS_ACTIVE = "active"
_POS_INHERITED = "inherited"
_POS_GENERAL = "general"
_POS_FRONTEND = "frontend"
_POS_BACKEND = "backend"

_SUB_RING_REGISTRY: tuple[RingSpec, ...] = (
    RingSpec(SUB_RING_TASK, RING_FIXED, "Task Instructions",
             allow_cross_read=True, max_slots_per_position=3),
    RingSpec(SUB_RING_PROGRESS, RING_FIXED, "Execution Progress",
             allow_cross_read=True, max_slots_per_position=8),
    RingSpec(SUB_RING_KNOWLEDGE, RING_FIXED, "Learned Knowledge",
             allow_cross_read=True, max_slots_per_position=6),
    RingSpec(SUB_RING_ORCHESTRATOR, RING_FIXED, "Orchestrator Directives",
             allow_cross_read=True, max_slots_per_position=8),
    RingSpec(RING_PROJECT_FACTS, RING_FIXED, "Project Facts",
             allow_cross_read=True, max_slots_per_position=12),
    RingSpec(RING_CREDENTIALS, RING_FIXED, "Credentials",
             allow_cross_read=False, max_slots_per_position=8),
    RingSpec(RING_TACTICAL_GOALS, RING_FIXED, "Tactical Goals",
             allow_cross_read=True, max_slots_per_position=6),
    RingSpec(RING_SKILLS, RING_FIXED, "Skills",
             allow_cross_read=True, max_slots_per_position=8),
)

_SUB_SPECS_BY_ID: dict[str, RingSpec] = {s.ring_id: s for s in _SUB_RING_REGISTRY}

_DEFAULT_PRIORITIES: dict[str, float] = {
    SUB_RING_TASK: 1.0,
    SUB_RING_ORCHESTRATOR: 0.97,
    SUB_RING_PROGRESS: 0.9,
    SUB_RING_KNOWLEDGE: 0.8,
    RING_PROJECT_FACTS: 0.65,
    RING_CREDENTIALS: 0.6,
    RING_TACTICAL_GOALS: 0.55,
    RING_SKILLS: 0.5,
}

_WRITE_TOOLS = frozenset({"write", "edit", "delete_file", "move_file"})
_READ_TOOLS = frozenset({"read", "list_dir", "glob", "grep", "semantic_search"})
_EXEC_TOOLS = frozenset({"bash"})

# Ring-section headers used by compose_context
_RING_HEADERS: dict[str, str] = {
    SUB_RING_ORCHESTRATOR: "[ORCHESTRATOR — follow these directives]",
    SUB_RING_PROGRESS: "[PROGRESS — what you have already done]",
    SUB_RING_KNOWLEDGE: "[KNOWLEDGE — what you have learned]",
    RING_PROJECT_FACTS: "[PROJECT FACTS]",
    RING_CREDENTIALS: "[CREDENTIALS]",
    RING_TACTICAL_GOALS: "[GOALS]",
    RING_SKILLS: "[RELEVANT SKILLS]",
}

_SKILL_BOOST_HEADER = "[⚠ RELEVANT SKILLS — use now (stuck recovery)]"

_MAX_CROSS_READ_LINES = 8


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _classify_file(path: str) -> str:
    """Classify a file path into frontend / backend / general."""
    p = path.replace("\\", "/").lower()
    if any(p.startswith(pfx) for pfx in ("src/", "frontend/", "public/", "app/")):
        return _POS_FRONTEND
    if any(p.endswith(ext) for ext in (".jsx", ".tsx", ".vue", ".svelte", ".css", ".scss")):
        return _POS_FRONTEND
    if any(p.startswith(pfx) for pfx in ("backend/", "server/", "api/")):
        return _POS_BACKEND
    if p.endswith(".py"):
        return _POS_BACKEND
    return _POS_GENERAL


def _file_to_position(path: str) -> str:
    """Normalise a file path into a ring position key."""
    return path.replace("\\", "/").strip("/")


def _summarize_tree(files: list[str], max_dirs: int = 12) -> str:
    """Summarise a file list as a compact directory tree.

    Returns something like:
        ``backend/ (14 files), src/components/ (8 files), ...``
    """
    from collections import Counter
    dir_counts: Counter[str] = Counter()
    for f in files:
        parts = f.replace("\\", "/").split("/")
        if len(parts) >= 2:
            prefix = "/".join(parts[:2]) + "/"
        else:
            prefix = "(root)/"
        dir_counts[prefix] += 1
    top = dir_counts.most_common(max_dirs)
    pieces = [f"{d} ({n} files)" for d, n in top]
    remainder = sum(dir_counts.values()) - sum(n for _, n in top)
    if remainder > 0:
        pieces.append(f"+{remainder} more files")
    return ", ".join(pieces) if pieces else "(empty)"


# -----------------------------------------------------------------------
# SubCryptex
# -----------------------------------------------------------------------

class SubCryptex:
    """Lightweight per-delegate Cryptex with ring rotation."""

    _MAX_TRACKED_FILES = 50
    _DECAY_INTERVAL = 3  # decay every N iterations

    def __init__(self, context_budget_tokens: int = 20_000) -> None:
        self._rings: dict[str, WMRing] = {}
        for spec in _SUB_RING_REGISTRY:
            self._rings[spec.ring_id] = WMRing(spec=spec)

        self._priorities: dict[str, float] = dict(_DEFAULT_PRIORITIES)
        self._context_budget = context_budget_tokens
        self._iteration: int = 0

        self._files_created: list[str] = []
        self._files_modified: list[str] = []
        self._files_read: list[str] = []
        self._errors_seen: list[str] = []
        self._tools_used: dict[str, int] = {}
        self._file_ledger: Any | None = None
        self._skill_boost_remaining: int = 0

        # Set initial active positions for multi-position rings
        self._rings[SUB_RING_TASK].rotate(_POS_INSTRUCTIONS)
        self._rings[SUB_RING_PROGRESS].rotate(_POS_GENERAL)
        self._rings[SUB_RING_KNOWLEDGE].rotate(_POS_GENERAL)
        self._rings[RING_TACTICAL_GOALS].rotate(_POS_ACTIVE)

    # ------------------------------------------------------------------
    # Tick — called every iteration for decay
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Advance iteration counter and periodically decay salience."""
        self._iteration += 1
        if self._skill_boost_remaining > 0:
            self._skill_boost_remaining -= 1
        if self._iteration % self._DECAY_INTERVAL == 0:
            for ring in self._rings.values():
                ring.decay_salience(dt=1.0)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def spawn_from_parent(
        cls,
        parent: Any | None,
        task: str,
        preflight_facts: str = "",
        cwd_info: str = "",
        project_dir_info: str = "",
        sub_agent_supplement: str = "",
        context_window_tokens: int = 0,
        file_manifest: list[str] | None = None,
        team_briefing: str = "",
        tech_stack_block: str = "",
        file_ownership_block: str = "",
        file_ledger: Any | None = None,
    ) -> "SubCryptex":
        """Create a pre-populated SubCryptex from the parent's Cryptex.

        The task ring is now segmented into positions:

        - ``instructions`` — locked task + CWD + supplement (always active)
        - ``manifest`` — compact directory summary (dormant, cross-readable)
        - ``briefing`` — team wave context (dormant, decaying)
        """
        _budget = 20_000
        if context_window_tokens > 0:
            _budget = max(4_000, context_window_tokens * 3 // 10)
        sc = cls(context_budget_tokens=_budget)

        # --- Task ring: instructions position (locked, always active) ---
        task_content = (
            f"TASK:\n{task}\n\n"
            + (cwd_info + "\n" if cwd_info else "")
            + (project_dir_info + "\n" if project_dir_info else "")
            + (sub_agent_supplement if sub_agent_supplement else "")
        )
        sc._rings[SUB_RING_TASK].upsert_slot(
            domain="task_instructions",
            content=task_content,
            slot_type="instruction",
            salience=1.0,
            source="genesis",
            access="genesis",
            position=_POS_INSTRUCTIONS,
        )

        if tech_stack_block:
            sc._rings[SUB_RING_TASK].upsert_slot(
                domain="tech_stack",
                content=tech_stack_block[:3000],
                slot_type="instruction",
                salience=1.0,
                source="genesis",
                access="genesis",
                position=_POS_TECH_STACK,
            )

        if file_ownership_block:
            sc._rings[SUB_RING_TASK].upsert_slot(
                domain="file_ownership",
                content=file_ownership_block[:2000],
                slot_type="instruction",
                salience=0.98,
                source="genesis",
                access="genesis",
                position=_POS_FILE_OWNERSHIP,
            )

        sc._file_ledger = file_ledger

        # --- Task ring: manifest position (compact directory summary) ---
        if file_manifest:
            tree_summary = _summarize_tree(file_manifest)
            sc._rings[SUB_RING_TASK].upsert_slot(
                domain="file_manifest",
                content=f"Project files ({len(file_manifest)} total): {tree_summary}",
                slot_type="fact",
                salience=0.5,
                source="genesis",
                position=_POS_MANIFEST,
            )

        # --- Task ring: briefing position (peer awareness only, decays) ---
        if team_briefing:
            sc._rings[SUB_RING_TASK].upsert_slot(
                domain="team_briefing",
                content=(
                    "Teammate scopes (awareness only — do NOT implement these):\n"
                    + team_briefing[:1800]
                ),
                slot_type="fact",
                salience=0.6,
                source="genesis",
                position=_POS_BRIEFING,
            )

        # Ensure active position is instructions
        sc._rings[SUB_RING_TASK].rotate(_POS_INSTRUCTIONS)

        if parent is None:
            if preflight_facts:
                sc._rings[RING_PROJECT_FACTS].upsert_slot(
                    domain="preflight",
                    content=preflight_facts[:2000],
                    salience=0.8,
                )
            return sc

        # --- Copy from parent rings ---
        try:
            from .cryptex import CryptexMemory
            if not isinstance(parent, CryptexMemory):
                return sc
        except ImportError:
            return sc

        # Project facts
        facts_ring = parent.get_ring(RING_PROJECT_FACTS)
        if facts_ring:
            for slot in facts_ring.get_active_slots()[:12]:
                sc._rings[RING_PROJECT_FACTS].add_slot(deepcopy(slot))

        # Credentials (active + cross-read)
        cred_ring = parent.get_ring(RING_CREDENTIALS)
        if cred_ring:
            seen_domains: set[str] = set()
            for slot in cred_ring.get_active_slots()[:6]:
                sc._rings[RING_CREDENTIALS].add_slot(deepcopy(slot))
                seen_domains.add(slot.domain)
            for _, slot in cred_ring.cross_read(max_per_position=2):
                if slot.domain not in seen_domains:
                    sc._rings[RING_CREDENTIALS].add_slot(deepcopy(slot))

        # Tactical goals → go into 'inherited' position with lower salience
        goals_ring = parent.get_ring(RING_TACTICAL_GOALS)
        if goals_ring:
            for slot in goals_ring.get_active_slots()[:6]:
                inherited = deepcopy(slot)
                inherited.salience = min(inherited.salience, 0.5)
                sc._rings[RING_TACTICAL_GOALS].add_slot(
                    inherited, position=_POS_INHERITED,
                )
            sc._rings[RING_TACTICAL_GOALS].rotate(_POS_ACTIVE)

        # Skills (searched by task relevance)
        skills_ring = parent.get_ring(RING_SKILLS)
        if skills_ring:
            results = skills_ring.search(task, max_results=5)
            for _, slot, _ in results:
                sc._rings[RING_SKILLS].add_slot(deepcopy(slot))

        # User model → store as knowledge in a dedicated position
        user_ring = parent.get_ring(RING_USER_MODEL)
        if user_ring:
            um_slots = user_ring.get_active_slots()[:3]
            if um_slots:
                um_text = "User preferences:\n" + "\n".join(
                    f"  - {s.content[:150]}" for s in um_slots
                )
                sc._rings[SUB_RING_KNOWLEDGE].upsert_slot(
                    domain="UserModel",
                    content=um_text,
                    salience=0.6,
                    source="parent",
                    position=_POS_GENERAL,
                )

        if preflight_facts:
            sc._rings[RING_PROJECT_FACTS].upsert_slot(
                domain="preflight",
                content=preflight_facts[:2000],
                salience=0.75,
                source="preflight",
            )

        return sc

    # ------------------------------------------------------------------
    # Ring access
    # ------------------------------------------------------------------

    def get_ring(self, ring_id: str) -> WMRing | None:
        return self._rings.get(ring_id)

    @property
    def ring_ids(self) -> list[str]:
        return list(self._rings.keys())

    # ------------------------------------------------------------------
    # Context composition — proportional budget + rotation
    # ------------------------------------------------------------------

    def compose_context(
        self,
        token_budget: int | None = None,
    ) -> list[dict[str, str]]:
        """Compose system messages with proportional ring budgets.

        Task ring (instructions position) is always forced.  Remaining
        budget is split proportionally across other rings by priority,
        with empty rings donating their share.
        """
        if token_budget is None:
            token_budget = self._context_budget
        used = 0
        parts: list[str] = []

        # --- Task: always render locked positions (instructions + stack + ownership) ---
        task_ring = self._rings.get(SUB_RING_TASK)
        if task_ring:
            old_pos = task_ring.active_position
            for locked_pos in (
                _POS_INSTRUCTIONS,
                _POS_TECH_STACK,
                _POS_FILE_OWNERSHIP,
                _POS_FILE_CONTEXT,
            ):
                slots = task_ring.positions.get(locked_pos) or []
                for slot in sorted(slots, key=lambda s: s.salience, reverse=True):
                    parts.append(slot.content)
                    used += _estimate_tokens(slot.content)
            # Cross-read manifest + briefing as compact summaries
            cross = task_ring.cross_read(max_per_position=1)
            if cross:
                xr_lines: list[str] = []
                for pos_id, slot in cross:
                    if pos_id in (
                        _POS_INSTRUCTIONS,
                        _POS_TECH_STACK,
                        _POS_FILE_OWNERSHIP,
                        _POS_FILE_CONTEXT,
                    ):
                        continue
                    xr_lines.append(f"  [{pos_id}] {slot.content[:120]}")
                if xr_lines:
                    xr_text = "\n".join(xr_lines)
                    parts.append(xr_text)
                    used += _estimate_tokens(xr_text)
            task_ring.rotate(old_pos)

        non_task_budget = max(0, token_budget - used)

        # --- Proportional budget allocation for other rings ---
        ordered = sorted(
            ((rid, p) for rid, p in self._priorities.items()
             if rid != SUB_RING_TASK),
            key=lambda x: x[1],
            reverse=True,
        )

        # First pass: compute allocations and detect empty rings
        ring_allocs: list[tuple[str, float, int]] = []
        for ring_id, priority in ordered:
            ring = self._rings.get(ring_id)
            has_content = False
            if ring:
                has_content = bool(ring.get_active_slots())
                if not has_content:
                    for pos_id in ring.position_ids:
                        if ring.positions.get(pos_id):
                            has_content = True
                            break
            if has_content:
                ring_allocs.append((ring_id, priority, 0))

        # Second pass: distribute with donated budget
        active_weight = sum(p for _, p, _ in ring_allocs)
        distributable = non_task_budget
        if active_weight > 0:
            ring_allocs = [
                (rid, p, int((p / active_weight) * distributable))
                for rid, p, _ in ring_allocs
            ]

        for ring_id, _, ring_budget in ring_allocs:
            if ring_budget <= 50:
                continue
            ring = self._rings.get(ring_id)
            if not ring:
                continue

            section = self._render_ring_with_rotation(ring_id, ring, ring_budget)
            if section:
                cost = _estimate_tokens(section)
                parts.append(section)
                used += cost

        content = "\n\n".join(parts)
        if not content:
            return []
        return [{"role": "system", "content": content}]

    def _render_ring_with_rotation(
        self, ring_id: str, ring: WMRing, budget: int,
    ) -> str:
        """Render a ring using rotation: active position full, others as cross-read."""
        _boost_skills = (
            ring_id == RING_SKILLS and self._skill_boost_remaining > 0
        )
        header = (
            _SKILL_BOOST_HEADER
            if _boost_skills
            else _RING_HEADERS.get(ring_id, f"[{ring_id.upper()}]")
        )
        lines: list[str] = [header]

        active_budget = int(budget * 0.7)
        cross_budget = budget - active_budget

        # --- Active position: full render ---
        active_slots = ring.get_active_slots()
        if active_slots:
            sorted_slots = sorted(active_slots, key=lambda s: s.salience, reverse=True)
            for slot in sorted_slots:
                max_content = self._slot_render_limit(ring_id)
                if _boost_skills:
                    max_content = max(max_content, 800)
                content = slot.content[:max_content]
                if len(slot.content) > max_content:
                    content += "..."
                domain_tag = f" ({slot.domain})" if slot.domain else ""
                line = f"  - {content}{domain_tag}"
                lines.append(line)
                if _estimate_tokens("\n".join(lines)) > active_budget:
                    lines.pop()
                    break

        # --- Cross-read: compact summaries from dormant positions ---
        cross_items = ring.cross_read(max_per_position=2)
        if cross_items:
            xr_lines: list[str] = []
            limit = self._cross_read_limit(ring_id)
            for pos_id, slot in cross_items[:_MAX_CROSS_READ_LINES]:
                summary = slot.content[:limit]
                if len(slot.content) > limit:
                    summary += "…"
                tag = f" ({slot.domain})" if slot.domain else ""
                xr_lines.append(f"    o [{pos_id}]{tag} {summary}")
            if xr_lines:
                xr_text = "\n".join(xr_lines)
                if _estimate_tokens(xr_text) <= cross_budget:
                    lines.append("  Peripheral:")
                    lines.extend(xr_lines)

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    @staticmethod
    def _slot_render_limit(ring_id: str) -> int:
        """Max chars to render per slot in the active position."""
        limits = {
            SUB_RING_PROGRESS: 600,
            SUB_RING_KNOWLEDGE: 500,
            RING_PROJECT_FACTS: 300,
            RING_CREDENTIALS: 150,
            RING_TACTICAL_GOALS: 250,
            RING_SKILLS: 300,
        }
        return limits.get(ring_id, 300)

    @staticmethod
    def _cross_read_limit(ring_id: str) -> int:
        """Max chars per slot in cross-read (dormant position summaries).

        Longer than a one-liner, shorter than active-position render.
        Must preserve enough semantics to be useful after compaction.
        """
        limits = {
            SUB_RING_PROGRESS: 180,
            SUB_RING_KNOWLEDGE: 160,
            RING_PROJECT_FACTS: 120,
            RING_TACTICAL_GOALS: 140,
            RING_SKILLS: 100,
        }
        return limits.get(ring_id, 120)

    # ------------------------------------------------------------------
    # Tool result absorption — with ring rotation
    # ------------------------------------------------------------------

    def absorb_tool_result(
        self,
        tool_name: str,
        args: dict,
        result_str: str,
        is_error: bool,
    ) -> None:
        """Update rings from a tool outcome, rotating by file/area."""
        self._tools_used[tool_name] = self._tools_used.get(tool_name, 0) + 1

        path = args.get("path", args.get("file_path", args.get("pattern", "")))
        ledger = getattr(self, "_file_ledger", None)
        if ledger is not None and path and tool_name in _WRITE_TOOLS | _READ_TOOLS:
            try:
                from nls.tools.agent_tools.file_ledger import normalize_ledger_path
                norm = normalize_ledger_path(str(path))
                if norm:
                    ctx = ledger.format_path_context(norm, {})
                    task_ring = self._rings.get(SUB_RING_TASK)
                    if task_ring:
                        task_ring.upsert_slot(
                            domain=f"file_ctx:{norm[-40:]}",
                            content=ctx[:1200],
                            slot_type="fact",
                            salience=0.95,
                            source="ledger",
                            position=_POS_FILE_CONTEXT,
                        )
                        task_ring.rotate(_POS_FILE_CONTEXT)
            except Exception:
                pass

        if tool_name in _WRITE_TOOLS and not is_error:
            if path:
                if tool_name == "delete_file":
                    self._files_modified = [f for f in self._files_modified if f != path]
                    self._files_created = [f for f in self._files_created if f != path]
                elif tool_name in ("edit", "move_file"):
                    if path not in self._files_modified:
                        self._files_modified.append(path)
                        if len(self._files_modified) > self._MAX_TRACKED_FILES:
                            self._files_modified = self._files_modified[-self._MAX_TRACKED_FILES:]
                else:
                    if path not in self._files_created:
                        self._files_created.append(path)
                        if len(self._files_created) > self._MAX_TRACKED_FILES:
                            self._files_created = self._files_created[-self._MAX_TRACKED_FILES:]

                # Rotate knowledge ring to this file
                file_pos = _file_to_position(path)
                self._rings[SUB_RING_KNOWLEDGE].rotate(file_pos)
                action = {
                    "write": "Created", "delete_file": "Deleted",
                    "edit": "Edited", "move_file": "Moved",
                }.get(tool_name, "Modified")
                self._rings[SUB_RING_KNOWLEDGE].upsert_slot(
                    domain="file_action",
                    content=f"{action} {path}",
                    salience=0.8,
                    source="tool",
                    position=file_pos,
                )

                # Rotate progress to the file's area
                area = _classify_file(path)
                self._rings[SUB_RING_PROGRESS].rotate(area)

            self._update_progress()

        elif tool_name in _READ_TOOLS and not is_error:
            path = args.get("path", args.get("pattern", ""))
            if path and path not in self._files_read:
                self._files_read.append(path)
                if len(self._files_read) > self._MAX_TRACKED_FILES:
                    self._files_read = self._files_read[-self._MAX_TRACKED_FILES:]

            if tool_name == "read" and result_str and path:
                file_pos = _file_to_position(path)
                self._rings[SUB_RING_KNOWLEDGE].rotate(file_pos)
                preview = result_str[:500]
                self._rings[SUB_RING_KNOWLEDGE].upsert_slot(
                    domain="file_content",
                    content=f"Read {path}: {preview}",
                    salience=0.85,
                    source="tool",
                    position=file_pos,
                )
                # Metadata index — not full body (chat or read cache holds content)
                meta_line = result_str.split("\n", 1)[0][:80]
                if "[CACHED READ" in result_str:
                    meta_line = result_str.split("\n", 2)[1][:120] if "\n" in result_str else meta_line
                total_hint = ""
                for line in result_str.split("\n"):
                    if "lines" in line and "bytes" in line:
                        total_hint = line.strip()[:100]
                        break
                    if "Showing lines" in line or "more lines" in line:
                        total_hint = line.strip()[:100]
                body = f"Read {path}"
                if total_hint:
                    body += f" — {total_hint}"
                elif meta_line:
                    body += f" — {meta_line}"
                cache_m = None
                for line in result_str.split("\n"):
                    if line.startswith("cache_key="):
                        cache_m = line.split("=", 1)[-1].strip()
                        break
                if cache_m:
                    body += f" [{cache_m}]"
                self._rings[SUB_RING_KNOWLEDGE].upsert_slot(
                    domain=f"FileReadIndex:{path[-40:]}",
                    content=body,
                    salience=0.9,
                    source="tool",
                    position=file_pos,
                )
            elif tool_name in ("grep", "glob", "semantic_search") and result_str:
                self._rings[SUB_RING_KNOWLEDGE].upsert_slot(
                    domain=f"search:{args.get('pattern', args.get('query', ''))[:40]}",
                    content=result_str[:300],
                    salience=0.7,
                    source="tool",
                )

        elif tool_name in _EXEC_TOOLS:
            cmd = args.get("command", "")[:100]
            if is_error and result_str:
                err_preview = result_str[:200]
                if err_preview not in self._errors_seen:
                    self._errors_seen.append(err_preview)
                    if len(self._errors_seen) > 20:
                        self._errors_seen = self._errors_seen[-20:]
                self._rings[SUB_RING_KNOWLEDGE].upsert_slot(
                    domain=f"BashError:{cmd[:50]}",
                    content=f"Command `{cmd}` failed: {err_preview}",
                    salience=0.85,
                    source="tool",
                )
            elif not is_error:
                self._rings[SUB_RING_PROGRESS].upsert_slot(
                    domain=f"ResolvedAttempt:{cmd[:40]}",
                    content=f"bash ok: `{cmd[:80]}`",
                    salience=0.75,
                    source="tool",
                )
                self._update_progress()

        from nls.brain.cryptex_tool_absorption import absorb_delegate_tool_result

        absorb_delegate_tool_result(
            self,
            tool_name,
            args,
            result_str,
            is_error,
            guardrails_registry=getattr(self, "_guardrails_registry", None),
            delegate_number=int(getattr(self, "_delegate_number", 0) or 0),
        )

    @property
    def _all_files_touched(self) -> list[str]:
        return self._files_created + self._files_modified

    def _update_progress(self) -> None:
        """Rebuild progress summary in the currently active progress position."""
        area = self._rings[SUB_RING_PROGRESS].active_position
        area_created = [f for f in self._files_created if _classify_file(f) == area]
        area_modified = [f for f in self._files_modified if _classify_file(f) == area]
        area_read = [f for f in self._files_read if _classify_file(f) == area]

        parts: list[str] = []
        if area_created:
            parts.append(
                f"Files created ({len(area_created)}): "
                + ", ".join(area_created[-10:])
            )
        if area_modified:
            parts.append(
                f"Files modified ({len(area_modified)}): "
                + ", ".join(area_modified[-10:])
            )
        if area_read:
            parts.append(
                f"Files read ({len(area_read)}): "
                + ", ".join(area_read[-8:])
            )
        if self._errors_seen:
            parts.append(
                f"Errors ({len(self._errors_seen)}): "
                + "; ".join(e[:80] for e in self._errors_seen[-3:])
            )
        tool_summary = ", ".join(
            f"{k}:{v}" for k, v in sorted(
                self._tools_used.items(), key=lambda x: x[1], reverse=True,
            )[:6]
        )
        if tool_summary:
            parts.append(f"Tools: {tool_summary}")

        if parts:
            self._rings[SUB_RING_PROGRESS].upsert_slot(
                domain=f"Summary:{area}",
                content="\n".join(parts),
                salience=0.95,
                source="system",
                position=area,
            )

        # Also maintain a global summary in each non-active area for cross-read
        for other_area in (_POS_FRONTEND, _POS_BACKEND, _POS_GENERAL):
            if other_area == area:
                continue
            oc = [f for f in self._files_created if _classify_file(f) == other_area]
            om = [f for f in self._files_modified if _classify_file(f) == other_area]
            if oc or om:
                self._rings[SUB_RING_PROGRESS].upsert_slot(
                    domain=f"Summary:{other_area}",
                    content=f"{len(oc)} created, {len(om)} modified",
                    salience=0.6,
                    source="system",
                    position=other_area,
                )

    # ------------------------------------------------------------------
    # Compaction integration
    # ------------------------------------------------------------------

    def absorb_compaction(self, anchor: Any) -> None:
        """Merge CompactionAnchor data into progress/knowledge rings."""
        if anchor is None:
            return
        done = getattr(anchor, "progress_done", [])
        pending = getattr(anchor, "progress_pending", [])
        decisions = getattr(anchor, "decisions", [])
        files_mod = getattr(anchor, "files_modified", [])
        files_rd = getattr(anchor, "files_read", [])

        for f in files_mod:
            if f not in self._files_modified and f not in self._files_created:
                self._files_modified.append(f)
        for f in files_rd:
            if f not in self._files_read:
                self._files_read.append(f)

        if done:
            self._rings[SUB_RING_PROGRESS].upsert_slot(
                domain="CompactionDone",
                content="Completed: " + " | ".join(done[-10:]),
                salience=0.85,
                source="compaction",
            )
        if pending:
            self._rings[SUB_RING_PROGRESS].upsert_slot(
                domain="CompactionPending",
                content="Still pending: " + " | ".join(pending[-5:]),
                salience=0.8,
                source="compaction",
            )
        if decisions:
            self._rings[SUB_RING_KNOWLEDGE].upsert_slot(
                domain="KeyDecisions",
                content="Decisions: " + " | ".join(decisions[-5:]),
                salience=0.8,
                source="compaction",
            )
        superseded = getattr(anchor, "superseded_attempts", [])
        if superseded:
            self._rings[SUB_RING_PROGRESS].upsert_slot(
                domain="SupersededAttempts",
                content="Superseded: " + " | ".join(superseded[-8:]),
                salience=0.7,
                source="compaction",
            )
        blockers = getattr(anchor, "open_blockers", [])
        if blockers:
            self._rings[SUB_RING_KNOWLEDGE].upsert_slot(
                domain="OpenBlockers",
                content="Blockers: " + " | ".join(blockers[-5:]),
                salience=0.95,
                source="compaction",
            )

        self._update_progress()

    # ------------------------------------------------------------------
    # Post-completion digest
    # ------------------------------------------------------------------

    _DIGEST_PROMPT = (
        "You are compressing a sub-agent's working memory into a concise "
        "knowledge digest for the orchestrator.\n\n"
        "Given the sub-agent's ring contents below, produce a JSON object "
        "with EXACTLY these fields:\n"
        '{\n'
        '  "task_summary": "1-2 sentence summary of what was accomplished",\n'
        '  "files_created": ["path1", "path2"],\n'
        '  "files_modified": ["path1"],\n'
        '  "decisions": ["decision 1: rationale", ...],\n'
        '  "knowledge_gained": ["fact 1", "fact 2", ...],\n'
        '  "blockers": ["unresolved issue 1", ...],\n'
        '  "credentials_used": ["CRED_NAME: brief note", ...]\n'
        '}\n\n'
        "Be concise. Preserve exact file paths. Return ONLY the JSON."
    )

    async def compress_to_digest(
        self,
        vllm_client: Any,
    ) -> dict[str, Any]:
        """Micro-inference compression of all rings into structured digest.

        Falls back to :meth:`raw_ring_dump` if the vLLM call fails.
        """
        ring_text = self._render_all_rings_for_compression()
        if not ring_text:
            return self.raw_ring_dump()

        if vllm_client is None:
            logger.warning("SubCryptex digest: vllm_client is None, using raw dump")
            return self.raw_ring_dump()

        import asyncio
        try:
            if hasattr(vllm_client, "_ensure_client"):
                vllm_client._ensure_client()
            result = await asyncio.wait_for(
                vllm_client.generate(
                    messages=[
                        {"role": "system", "content": self._DIGEST_PROMPT},
                        {"role": "user", "content": ring_text},
                    ],
                    adapter_name=None,
                    max_tokens=512,
                    temperature=0.2,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                ),
                timeout=30.0,
            )
            text = (result.text or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                digest = json.loads(text[start:end + 1])
                if isinstance(digest, dict):
                    return digest
        except Exception as exc:
            logger.warning(
                "SubCryptex digest compression failed: %s: %s",
                type(exc).__name__, exc,
            )

        return self.raw_ring_dump()

    def raw_ring_dump(self) -> dict[str, Any]:
        """Fallback: return ring contents without LLM compression."""
        dump: dict[str, Any] = {
            "task_summary": "",
            "files_created": list(self._files_created),
            "files_modified": list(self._files_modified),
            "decisions": [],
            "knowledge_gained": [],
            "blockers": list(self._errors_seen[-5:]),
            "credentials_used": [],
        }

        # Extract from ALL knowledge ring positions
        kr = self._rings.get(SUB_RING_KNOWLEDGE)
        if kr:
            for pos_id, slots in kr.positions.items():
                for slot in slots:
                    if slot.domain.startswith("KeyDecision"):
                        dump["decisions"].append(slot.content[:200])
                    else:
                        dump["knowledge_gained"].append(
                            f"[{pos_id}] {slot.content[:200]}"
                        )

        cr = self._rings.get(RING_CREDENTIALS)
        if cr:
            for slot in cr.get_active_slots():
                dump["credentials_used"].append(f"{slot.domain}: (present)")

        return dump

    def _render_all_rings_for_compression(self) -> str:
        """Render all ring positions into text for the digest LLM call."""
        sections: list[str] = []
        for ring_id, ring in self._rings.items():
            if ring_id == SUB_RING_TASK:
                continue
            ring_lines: list[str] = []
            for pos_id, slots in ring.positions.items():
                if not slots:
                    continue
                ring_lines.append(f"### {pos_id}")
                for s in slots:
                    domain_tag = f" [{s.domain}]" if s.domain else ""
                    ring_lines.append(f"- {s.content[:300]}{domain_tag}")
            if ring_lines:
                sections.append(
                    f"## {ring.spec.display_name}\n" + "\n".join(ring_lines)
                )
        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Orchestrator manipulation API
    # ------------------------------------------------------------------

    def read_ring(self, ring_id: str) -> list[dict[str, Any]]:
        """Return ring slots as dicts for orchestrator inspection."""
        ring = self._rings.get(ring_id)
        if not ring:
            return []
        result: list[dict[str, Any]] = []
        for slot in ring.get_active_slots():
            result.append({
                "domain": slot.domain,
                "content": slot.content[:500],
                "salience": round(slot.salience, 2),
                "source": slot.source,
                "slot_type": slot.slot_type,
            })
        return result

    def read_all_rings_summary(self) -> str:
        """Compact summary of all rings for orchestrator ``summary`` action."""
        lines: list[str] = []
        for ring_id, ring in self._rings.items():
            pos_count = sum(1 for s in ring.positions.values() if s)
            active_slots = ring.get_active_slots()
            active_pos = ring.active_position
            if not pos_count:
                lines.append(f"  {ring.spec.display_name}: (empty)")
                continue
            domains = ", ".join(s.domain or s.content[:30] for s in active_slots[:5])
            lines.append(
                f"  {ring.spec.display_name} [active={active_pos}, "
                f"{len(active_slots)} slots, {pos_count} positions]: {domains}"
            )
        lines.append(f"  Files created: {len(self._files_created)}")
        lines.append(f"  Files modified: {len(self._files_modified)}")
        lines.append(f"  Files read: {len(self._files_read)}")
        lines.append(f"  Errors: {len(self._errors_seen)}")
        return "\n".join(lines)

    _MAX_ORCHESTRATOR_DIRECTIVES = 8

    def upsert_orchestrator_directive(
        self,
        content: str,
        *,
        domain: str = "hint",
        salience: float = 0.95,
        replace_domain: bool = True,
    ) -> bool:
        """Store an orchestrator hint in the high-priority orchestrator ring.

        When ``replace_domain`` is True, a new directive with the same
        ``domain`` replaces the previous one (e.g. ``finalize`` overwrites
        an older finalize mandate).
        """
        if not content.strip():
            return False
        ring = self._rings.get(SUB_RING_ORCHESTRATOR)
        if ring is None:
            return False
        ring.upsert_slot(
            domain=domain,
            content=content.strip(),
            salience=min(1.0, max(0.5, salience)),
            source="orchestrator",
            slot_type="instruction",
        )
        return True

    def upsert(
        self,
        ring_id: str,
        domain: str,
        content: str,
        salience: float = 0.9,
    ) -> bool:
        """Orchestrator pushes content into a sub-agent ring."""
        if ring_id == SUB_RING_ORCHESTRATOR:
            return self.upsert_orchestrator_directive(
                content, domain=domain, salience=salience,
            )
        ring = self._rings.get(ring_id)
        if ring is None:
            return False
        if ring_id == SUB_RING_TASK:
            return False
        ring.upsert_slot(
            domain=domain,
            content=content,
            salience=salience,
            source="orchestrator",
        )
        return True

    def boost_priority(self, ring_id: str, boost: float = 0.2) -> bool:
        """Orchestrator boosts a ring's priority for next compose_context."""
        if ring_id not in self._priorities:
            return False
        if ring_id == SUB_RING_TASK:
            return False
        self._priorities[ring_id] = min(
            1.0, self._priorities[ring_id] + boost,
        )
        return True

    def activate_skill_discovery_boost(self, reason: str = "", ttl_iters: int = 8) -> None:
        """Promote skills ring when delegate is stuck or receives a hint."""
        from nls.agentic.skill_discovery_boost import (
            SKILL_DISCOVERY_PROMPT,
            SKILL_DISCOVERY_SLOT_DOMAIN,
        )

        self._skill_boost_remaining = max(self._skill_boost_remaining, ttl_iters)
        self.boost_priority(RING_SKILLS, 0.42)
        ring = self._rings.get(RING_SKILLS)
        if ring is None:
            return
        body = SKILL_DISCOVERY_PROMPT
        if reason:
            body = f"{body}\n\nTrigger: {reason[:200]}"
        ring.upsert_slot(
            domain=SKILL_DISCOVERY_SLOT_DOMAIN,
            content=body,
            slot_type="skill",
            salience=1.0,
            source="stall_boost",
        )

    # ------------------------------------------------------------------
    # Hook factories (for wiring into LoopHooks)
    # ------------------------------------------------------------------

    def make_transform_hook(self) -> Callable[[list[dict]], list[dict]]:
        """Return a ``transform_context`` callable for the sub-agent loop.

        Calls tick() for decay, then replaces the first system message
        with the SubCryptex-composed context.
        """
        sc = self

        def _transform(ctx: list[dict]) -> list[dict]:
            sc.tick()
            fresh = sc.compose_context()
            if not fresh:
                return ctx
            sys_indices = [
                i for i, m in enumerate(ctx)
                if m.get("role") == "system"
            ]
            if sys_indices:
                ctx[sys_indices[0]] = fresh[0]
            return ctx

        return _transform

    def make_compaction_hook(self) -> Callable:
        """Return an ``on_compaction`` callable that feeds the anchor
        into the SubCryptex's progress/knowledge rings."""
        sc = self

        def _on_compaction(anchor: Any) -> None:
            sc.absorb_compaction(anchor)

        return _on_compaction

    def make_after_tool_hook(
        self,
        parent_hook: Callable | None = None,
    ) -> Callable:
        """Return an ``on_after_tool`` callable that feeds tool results
        into the SubCryptex's rings with rotation."""
        sc = self

        def _on_after_tool(
            tool_name: str, args: dict, result: Any,
        ) -> None:
            if parent_hook is not None:
                try:
                    parent_hook(tool_name, args, result)
                except Exception:
                    pass
            result_str = ""
            is_error = False
            if hasattr(result, "content"):
                result_str = result.content or ""
                is_error = getattr(result, "is_error", False)
            elif isinstance(result, str):
                result_str = result
            sc.absorb_tool_result(tool_name, args, result_str, is_error)

        return _on_after_tool
