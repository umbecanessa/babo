"""Plan work-state helpers — single source of truth for active vs recovery vs complete.

Orchestrator loops, schedulers, plan tool actions, and team wave finalization
all use these functions so a plan is never marked ``done`` while partial work
remains unresolved.
"""

from __future__ import annotations

from typing import Any

from nls.agentic.plan_store import Plan, PlanStep, PlanStore

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
                work = resolve_work_plan("", team_manager, reopen=False)
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
