"""Triage guards when an active plan + teams already exist.

Prevents post-restart continuation turns from landing in solo_structured
when the engineering-manager stack (plan + team waves) is already set up.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from nls.agentic.orchestration_profile_spec import normalize_profile
from nls.agentic.plan_store import Plan
from nls.agentic.profile_guard_policy import (
    HINT_FORBID_TEAM,
    _max_profile,
)

logger = logging.getLogger(__name__)

_CONTINUATION_RE = re.compile(
    r"\b("
    r"continue|pick\s+up|from\s+there|keep\s+going|remaining|"
    r"finish\s+the\s+plan|resume|already\s+have\s+a\s+plan|"
    r"just\s+continue"
    r")\b",
    re.IGNORECASE,
)

HINT_PLAN_ORCHESTRATION = "continuation:plan_orchestration"


def _teams_for_plan(team_manager: Any | None, plan_id: str) -> list[Any]:
    if team_manager is None or not plan_id:
        return []
    try:
        return [
            t for t in team_manager.list_teams(include_terminal=True)
            if getattr(t, "plan_id", None) == plan_id
        ]
    except Exception:
        return []


def plan_requires_orchestrated_profile(
    plan: Plan | None,
    team_manager: Any | None = None,
) -> bool:
    """True when solo_structured would block team launch / wave continuation."""
    if plan is None or plan.status in ("done", "archived"):
        return False

    teams = _teams_for_plan(team_manager, plan.id)
    if any(
        getattr(t, "status", None) in ("created", "active", "paused")
        for t in teams
    ):
        return True
    if any(
        getattr(t, "status", None) == "created" and not getattr(t, "batch_id", "")
        for t in teams
    ):
        return True

    pending_delegatable = any(
        s.delegatable and s.status not in ("done", "skipped")
        for s in plan.steps
    )
    if pending_delegatable and teams:
        return True

    if plan.status in ("blocked", "in_progress") and teams:
        open_steps = [
            s for s in plan.steps if s.status not in ("done", "skipped")
        ]
        if open_steps and any(s.delegatable for s in open_steps):
            return True
    return False


def build_plan_triage_continuation_block(
    plan_store: Any | None,
    team_manager: Any | None,
) -> str:
    if plan_store is None:
        return ""
    try:
        plan = plan_store.find_active()
    except Exception:
        return ""
    if plan is None or plan.status in ("done", "archived"):
        return ""

    done = sum(1 for s in plan.steps if s.status in ("done", "skipped"))
    total = len(plan.steps)
    pending = [
        f"{s.id}: {s.label[:60]}"
        for s in plan.steps
        if s.status not in ("done", "skipped")
    ][:4]
    teams = _teams_for_plan(team_manager, plan.id)
    unlaunched = [
        t for t in teams
        if getattr(t, "status", None) == "created"
        and not getattr(t, "batch_id", "")
    ]
    active = [
        t for t in teams
        if getattr(t, "status", None) in ("active", "paused")
    ]

    lines = [
        "[ACTIVE PLAN — CONTINUATION CONTEXT]",
        f"plan_id={plan.id} status={plan.status} progress={done}/{total}",
        f"Title: {plan.title}",
    ]
    if pending:
        lines.append("Open steps: " + "; ".join(pending))
    if unlaunched:
        for team in sorted(unlaunched, key=lambda t: t.wave_index)[:3]:
            lines.append(
                f"Wave team {team.name} [{team.id}] wave={team.wave_index} "
                f"status=created — launch with team(action='launch', team_id='{team.id}')"
            )
    if active:
        for team in active[:2]:
            lines.append(
                f"Running team {team.name} [{team.id}] — use team(inspect) / "
                "await_delegates(); do NOT re-implement delegatable steps solo."
            )
    if plan_requires_orchestrated_profile(plan, team_manager):
        lines.append(
            "PROFILE: use orchestrated (NOT solo_structured) — plan waves / "
            "teams already exist."
        )
    return "\n".join(lines)


def boost_triage_for_active_plan(
    triage: Any,
    user_input: str,
    *,
    plan_store: Any | None = None,
    team_manager: Any | None = None,
) -> None:
    """Lift profile to orchestrated when EM infrastructure is already in place."""
    if plan_store is None:
        return
    try:
        plan = plan_store.find_active()
    except Exception:
        return
    if plan is None:
        return

    requires_orch = plan_requires_orchestrated_profile(plan, team_manager)
    continuation = bool(_CONTINUATION_RE.search(user_input or ""))
    if not requires_orch and not (continuation and plan.status not in ("done", "archived")):
        return

    prev = normalize_profile(getattr(triage, "profile", "") or "solo_structured")
    target = prev
    if requires_orch:
        target = _max_profile(target, "orchestrated")
    elif continuation and plan.status in ("blocked", "in_progress"):
        target = _max_profile(target, "solo_structured")

    if target == prev and not requires_orch:
        return

    triage.profile = target
    if (getattr(triage, "intent", "") or "").upper().startswith("CHAT"):
        triage.intent = "TASK_THINK"
        triage.thinking = True

    hints = list(getattr(triage, "hints", None) or [])
    hint_tokens = {h.strip().lower() for h in hints if h and h.strip()}
    if requires_orch and hint_tokens & HINT_FORBID_TEAM:
        hints = [
            h for h in hints
            if h.strip().lower() not in HINT_FORBID_TEAM
        ]
    if requires_orch and HINT_PLAN_ORCHESTRATION not in hint_tokens:
        hints.append(HINT_PLAN_ORCHESTRATION)
    triage.hints = hints

    if not getattr(triage, "goals", None) and continuation:
        pending = next(
            (s.label for s in plan.steps if s.status not in ("done", "skipped")),
            "",
        )
        triage.goals = [
            pending or f"Continue plan {plan.title}",
        ]

    logger.info(
        "Plan triage boost: plan=%s profile %s → %s continuation=%s requires_orch=%s",
        plan.id, prev, target, continuation, requires_orch,
    )


def apply_user_profile_override(
    triage: Any,
    override: str | None,
    *,
    plan_store: Any | None = None,
    team_manager: Any | None = None,
) -> None:
    """Apply explicit user profile pick; never below active-plan orchestration floor."""
    raw = (override or "").strip().lower()
    floor = None
    if plan_store is not None:
        try:
            plan = plan_store.find_active()
        except Exception:
            plan = None
        if plan_requires_orchestrated_profile(plan, team_manager):
            floor = "orchestrated"

    if not raw or raw == "auto":
        if floor:
            triage.profile = _max_profile(
                normalize_profile(getattr(triage, "profile", "") or "solo_structured"),
                floor,
            )
        return

    if raw not in (
        "conversational", "solo_structured", "orchestrated", "squad_lead",
    ):
        return

    target = normalize_profile(raw)
    if floor:
        target = _max_profile(target, floor)
    triage.profile = target


def enforce_loop_profile_for_active_plan(
    state: Any,
    plan_store: Any | None,
    team_manager: Any | None,
) -> None:
    """Last-line guard at loop entry when triage under-shot EM depth."""
    if plan_store is None:
        return
    try:
        plan = plan_store.find_active()
    except Exception:
        return
    if not plan_requires_orchestrated_profile(plan, team_manager):
        return

    prev = normalize_profile(getattr(state, "orchestration_profile", "") or "solo_structured")
    if _max_profile(prev, "orchestrated") == prev:
        return

    state.orchestration_profile = "orchestrated"
    try:
        from nls.agentic.profile_depth_policy import (
            invalidate_tool_policy_cache,
            profile_anchor_message,
        )

        invalidate_tool_policy_cache(state)
        anchor = profile_anchor_message("orchestrated")
        if anchor:
            state.pending_profile_anchor = anchor
    except Exception:
        pass
    logger.info(
        "Loop profile enforced orchestrated for active plan %s (was %s)",
        plan.id, prev,
    )
