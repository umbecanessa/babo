"""Plan work-state helpers — single source of truth for active vs recovery vs complete.

Orchestrator loops, schedulers, plan tool actions, and team wave finalization
all use these functions so a plan is never marked ``done`` while partial work
remains unresolved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from nls.agentic.plan_store import Plan, PlanStep, PlanStore

logger = logging.getLogger(__name__)

ACCEPT_PARTIAL_TAG = "[accept_partial]"
VERIFIED_ON_DISK_TAG = "[verified_on_disk]"
AUTO_SKIP_FORCE_TAG = "[auto-skipped on force complete]"


def step_has_acceptance_evidence(step: PlanStep) -> bool:
    notes = step.notes or ""
    return ACCEPT_PARTIAL_TAG in notes or VERIFIED_ON_DISK_TAG in notes


def step_blocks_plan_completion(step: PlanStep) -> bool:
    """True when this step prevents plan(action='complete')."""
    if step.status in ("pending", "in_progress", "failed"):
        return True
    if step.status == "skipped":
        return True
    if step.status != "done":
        return True
    return False


def incomplete_steps(plan: Plan) -> list[PlanStep]:
    return [s for s in plan.steps if step_blocks_plan_completion(s)]


def all_steps_properly_done(plan: Plan) -> bool:
    if not plan.steps:
        return False
    return all(s.status == "done" for s in plan.steps)


def plan_has_improper_closure(plan: Plan) -> bool:
    """True when plan was closed (done) but steps were abandoned without review."""
    if plan.status != "done":
        return False
    if not all_steps_properly_done(plan):
        return True
    if plan.audit is not None and not plan.audit.all_criteria_met:
        if plan.audit.issues:
            return True
    for s in plan.steps:
        if AUTO_SKIP_FORCE_TAG in (s.notes or ""):
            return True
    return False


def _teams_for_plan(team_manager: Any | None, plan_id: str) -> list[Any]:
    if team_manager is None:
        return []
    try:
        return [
            t for t in team_manager.list_teams(include_terminal=True)
            if getattr(t, "plan_id", None) == plan_id
        ]
    except Exception:
        return []


def plan_has_unresolved_partial_team(
    plan_id: str, team_manager: Any | None,
) -> bool:
    for team in _teams_for_plan(team_manager, plan_id):
        if getattr(team, "status", None) == "partial":
            for member in getattr(team, "members", []):
                if getattr(member, "status", None) in ("failed", "cancelled"):
                    step_id = getattr(member, "step_id", "")
                    return True
    return False


def plan_has_active_execution_team(
    plan_id: str, team_manager: Any | None,
) -> bool:
    for team in _teams_for_plan(team_manager, plan_id):
        st = getattr(team, "status", None)
        if st in ("active", "created", "paused"):
            return True
        if st == "active":
            for member in getattr(team, "members", []):
                if getattr(member, "status", None) in ("running", "pending"):
                    return True
    return False


def plan_needs_recovery(
    plan: Plan,
    team_manager: Any | None = None,
) -> bool:
    if plan.parent_id is not None:
        return False
    if plan.status in ("blocked", "failed"):
        return True
    if plan.status in ("planning", "in_progress"):
        if any(s.status == "failed" for s in plan.steps):
            return True
        if plan_has_unresolved_partial_team(plan.id, team_manager):
            return True
    if plan_has_improper_closure(plan):
        return True
    return False


def reopen_for_recovery(plan: Plan) -> bool:
    """Move a falsely-closed plan back to blocked for EM recovery."""
    if plan.parent_id is not None:
        return False
    if plan.status == "done" and plan_has_improper_closure(plan):
        plan.status = "blocked"
        issue = (
            "Plan was marked done while steps were incomplete or auto-skipped. "
            "Use switch_mode(evaluating), plan(verify), accept_partial per failed "
            "step, or delegate/sub_plan to finish remaining work."
        )
        if plan.audit is not None:
            if issue not in plan.audit.issues:
                plan.audit.issues.append(issue)
        plan.touch()
        return True
    if plan.status in ("planning", "in_progress") and any(
        s.status == "failed" for s in plan.steps
    ):
        plan.status = "blocked"
        plan.touch()
        return True
    return False


def mark_plan_blocked_for_partial_wave(
    plan: Plan,
    *,
    team_id: str,
    failed_step_ids: list[str],
) -> None:
    plan.status = "blocked"
    msg = (
        f"Wave {team_id} landed partial — failed steps: "
        f"{', '.join(failed_step_ids) or '(see team inspect)'}. "
        "EM review required before plan(complete)."
    )
    if plan.audit is not None:
        if msg not in plan.audit.issues:
            plan.audit.issues.append(msg)
    plan.touch()


def completion_gate_message(
    plan: Plan,
    team_manager: Any | None = None,
) -> str | None:
    """Human-readable reason when plan(action='complete') must be rejected."""
    if plan_has_active_execution_team(plan.id, team_manager):
        return (
            "Cannot complete plan while a team wave is still running. "
            "Wait for delegates or use team(inspect)."
        )
    if plan_has_unresolved_partial_team(plan.id, team_manager):
        return (
            "Cannot complete plan — a partial wave has failed delegate(s). "
            "switch_mode(evaluating), verify artifacts, plan(accept_partial) "
            "per step, then team(advance)."
        )
    inc = incomplete_steps(plan)
    if inc:
        lines = "\n".join(
            f"  - [{s.id}] {s.label} ({s.status})" for s in inc[:12]
        )
        extra = f"\n  ... and {len(inc) - 12} more" if len(inc) > 12 else ""
        return (
            f"Cannot complete plan {plan.id}: {len(inc)} step(s) not properly done:\n"
            f"{lines}{extra}\n\n"
            "Finish each step (status=done with evidence), use accept_partial "
            "after a failed wave, or plan(sub_plan) / delegate for rework. "
            "Skipped steps do not count as done."
        )
    if (
        plan.audit is not None
        and getattr(plan.audit, "last_verified_at", None) is None
    ):
        return (
            "Cannot complete plan without verification. "
            "Call plan(action='verify') first."
        )
    if plan.audit is not None and plan.audit.issues:
        return (
            f"Cannot complete plan — verification reported {len(plan.audit.issues)} "
            f"issue(s). Fix them or accept_partial where appropriate, then verify again."
        )
    if plan.audit is not None and not plan.audit.all_criteria_met:
        return (
            "Cannot complete plan — last verify did not pass all criteria. "
            "Fix issues and plan(action='verify') again."
        )
    return None


def can_complete_plan(
    plan: Plan,
    team_manager: Any | None = None,
) -> bool:
    return completion_gate_message(plan, team_manager) is None


def find_recoverable_plan(
    store: PlanStore,
    team_manager: Any | None = None,
    *,
    reopen: bool = True,
) -> Plan | None:
    candidates: list[Plan] = []
    for plan in store.list_plans():
        if plan.parent_id is not None:
            continue
        if plan.status == "archived":
            continue
        if plan_needs_recovery(plan, team_manager):
            if reopen and reopen_for_recovery(plan):
                store.save(plan)
            candidates.append(plan)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.updated_at)


def resolve_work_plan(
    store: PlanStore,
    plan_id: str = "",
    team_manager: Any | None = None,
    *,
    reopen: bool = True,
) -> Plan | None:
    if plan_id:
        plan = store.load(plan_id)
        if plan is None:
            return None
        if reopen and plan_needs_recovery(plan, team_manager):
            if reopen_for_recovery(plan):
                store.save(plan)
        return plan
    active = store.find_active()
    if active is not None:
        return active
    return find_recoverable_plan(store, team_manager, reopen=reopen)


def work_plan_has_open_steps(plan: Plan) -> bool:
    """True when orchestration should continue (pending work or recovery)."""
    if plan_needs_recovery(plan):
        return True
    return bool(plan.pending_steps()) or any(
        s.status == "failed" for s in plan.steps
    )


def plan_open_step_count(plan: Plan) -> int:
    """Steps still blocking closure (excludes skipped)."""
    return len([
        s for s in plan.steps
        if s.status not in ("done", "skipped")
    ])


def format_plan_closure_nudge(plan_id: str) -> str:
    """Explicit verify → complete → task_complete when all steps are done."""
    return (
        f"[PLAN CLOSURE — ALL STEPS DONE]\n"
        f"plan_id={plan_id}\n"
        "1) Release check: read key paths; bash tests/smoke (server, build, curl)\n"
        "2) Confirm frontend ↔ backend contracts before trusting green verify\n"
        f"3) plan(action='verify', plan_id='{plan_id}')\n"
        f"4) plan(action='complete', plan_id='{plan_id}')\n"
        "5) task_complete(summary='...' with what runs and how to test)\n"
        "Do NOT launch another wave unless verify reports blockers."
    )


def format_wave_complete_wake(
    *,
    plan_id: str,
    team_id: str,
    team_name: str = "",
    outcome: str = "completed",
    ok_count: int = 0,
    fail_count: int = 0,
    pending_step_count: int | None = None,
) -> str:
    """Healthy wave landed — review path without PLAN RECOVERY alarm."""
    label = f"{team_name} [{team_id}]" if team_name else team_id
    lines = [
        f"[WAVE COMPLETE — REVIEW]",
        f"Team: {label}",
        f"Outcome: {outcome.upper()} ({ok_count} done, {fail_count} failed)",
        f"plan_id={plan_id}",
        "1) switch_mode(evaluating) if not already",
        "2) Production spot-check: read routes/services — not config-only",
        f"3) team(action='inspect', team_id='{team_id}')",
        "4) team(intervene, decision='approve') only after read/list_dir review",
        f"5) team(advance) ONLY when no members running and no pending reviews",
        "[BREADCRUMB] If others still run: await_delegates — not advance.",
    ]
    if pending_step_count == 0:
        lines.append(
            "6) All plan steps are done — after advance: "
            "plan(verify) → plan(complete) → task_complete."
        )
        lines.append(format_plan_closure_nudge(plan_id))
    elif pending_step_count is not None and pending_step_count > 0:
        lines.append(
            f"6) {pending_step_count} plan step(s) still open after this wave — "
            "launch the next wave or delegate remaining work after advance."
        )
    return "\n".join(lines)


def format_recovery_wake(
    *,
    plan_id: str,
    team_id: str = "",
    failed_step_ids: list[str] | None = None,
) -> str:
    steps = ", ".join(failed_step_ids or []) or "(inspect team)"
    inspect_line = (
        f"3) team(inspect, team_id='{team_id}')\n"
        if team_id
        else "3) team(inspect) on the partial wave\n"
    )
    return (
        f"[PLAN RECOVERY REQUIRED]\n"
        f"plan_id={plan_id}\n"
        + (f"team_id={team_id}\n" if team_id else "")
        + f"failed_steps={steps}\n"
        + "1) switch_mode(evaluating)\n"
        + f"2) plan(action='read', plan_id='{plan_id}')\n"
        + inspect_line
        + "4) For each failed step with artifacts: "
        + f"plan(action='accept_partial', plan_id='{plan_id}', ...)\n"
        + "5) For gaps: delegate or plan(sub_plan), then team(advance)\n"
        + "6) plan(verify) then plan(complete) only when all steps are done"
    )


async def auto_complete_active_plan_if_ready(
    plan_tool: Any,
    team_manager: Any | None = None,
) -> str | None:
    """Auto-call plan(complete) when the gate passes. Returns plan_id or None."""
    if plan_tool is None or not hasattr(plan_tool, "execute"):
        return None
    store = plan_tool.get_store() if hasattr(plan_tool, "get_store") else None
    if store is None:
        return None
    tm = team_manager if team_manager is not None else getattr(
        plan_tool, "_team_manager", None,
    )
    try:
        active = resolve_work_plan(store, "", tm, reopen=False)
    except Exception:
        try:
            active = store.find_active()
        except Exception:
            active = None
    if active is None or active.status in ("done", "archived"):
        return None
    if not can_complete_plan(active, tm):
        return None
    try:
        result = await plan_tool.execute(
            {"action": "complete", "plan_id": active.id},
        )
    except Exception:
        logger.debug("auto_complete_active_plan failed", exc_info=True)
        return None
    if getattr(result, "is_error", False):
        return None
    return active.id


def runtime_has_open_plan_work(runtime: Any) -> bool:
    """True when the agent has an orchestration plan or todo still in flight.

    Used by the inner loop to suppress DMN/daydreaming while build work
    remains — mirrors ``LoopHooks.has_active_plan`` without hook wiring.
    """
    tools = getattr(runtime, "_agent_tools", None) or []
    team_manager = getattr(runtime, "_team_manager", None)

    for tool in tools:
        if hasattr(tool, "get_store") and getattr(tool, "name", "") == "plan":
            try:
                store = tool.get_store()
                work = resolve_work_plan(store, "", team_manager, reopen=False)
                if work is None:
                    continue
                if work_plan_has_open_steps(work):
                    return True
                if plan_needs_recovery(work, team_manager):
                    return True
            except Exception:
                pass

    for tool in tools:
        if getattr(tool, "name", "") == "todo":
            try:
                todo_store = getattr(tool, "_store", None)
                if todo_store is not None and todo_store.list_items(
                    status="in_progress",
                ):
                    return True
            except Exception:
                pass

    return False


@dataclass(frozen=True)
class BoardReconcileContext:
    """When a stale wave wake should pivot to plan/todo board hygiene."""

    plan_id: str
    message: str
    reason: str


def _master_todo_status(plan: Plan, todo_store: Any | None) -> tuple[str, str]:
    if todo_store is None or not plan.todo_id:
        return "", ""
    try:
        item = todo_store.get(plan.todo_id)
        if item is None:
            return plan.todo_id, "missing"
        return plan.todo_id, str(getattr(item, "status", "") or "")
    except Exception:
        return plan.todo_id, ""


def build_board_snapshot_lines(
    plan: Plan | None,
    *,
    todo_store: Any | None = None,
    team_manager: Any | None = None,
) -> list[str]:
    """Compact board state for every orchestration wake."""
    if plan is None:
        return []
    lines = [f"[BOARD SNAPSHOT] plan_id={plan.id} status={plan.status}"]
    done = sum(1 for s in plan.steps if s.status == "done")
    skipped = sum(1 for s in plan.steps if s.status == "skipped")
    open_n = plan_open_step_count(plan)
    lines.append(
        f"Steps: {done} done, {skipped} skipped, {open_n} blocking closure "
        f"({len(plan.steps)} total)"
    )
    _todo_id, _todo_st = _master_todo_status(plan, todo_store)
    if _todo_id:
        lines.append(f"Master todo: {_todo_id} — {_todo_st or 'unknown'}")
    if plan.audit and plan.audit.issues:
        lines.append(f"Verify issues ({len(plan.audit.issues)}):")
        for issue in plan.audit.issues[:3]:
            lines.append(f"  - {issue[:120]}")
    if open_n == 0 and plan.status not in ("done", "archived"):
        lines.append(
            "All steps done — next: plan(verify) → plan(complete) → "
            "communicate(summary) → todo complete if master card still open."
        )
    elif plan_needs_recovery(plan, team_manager):
        lines.append("Plan needs recovery — read plan + team(inspect) before advancing.")
    return lines


def needs_board_reconcile(
    plan: Plan,
    *,
    todo_store: Any | None = None,
    team_manager: Any | None = None,
) -> bool:
    """True when plan/todo closure work remains despite finalized waves."""
    if plan.status in ("done", "archived"):
        return False
    if plan_open_step_count(plan) > 0:
        return True
    if plan_needs_recovery(plan, team_manager):
        return True
    if plan.audit and plan.audit.issues:
        return True
    if plan.audit and not plan.audit.all_criteria_met:
        return True
    _tid, _tst = _master_todo_status(plan, todo_store)
    if _tid and _tst in ("in_progress", "queued", "inbox"):
        return True
    if completion_gate_message(plan, team_manager) is not None:
        return True
    return False


def format_stale_wave_board_redirect(
    *,
    plan: Plan,
    team_id: str,
    stale_reason: str,
    todo_store: Any | None = None,
    team_manager: Any | None = None,
) -> str:
    _tid, _tst = _master_todo_status(plan, todo_store)
    lines = [
        "[STALE WAVE WAKE — REDIRECTED TO BOARD CHECK]",
        f"Suppressed redundant review for team {team_id} ({stale_reason}).",
        "The wave is already finalized — do NOT team(inspect/advance) again.",
        "",
        "MANDATORY this turn:",
        "1) todo(action='list') — confirm master Kanban card status",
        f"2) plan(action='read', plan_id='{plan.id}')",
    ]
    if _tid:
        lines.append(f"   Master todo {_tid}: {_tst or 'unknown'}")
    if plan_open_step_count(plan) == 0:
        lines.append(format_plan_closure_nudge(plan.id))
        lines.append(
            "3) communicate(message=...) — brief stakeholder update on "
            "completion or what blocks verify/complete."
        )
    else:
        lines.append(
            "3) Resolve open steps or recovery, then verify → complete."
        )
    lines.extend(build_board_snapshot_lines(
        plan, todo_store=todo_store, team_manager=team_manager,
    ))
    return "\n".join(lines)


def apply_stale_wave_wake_redirect(
    dispatch_source: str,
    *,
    team_manager: Any,
    plan_tool: Any | None,
    todo_tool: Any | None = None,
) -> tuple[str, str | None, str | None]:
    """Rewrite stale ``team_wave_complete`` wakes to board reconcile or no-op.

    Returns ``(dispatch_source, extra_system_message, early_exit_reason)``.
    """
    if not (dispatch_source or "").startswith("team_wave_complete:"):
        return dispatch_source, None, None
    team_id = dispatch_source.split(":", 1)[1]
    stale_reason = team_manager.stale_wave_review_wake_reason(team_id)
    if not stale_reason:
        return dispatch_source, None, None
    team_manager._drain_wave_complete_dispatch(team_id)
    board = resolve_board_reconcile_wake(
        plan_tool=plan_tool,
        team_manager=team_manager,
        todo_tool=todo_tool,
        stale_reason=stale_reason,
        team_id=team_id,
    )
    if board is not None:
        return f"board_reconcile:{board.plan_id}", board.message, None
    return dispatch_source, None, "stale_wave_review_wake"


def resolve_board_reconcile_wake(
    *,
    plan_tool: Any | None,
    team_manager: Any | None,
    todo_tool: Any | None = None,
    stale_reason: str = "",
    team_id: str = "",
) -> BoardReconcileContext | None:
    if plan_tool is None:
        return None
    store = plan_tool.get_store() if hasattr(plan_tool, "get_store") else getattr(
        plan_tool, "_store", None,
    )
    if store is None:
        return None
    tm = team_manager if team_manager is not None else getattr(
        plan_tool, "_team_manager", None,
    )
    try:
        plan = resolve_work_plan(store, "", tm, reopen=False)
    except Exception:
        plan = store.find_active()
    if plan is None:
        return None
    todo_store = getattr(todo_tool, "_store", None) if todo_tool else None
    if not needs_board_reconcile(plan, todo_store=todo_store, team_manager=tm):
        return None
    msg = format_stale_wave_board_redirect(
        plan=plan,
        team_id=team_id,
        stale_reason=stale_reason or "wave_already_finalized",
        todo_store=todo_store,
        team_manager=tm,
    )
    return BoardReconcileContext(
        plan_id=plan.id,
        message=msg,
        reason=stale_reason or "board_reconcile",
    )


def plan_closure_blocked_summary(plan: Plan) -> str:
    """User-facing summary when the loop exits on stall near completion."""
    issues: list[str] = []
    if plan.audit and plan.audit.issues:
        issues = [str(i) for i in plan.audit.issues[:5]]
    gate = completion_gate_message(plan, None) or ""
    parts = [
        f"Plan `{plan.id}` is not closed yet (status={plan.status}).",
    ]
    if issues:
        parts.append("Verify blockers:")
        parts.extend(f"- {i[:200]}" for i in issues)
    elif gate:
        parts.append(gate[:400])
    else:
        parts.append(
            "All steps appear done — run plan(verify) then plan(complete)."
        )
    parts.append(
        "I hit a tool loop limit before finishing closure; "
        "the build artifacts may still be ready to test."
    )
    return "\n".join(parts)


def should_emit_closure_blocked_communicate(
    plan: Plan | None,
    *,
    exit_reason: str,
    tool_successes: dict[str, int],
    is_delegate_loop: bool,
) -> bool:
    if is_delegate_loop or plan is None:
        return False
    if exit_reason not in ("stalled", "consecutive_errors"):
        return False
    if plan.status in ("done", "archived"):
        return False
    if plan_open_step_count(plan) > 0:
        return False
    if tool_successes.get("communicate", 0) > 0:
        return False
    return True
