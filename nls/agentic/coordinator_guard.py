"""Guards to keep the orchestrator delegating instead of self-implementing."""

from __future__ import annotations

import re
from typing import Any, Callable

from .plan_store import Plan
from .profile_guard_policy import (
    EM_COLD_START_GOAL_THRESHOLD,
    em_cold_start_goal_blocks_enabled,
    em_pre_delegate_blocks_enabled,
    normalize_profile,
)
from .types import AgentMode, LoopConfig, LoopState

_BUILD_TODO_RE = re.compile(
    r"\b(build|develop|implement|scaffold|deploy|platform|monorepo|end-to-end)\b",
    re.IGNORECASE,
)
_IMPL_TOOLS = frozenset({"bash", "write", "edit", "server_install"})
_PRE_DELEGATE_OK = frozenset({
    "plan", "todo", "team", "switch_mode", "read", "list_dir", "grep", "glob",
    "wm", "contacts", "communicate", "ask_user", "delegate", "delegate_status",
    "wait", "await_delegates", "web_search", "web_fetch", "task_complete",
})
_BASH_CREATE_RE = re.compile(
    r"\b(mkdir|New-Item|touch|cp\b|copy\b|mv\b|move\b"
    r"|Out-File|Set-Content|Add-Content|>>|> "
    r"|git\s+init|git\s+push|git\s+commit"
    r"|git\s+add\b|git\s+config|git\s+remote\s+add"
    r"|gh\s+repo\s+create|npm\s+init|pip\s+install)",
    re.IGNORECASE,
)
# Modes where the orchestrator may self-fix after review (Dan: evaluate → patch).
_EXECUTING_ESCAPE_OK_MODES = frozenset({
    AgentMode.EVALUATING,
})

_BLOCK_MESSAGES: dict[str, str] = {
    "team_plan": (
        "BLOCKED: This plan expects team waves — do not self-implement until "
        "delegates are running or the wave is salvaged.\n"
        "While delegates run: team(action='inspect'), hint/intervene.\n"
        "After a failed/disbanded wave: plan(accept_partial) with artifact "
        "notes, then switch_mode(executing|evaluating) to patch gaps, or "
        "team(create) + launch for the next wave."
    ),
    "tactical_goals": (
        "BLOCKED: Multiple build/platform goals are queued but no plan exists "
        "yet. Create a plan first: plan(action='create', ...). For a large "
        "build use delegatable steps + team waves; for a small fix use a "
        "simple plan without delegatable steps, then switch_mode(executing)."
    ),
    "build_goals": (
        "BLOCKED: Goals mention building/implementing but no plan is active. "
        "Call plan(action='create', ...) before write/bash/edit, or "
        "plan(action='delete', ...) to archive a stale plan and finish solo "
        "in switch_mode(evaluating)."
    ),
}


def plan_requires_team_delegation(plan: Plan | None) -> bool:
    """True when the active plan expects team waves, not solo execution.

    Simple plans (no delegatable steps, or only one) may use EXECUTING directly.
    """
    if plan is None:
        return False
    delegatable = [s for s in plan.steps if s.delegatable]
    if len(delegatable) < 2:
        return False
    pending = [
        s for s in delegatable
        if s.status in ("pending", "in_progress")
    ]
    return bool(pending)


def plan_suppresses_raw_delegate(plan: Plan | None) -> bool:
    """True when raw delegate() must be hidden — use team waves instead."""
    if plan is None:
        return False
    return any(
        s.delegatable and s.status in ("pending", "in_progress")
        for s in plan.steps
    )


def hook_suppresses_raw_delegate(hooks: Any | None) -> bool:
    """Read LoopHooks.plan_suppresses_raw_delegate if present."""
    if hooks is None:
        return False
    fn = getattr(hooks, "plan_suppresses_raw_delegate", None)
    if fn is None:
        return False
    try:
        return bool(fn())
    except Exception:
        return False


def filter_stale_tactical_goals(
    goals: list[str],
    plan: Plan | None = None,
) -> list[str]:
    """Drop tactical goals superseded by done plan steps (plan-aligned only)."""
    from nls.agentic.plan_goal_hygiene import filter_stale_tactical_goals as _filter
    return _filter(goals, plan)


def prune_stale_tactical_goals_for_plan(
    wm: Any,
    plan_store: Any,
    plan_id: str,
) -> int:
    """Remove WM tactical goals that duplicate finished plan steps."""
    from nls.agentic.plan_goal_hygiene import prune_stale_tactical_goals_for_plan as _prune
    return _prune(wm, plan_store, plan_id)


