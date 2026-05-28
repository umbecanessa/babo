"""Tool-success → AgentMode transitions (Tier 1 & 2 orchestration guidance).

Centralizes when the loop should switch operational mode after specific tools
succeed, so the model gets the right tool palette without relying on switch_mode.
Explicit switch_mode calls still win for USER_MODE_SWITCH_GRACE_ITERS iterations.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from .coordinator_guard import _BUILD_TODO_RE, plan_requires_team_delegation
from .orchestration_policy import invalidate_tool_policy_cache
from .types import AgentMode, LoopState

logger = logging.getLogger(__name__)

USER_MODE_SWITCH_GRACE_ITERS = 3

_BUILD_TODO_TITLE_RE = _BUILD_TODO_RE

_MODE_HINTS: dict[str, str] = {
    "team_launch": (
        "MODE → MONITORING: wave is executing. Optional communicate(status), "
        "then await_delegates(summary='...'). Do not bash/write/edit yourself."
    ),
    "team_create": (
        "MODE → DELEGATING: team prepared. NEXT: team(action='launch', team_id=...)."
    ),
    "team_advance": (
        "MODE → EVALUATING: wave closed. Inspect deliverables, update plan/Kanban, "
        "then launch the next wave or accept_partial as needed."
    ),
    "team_advance_next_wave": (
        "MODE → DELEGATING: next wave team exists. Launch it — do not implement "
        "delegatable steps yourself."
    ),
    "plan_create_team": (
        "MODE → DELEGATING: team-shaped plan active. team(create) → team(launch) "
        "for pending delegatable steps."
    ),
    "plan_accept_partial": (
        "MODE → EVALUATING: partial step accepted. Review artifacts, then "
        "team(advance) or launch the next wave."
    ),
    "plan_fix_dependencies": (
        "MODE → PLANNING: dependency graph updated. Re-read plan, then delegate."
    ),
    "plan_delete": (
        "MODE → PLANNING: plan removed. Create a fresh plan or finish with a "
        "narrow solo task."
    ),
    "todo_add_build": (
        "MODE → PLANNING: build-style todo added. Create plan + delegate waves "
        "before write/bash."
    ),
    "dispatch_pending_launch": (
        "MODE → DELEGATING: next wave is prepared. team(action='launch', team_id=...) "
        "— do not implement wave work yourself."
    ),
}


@dataclass(frozen=True)
class ModeTransition:
    from_mode: AgentMode
    to_mode: AgentMode
    reason: str
    hint: str = ""
    refresh_schemas: bool = False


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def user_mode_switch_blocks_auto(state: LoopState) -> bool:
    """True when a recent explicit switch_mode should not be overridden."""
    return (state.iteration - state.user_mode_switch_iter) < USER_MODE_SWITCH_GRACE_ITERS


def apply_dispatch_mode(
    state: LoopState,
    dispatch_source: str,
    *,
    enable_delegation: bool,
) -> ModeTransition | None:
    """Loop-start mode from orchestration dispatch source (Tier 1)."""
    if not enable_delegation:
        return None
    if dispatch_source.startswith("pending_wave_launch:"):
        if state.active_mode == AgentMode.DELEGATING:
            return None
        return ModeTransition(
            state.active_mode,
            AgentMode.DELEGATING,
            reason="dispatch_pending_launch",
            hint=_MODE_HINTS["dispatch_pending_launch"],
        )
    return None


def _load_active_plan(plan_tool: Any | None) -> Any | None:
    if plan_tool is None or not hasattr(plan_tool, "get_store"):
        return None
    try:
        store = plan_tool.get_store()
        plan = store.find_active()
        if plan is not None and getattr(plan, "status", "") == "done":
            return None
        return plan
    except Exception:
        return None


def _plan_from_create_details(details: dict[str, Any], plan_tool: Any | None) -> Any | None:
    plan_id = details.get("plan_id", "")
    if not plan_id or plan_tool is None or not hasattr(plan_tool, "get_store"):
        return _load_active_plan(plan_tool)
    try:
        return plan_tool.get_store().load(plan_id)
    except Exception:
        return _load_active_plan(plan_tool)


def _candidate_from_tool(
    tool_name: str,
    args: dict[str, Any],
    details: dict[str, Any],
    *,
    plan_tool: Any | None,
) -> tuple[int, AgentMode, str] | None:
    """Return (priority, target_mode, reason) or None."""
    action = (details.get("action") or args.get("action") or "").strip().lower()

    if tool_name == "team":
        if action == "launch":
            return (100, AgentMode.MONITORING, "team_launch")
        if action == "create":
            return (90, AgentMode.DELEGATING, "team_create")
        if action == "advance":
            if details.get("next_team") or details.get("reconciled"):
                return (96, AgentMode.DELEGATING, "team_advance_next_wave")
            return (85, AgentMode.EVALUATING, "team_advance")

    if tool_name == "plan":
        if action == "create" and not details.get("is_error"):
            plan = _plan_from_create_details(details, plan_tool)
            if plan_requires_team_delegation(plan):
                return (88, AgentMode.DELEGATING, "plan_create_team")
        if action == "accept_partial" and details.get("wave_needs_advance"):
            return (84, AgentMode.EVALUATING, "plan_accept_partial")
        if action == "fix_dependencies":
            return (70, AgentMode.PLANNING, "plan_fix_dependencies")
        if action == "delete":
            return (68, AgentMode.PLANNING, "plan_delete")

    if tool_name == "todo" and action == "add":
        title = str(args.get("title", "") or args.get("description", "") or "")
        if _BUILD_TODO_TITLE_RE.search(title) and _load_active_plan(plan_tool) is None:
            return (65, AgentMode.PLANNING, "todo_add_build")

    return None


def compute_tool_mode_transition(
    state: LoopState,
    tool_calls: list[dict],
    results: list[Any],
    *,
    enable_delegation: bool,
    plan_tool: Any | None = None,
) -> ModeTransition | None:
    """Pick the highest-priority mode transition for this tool batch."""
    if not enable_delegation:
        return None
    if state.active_mode == AgentMode.RESPONDING:
        return None
    if user_mode_switch_blocks_auto(state):
        return None

    best: tuple[int, AgentMode, str] | None = None
    for tc, result in zip(tool_calls, results):
        if getattr(result, "is_error", True):
            continue
        fn = tc.get("function", {}) or {}
        name = fn.get("name", "")
        args = _parse_tool_args(fn.get("arguments", "{}"))
        details = getattr(result, "details", None) or {}
        cand = _candidate_from_tool(name, args, details, plan_tool=plan_tool)
        if cand is None:
            continue
        if best is None or cand[0] > best[0]:
            best = cand

    if best is None:
        return None

    _priority, to_mode, reason = best
    from_mode = state.active_mode

    # Coordinator transitions from EXECUTING/CHAT when delegation tools fire.
    _coordinator_modes = {
        AgentMode.PLANNING,
        AgentMode.DELEGATING,
        AgentMode.MONITORING,
        AgentMode.EVALUATING,
    }
    if from_mode == AgentMode.EXECUTING and to_mode not in _coordinator_modes:
        return None
    if from_mode == AgentMode.CHAT and to_mode != AgentMode.PLANNING:
        return None

    refresh = from_mode == to_mode and reason in (
        "team_advance",
        "plan_accept_partial",
    )
    if from_mode == to_mode and not refresh:
        return None

    hint = _MODE_HINTS.get(reason, "")
    return ModeTransition(
        from_mode=from_mode,
        to_mode=to_mode,
        reason=reason,
        hint=hint,
        refresh_schemas=refresh or from_mode != to_mode,
    )


def apply_tool_mode_transition(
    state: LoopState,
    transition: ModeTransition,
) -> bool:
    """Apply transition to state. Returns True if mode or schemas changed."""
    changed = transition.to_mode != state.active_mode
    if changed:
        state.active_mode = transition.to_mode
    if changed or transition.refresh_schemas:
        invalidate_tool_policy_cache(state)
        state.mode_override_count = 0
    if changed:
        logger.info(
            "[MODE-POLICY] %s → %s (%s)",
            transition.from_mode.value,
            transition.to_mode.value,
            transition.reason,
        )
    elif transition.refresh_schemas:
        logger.info(
            "[MODE-POLICY] refresh schemas in %s (%s)",
            transition.to_mode.value,
            transition.reason,
        )
    return changed or transition.refresh_schemas