def sync_goals_from_wm(
    state: LoopState,
    wm_get_tactical_goals: Callable[[], list[str]] | None,
    *,
    threshold: int = 3,
    max_goals: int = 5,
) -> int:
    """Copy WM tactical goals into loop state when extraction missed them."""
    if len(state.goals) >= threshold or wm_get_tactical_goals is None:
        return len(state.goals)
    try:
        wm_goals = wm_get_tactical_goals()
    except Exception:
        return len(state.goals)
    wm_goals = filter_stale_tactical_goals(wm_goals)
    if len(wm_goals) >= threshold:
        state.goals = wm_goals[:max_goals]
    return len(state.goals)


def pre_delegate_reason(
    state: LoopState,
    config: LoopConfig,
    *,
    plan_requires_team_delegation: bool,
    has_active_plan: bool,
    has_running_delegates: bool,
    has_non_terminal_team: bool,
    is_delegate_loop: bool,
    orchestrator_recovery: bool = False,
) -> str | None:
    """Why implementation is blocked, or None if allowed."""
    if not config.enable_delegation or is_delegate_loop:
        return None
    if state.active_mode in _EXECUTING_ESCAPE_OK_MODES:
        return None
    if has_running_delegates or has_non_terminal_team:
        return None
    if orchestrator_recovery:
        return None
    profile = normalize_profile(getattr(state, "orchestration_profile", None))
    if not em_pre_delegate_blocks_enabled(
        profile,
        plan_requires_team_delegation=plan_requires_team_delegation,
    ):
        return None
    if plan_requires_team_delegation:
        return "team_plan"
    # Goal heuristics only before any plan exists (cold-start nudge, EM only).
    if not has_active_plan and em_cold_start_goal_blocks_enabled(profile):
        if len(state.goals) >= EM_COLD_START_GOAL_THRESHOLD:
            return "tactical_goals"
        if any(_BUILD_TODO_RE.search(g or "") for g in state.goals):
            return "build_goals"
    return None


def must_delegate_before_impl(
    state: LoopState,
    config: LoopConfig,
    *,
    plan_requires_team_delegation: bool,
    has_active_plan: bool,
    has_running_delegates: bool,
    has_non_terminal_team: bool,
    is_delegate_loop: bool,
    orchestrator_recovery: bool = False,
) -> bool:
    """True when the orchestrator must launch teams instead of self-building."""
    return pre_delegate_reason(
        state,
        config,
        plan_requires_team_delegation=plan_requires_team_delegation,
        has_active_plan=has_active_plan,
        has_running_delegates=has_running_delegates,
        has_non_terminal_team=has_non_terminal_team,
        is_delegate_loop=is_delegate_loop,
        orchestrator_recovery=orchestrator_recovery,
    ) is not None


def block_em_executing_during_review(
    target_mode: AgentMode,
    *,
    active_mode: AgentMode,
    dispatch_source: str = "",
    has_pending_completion_reviews: bool = False,
    enable_delegation: bool,
    is_delegate_loop: bool,
) -> str | None:
    """Block orchestrator from switching to IC executing during EM review."""
    if not enable_delegation or is_delegate_loop:
        return None
    if target_mode != AgentMode.EXECUTING:
        return None
    src = (dispatch_source or "").strip()
    if (
        src.startswith("team_completion_review:")
        or has_pending_completion_reviews
    ):
        return (
            "Blocked: switch_mode(mode='executing') — you are the engineering "
            "manager reviewing delegate output, not a delegate.\n"
            "Stay in evaluating: read/list_dir their deliverables, then "
            "team(intervene, decision='approve'|'hint'). "
            "Do NOT switch to executing to do their step yourself."
        )
    return None


def block_executing_mode_escape(
    target_mode: AgentMode,
    *,
    active_mode: AgentMode,
    plan_requires_team_delegation: bool,
    has_non_terminal_team: bool,
    enable_delegation: bool,
    is_delegate_loop: bool,
    orchestrator_recovery: bool = False,
    orchestration_profile: str | None = None,
) -> str | None:
    """Block switch_mode(executing) only for team-style plans before Wave 0."""
    if not enable_delegation or is_delegate_loop or orchestrator_recovery:
        return None
    if normalize_profile(orchestration_profile) != "orchestrated":
        return None
    if target_mode != AgentMode.EXECUTING:
        return None
    if active_mode in _EXECUTING_ESCAPE_OK_MODES:
        return None
    if has_non_terminal_team:
        return None
    if not plan_requires_team_delegation:
        return None
    if active_mode in (AgentMode.PLANNING, AgentMode.DELEGATING):
        return (
            "Blocked: switch_mode(mode='executing') while this plan has pending "
            "delegatable work but no launched team. Use team(create) → "
            "team(launch) → switch_mode(mode='monitoring').\n"
            "If you finished reviewing delegate output and need a small patch, "
            "stay in or return to switch_mode(mode='evaluating') first, then "
            "write/edit there (or enable recovery after plan(delete))."
        )
    return (
        "Blocked: switch_mode(mode='executing') — pending team waves on this "
        "plan. Review in switch_mode(evaluating), patch files there, or "
        "archive the plan with plan(delete) and finish remaining work solo."
    )


def pre_delegate_block_message(
    tool_name: str,
    args: dict[str, Any],
    *,
    active_mode: AgentMode,
    block_reason: str | None = None,
    orchestrator_recovery: bool = False,
    orchestration_profile: str | None = None,
) -> str | None:
    """Return a block message, or None if the tool call is allowed."""
    if active_mode in _EXECUTING_ESCAPE_OK_MODES:
        return None
    if orchestrator_recovery:
        return None
    profile = normalize_profile(orchestration_profile)
    if block_reason == "team_plan" and profile != "orchestrated":
        return None
    if block_reason in ("tactical_goals", "build_goals"):
        if not em_cold_start_goal_blocks_enabled(profile):
            return None
    if tool_name in _PRE_DELEGATE_OK:
        return None
    if tool_name not in _IMPL_TOOLS:
        return None
    if tool_name == "bash":
        cmd = str(args.get("command", "") or "")
        if not _BASH_CREATE_RE.search(cmd):
            return None
    if block_reason and block_reason in _BLOCK_MESSAGES:
        return _BLOCK_MESSAGES[block_reason]
    if profile != "orchestrated":
        return None
    return _BLOCK_MESSAGES["team_plan"]


def coordinator_nudge_pre_delegate(block_reason: str | None = None) -> str:
    if block_reason == "tactical_goals":
        return (
            "STOP — multiple build goals but no active plan. "
            "Call plan(action='create', ...) first. For large work use "
            "delegatable steps + team(create/launch). For a small solo fix, "
            "use a simple plan without delegatable steps."
        )
    if block_reason == "build_goals":
        return (
            "STOP — goals require implementation but there is no active plan. "
            "Create plan(action='create') or plan(action='fix_dependencies') "
            "on the active plan — avoid plan(delete) while steps are done."
        )
    return (
        "STOP — this plan expects team waves, but no team is launched. "
        "Call team(action='create') → team(action='launch'), then "
        "switch_mode(mode='monitoring') and await_delegates(summary='...'). "
        "For a small solo task, use a simple plan without delegatable steps."
    )


def recovery_mode_system_note() -> str:
    return (
        "[ORCHESTRATOR RECOVERY] Wave failed, disbanded, or accept_partial "
        "applied — you may write/edit/bash in executing or evaluating mode "
        "to close small gaps ONLY when no delegates are actively running on "
        "that wave. PREFER team(create/launch) for large delegatable steps; "
        "do NOT plan(create) from scratch if artifacts exist on disk. "
        "After accept_partial, call team(advance) if the wave is still open. "
        "When delegates are running, await_delegates(summary='...') — "
        "not task_complete."
    )


def delegation_hallucination_nudge() -> str:
    """Injected when EM assumes delegates run but none are active."""
    return (
        "[DELEGATION CHECK] No sub-agents are running. You have NOT launched "
        "a wave yet (or the prior wave already finished).\n"
        "Do NOT switch to monitoring or call await_delegates until "
        "team(action='create') → team(action='launch') succeeds.\n"
        "If team(launch) failed on dependencies, call "
        "plan(action='fix_dependencies') — NOT plan(delete)."
    )


_RECENT_TEAM_INSPECT_WINDOW = 12


def record_team_inspect(state: LoopState, team_id: str) -> None:
    """Remember a successful team(inspect) for monitoring-mode advance guard."""
    tid = (team_id or "").strip()
    if not tid:
        return
    recent = state.recent_team_inspect_ids
    recent.append(tid)
    if len(recent) > _RECENT_TEAM_INSPECT_WINDOW:
        del recent[: len(recent) - _RECENT_TEAM_INSPECT_WINDOW]


def monitoring_advance_block_message(
    state: LoopState,
    team_id: str,
) -> str | None:
    """Soft guard: in MONITORING, require inspect on the same team before advance."""
    if state.active_mode != AgentMode.MONITORING:
        return None
    tid = (team_id or "").strip()
    if not tid:
        return "team_id is required for team(action='advance')."
    recent = state.recent_team_inspect_ids
    if tid not in recent[-_RECENT_TEAM_INSPECT_WINDOW:]:
        return (
            f"BLOCKED (monitoring): call team(action='inspect', "
            f"team_id='{tid}') to review this wave's status, then "
            f"team(action='advance', team_id='{tid}')."
        )
    return None
