"""Keep working memory aligned with authoritative plan + team state.

After accept_partial, verify, or wave reconciliation the Cryptex rings can
retain stale consolidation facts, duplicate plan-position blobs, and blocked
plan status long after the underlying steps moved forward.  This module
refreshes the high-salience orchestration breadcrumbs the EM needs — especially
``orch.pending_wave_launch`` when a wave team sits in ``created`` waiting for
``team(action='launch')``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from nls.agentic.orchestration_policy import build_pending_wave_launch_wake
from nls.agentic.plan_store import Plan
from nls.agentic.plan_work import (
    plan_has_improper_closure,
    plan_has_unresolved_partial_team,
)

logger = logging.getLogger(__name__)

try:
    from nls.brain.cryptex import (
        RING_CONSOLIDATION,
        RING_INSTRUCTIONS,
        RING_ORCHESTRATION,
        RING_PROJECT_FACTS,
        RING_TACTICAL_GOALS,
    )
except ImportError:  # pragma: no cover
    RING_CONSOLIDATION = "consolidation"
    RING_INSTRUCTIONS = "instructions"
    RING_ORCHESTRATION = "orchestration"
    RING_PROJECT_FACTS = "project_facts"
    RING_TACTICAL_GOALS = "tactical_goals"

_SCRUB_RING_IDS = frozenset({
    RING_CONSOLIDATION,
    RING_ORCHESTRATION,
    RING_INSTRUCTIONS,
    RING_PROJECT_FACTS,
    RING_TACTICAL_GOALS,
})

_STALE_FACT_DOMAINS = frozenset({
    "Consolidation.SessionProgress",
    "Project.Step.Status",
    "Project.Missing",
    "Project.Wave.Status",
    "Project.Progress",
    "Plan.Progress",
})

_COMPACTION_SNAPSHOT_DOMAINS = frozenset({
    "CompactionPending",
    "CompactionDone",
    "CompactionDecisions",
    "CompactionGoal",
    "CompactionNextSteps",
    "CompactionComms",
    "CompactionFilesRead",
    "CompactionFilesModified",
})

_CANONICAL_PLAN_POSITION_DOMAINS = frozenset({
    "_plan_position",
    "orch.plan_position",
})

_PROGRESS_RE = re.compile(r"\b(\d+)/(\d+)\b")
_STEP_ID_RE = re.compile(r"step-\d+")
_PLAN_POSITION_HDR_RE = re.compile(
    r"\[PLAN POSITION\s*[—-]\s*(\d+)/(\d+)",
    re.IGNORECASE,
)

SYNC_MODE_FULL = "full"
SYNC_MODE_REFRESH = "refresh"


def audit_issue_is_stale(issue: str, plan: Plan) -> bool:
    """True when an audit issue references steps already marked done."""
    low = (issue or "").lower()
    if not low:
        return False

    for step in plan.steps:
        if step.status not in ("done", "skipped"):
            continue
        if step.id in issue and any(
            k in low for k in ("not done", "pending", "missing", "failed")
        ):
            return True
        label = (step.label or "").lower()
        if len(label) > 8 and label[:40] in low and "not done" in low:
            return True

    if "landed partial" in low or "failed steps:" in low:
        ids = _STEP_ID_RE.findall(issue)
        if ids and all(
            any(s.id == sid and s.status == "done" for s in plan.steps)
            for sid in ids
        ):
            return True
    return False


def prune_stale_audit_issues(plan: Plan) -> int:
    """Drop audit issues superseded by step completion. Returns removed count."""
    audit = plan.audit
    if audit is None or not audit.issues:
        return 0
    kept = [i for i in audit.issues if not audit_issue_is_stale(i, plan)]
    removed = len(audit.issues) - len(kept)
    if removed:
        audit.issues = kept
        audit.all_criteria_met = len(kept) == 0
        plan.touch()
    return removed


def reconcile_plan_status(plan: Plan, team_manager: Any | None = None) -> bool:
    """Unblock plan when substantive recovery reasons are cleared."""
    if plan.status != "blocked":
        return False
    if any(s.status == "failed" for s in plan.steps):
        return False
    if plan_has_unresolved_partial_team(plan.id, team_manager):
        return False
    if plan_has_improper_closure(plan):
        return False
    plan.status = "in_progress"
    plan.touch()
    return True


def _plan_progress(plan: Plan) -> tuple[int, int]:
    done = sum(1 for s in plan.steps if s.status in ("done", "skipped"))
    return done, len(plan.steps)


def _content_has_stale_progress(content: str, done_count: int, total: int) -> bool:
    if not content or total <= 0:
        return False
    for match in _PROGRESS_RE.finditer(content):
        if int(match.group(2)) == total and int(match.group(1)) < done_count:
            return True
    header = _PLAN_POSITION_HDR_RE.search(content)
    if header and int(header.group(2)) == total and int(header.group(1)) < done_count:
        return True
    return False


def _slot_is_stale_for_plan(slot: Any, plan: Plan, done_count: int, total: int) -> bool:
    content = slot.content or ""
    domain = slot.domain or ""
    if domain in _STALE_FACT_DOMAINS:
        return True
    if domain in _COMPACTION_SNAPSHOT_DOMAINS:
        return True
    if (
        "[PLAN POSITION" in content
        and domain not in _CANONICAL_PLAN_POSITION_DOMAINS
    ):
        return True
    if domain == "Consolidation.DayNarrative":
        return _content_has_stale_progress(content, done_count, total)
    if domain.startswith("Consolidation."):
        return _content_has_stale_progress(content, done_count, total)
    if domain.startswith("Goal.Tactical.") or getattr(slot, "level", "") == "tactical":
        text = f"{domain} {content}".lower()
        for step in plan.steps:
            if step.status not in ("done", "skipped"):
                continue
            if step.id in text and any(
                w in text for w in ("pending", "fix", "implement", "wire", "complete")
            ):
                return True
        return _content_has_stale_progress(content, done_count, total)
    return False


def build_pending_teams_breadcrumb(
    team_manager: Any | None,
    plan_id: str | None = None,
) -> str:
    """Orchestrator-visible hint for unlaunched wave teams."""
    if team_manager is None:
        return ""
    discover = getattr(team_manager, "discover_unlaunched_wave_teams", None)
    if discover is None:
        return ""
    teams = discover()
    if plan_id:
        teams = [t for t in teams if getattr(t, "plan_id", "") == plan_id]
    if not teams:
        return ""

    lines = ["[WAVES READY — LAUNCH REQUIRED]"]
    for team in sorted(teams, key=lambda t: (t.wave_index, t.created_at)):
        step_labels = ", ".join(
            m.task.split("\n")[0][:50] for m in team.members[:4]
        )
        lines.append(
            build_pending_wave_launch_wake(
                team.id,
                team_name=team.name,
                reconcile_reason=f"wave {team.wave_index + 1} prepared",
            )
        )
        if step_labels:
            lines.append(f"  Steps: {step_labels}")
    if len(teams) > 1:
        lines.append(
            f"{len(teams)} wave team(s) waiting — launch lowest wave_index first."
        )
    lines.append(
        "Do NOT re-implement delegatable steps (read/verify loops). "
        "Launch the prepared wave, then await_delegates()."
    )
    return "\n".join(lines)


def _upsert_wm_domain(
    wm: Any,
    domain: str,
    content: str,
    *,
    salience: float = 0.95,
    slot_type: str = "fact",
) -> None:
    if hasattr(wm, "upsert_orchestration_slot") and domain.startswith("orch."):
        wm.upsert_orchestration_slot(
            domain=domain,
            content=content,
            salience=salience,
            source="plan_sync",
        )
        return
    if hasattr(wm, "upsert_fact"):
        wm.upsert_fact(
            domain=domain,
            content=content,
            source="plan_sync",
            salience=salience,
        )
        return
    if domain == "orch.plan_position" and hasattr(wm, "set_plan_position"):
        wm.set_plan_position(content)
    elif domain == "orch.pending_wave_launch" and hasattr(wm, "add_instruction"):
        wm.add_instruction(content[:2000], source="plan_sync", salience=salience)


def _remove_wm_domain(wm: Any, domain: str) -> None:
    if hasattr(wm, "remove_by_domain"):
        wm.remove_by_domain(domain)


def sync_pending_teams_breadcrumb(
    wm: Any | None,
    team_manager: Any | None,
    plan_id: str | None = None,
    *,
    plan: Plan | None = None,
) -> None:
    if wm is None:
        return
    domain = "orch.pending_wave_launch"
    if plan is not None and plan.status in ("done", "archived"):
        _remove_wm_domain(wm, domain)
        return
    breadcrumb = build_pending_teams_breadcrumb(team_manager, plan_id)
    if not breadcrumb:
        _remove_wm_domain(wm, domain)
        return
    _upsert_wm_domain(wm, domain, breadcrumb[:2000], salience=0.98)


def _scrub_stale_domains(wm: Any) -> None:
    if not hasattr(wm, "remove_by_domain"):
        return
    for domain in _STALE_FACT_DOMAINS | _COMPACTION_SNAPSHOT_DOMAINS:
        wm.remove_by_domain(domain)


def _scrub_stale_ring_slots(wm: Any, plan: Plan) -> None:
    rings = getattr(wm, "_rings", None)
    if not rings:
        return

    done_count, total = _plan_progress(plan)
    for ring_id in _SCRUB_RING_IDS:
        ring = rings.get(ring_id)
        if ring is None:
            continue
        for pos, slots in list(ring.positions.items()):
            kept: list[Any] = []
            for slot in slots:
                if getattr(slot, "access", "malleable") == "genesis":
                    kept.append(slot)
                    continue
                if _slot_is_stale_for_plan(slot, plan, done_count, total):
                    continue
                kept.append(slot)
            ring.positions[pos] = kept


def _scrub_stale_tactical_goals(wm: Any, plan: Plan) -> None:
    if not hasattr(wm, "remove_goals_where"):
        return
    done_count, total = _plan_progress(plan)
    done_ids = {
        s.id for s in plan.steps if s.status in ("done", "skipped")
    }

    def _stale_goal(goal: Any) -> bool:
        domain = goal.domain or ""
        level = getattr(goal, "level", "")
        if level != "tactical" and not domain.startswith("Goal.Tactical."):
            return False
        text = f"{domain} {goal.content or ''}"
        for sid in done_ids:
            if sid in text:
                return True
        return _content_has_stale_progress(text, done_count, total)

    wm.remove_goals_where(_stale_goal)


def _scrub_stale_slots(wm: Any, plan: Plan) -> None:
    _scrub_stale_domains(wm)
    _scrub_stale_ring_slots(wm, plan)
    _scrub_stale_tactical_goals(wm, plan)


def _refresh_consolidation_task_context(wm: Any, plan: Plan) -> None:
    done, total = _plan_progress(plan)
    pending = [
        s.label for s in plan.steps
        if s.status not in ("done", "skipped")
    ][:4]
    recent = [s.label for s in plan.steps if s.status == "done"][-3:]
    snapshot = (
        f"{plan.title}. Progress: {done}/{total} steps done. "
        f"Recently completed: {', '.join(recent) or 'none'}. "
        f"Remaining: {', '.join(pending) or 'none'}."
    )

    rings = getattr(wm, "_rings", None)
    if rings is not None:
        ring = rings.get(RING_CONSOLIDATION)
        if ring is not None:
            ring.upsert_slot(
                domain="Consolidation.TaskContext",
                content=snapshot,
                slot_type="fact",
                salience=0.95,
                source="plan_sync",
            )
            return

    if hasattr(wm, "upsert_fact"):
        wm.upsert_fact(
            domain="Consolidation.TaskContext",
            content=snapshot,
            source="plan_sync",
            salience=0.95,
        )


def _sync_plan_position(wm: Any, plan: Plan) -> None:
    position = plan.to_position_string()
    if not position:
        return
    if hasattr(wm, "set_plan_position"):
        wm.set_plan_position(position)
    summary = "\n".join(position.split("\n")[:6])
    _upsert_wm_domain(wm, "orch.plan_position", summary, salience=0.95)


def persist_wm(wm: Any | None, agent_dir: Any | None) -> None:
    """Write Cryptex / WM state so breadcrumbs survive mid-loop crashes."""
    if wm is None or agent_dir is None:
        return
    try:
        path = Path(agent_dir)
        if hasattr(wm, "save"):
            wm.save(path)
    except Exception:
        logger.debug("WM persist after plan sync failed", exc_info=True)


def apply_plan_wm_sync(
    plan: Plan,
    wm: Any | None = None,
    team_manager: Any | None = None,
    *,
    plan_store: Any | None = None,
    agent_dir: Any | None = None,
    mode: str = SYNC_MODE_FULL,
    persist: bool = True,
) -> None:
    """Refresh plan truth in Cryptex after plan or team mutations."""
    if plan is None:
        return

    if mode == SYNC_MODE_FULL:
        dirty = False
        if prune_stale_audit_issues(plan):
            dirty = True
        if reconcile_plan_status(plan, team_manager):
            dirty = True
        if dirty and plan_store is not None:
            try:
                plan_store.save(plan)
            except Exception:
                logger.debug("Plan save after WM sync failed", exc_info=True)

    if wm is None:
        return

    _sync_plan_position(wm, plan)
    _scrub_stale_slots(wm, plan)
    _refresh_consolidation_task_context(wm, plan)
    sync_pending_teams_breadcrumb(
        wm, team_manager, plan.id, plan=plan,
    )

    if persist:
        persist_wm(wm, agent_dir)


def sync_plan_wm_by_id(
    plan_id: str,
    wm: Any | None,
    team_manager: Any | None,
    plan_store: Any | None,
    *,
    agent_dir: Any | None = None,
    mode: str = SYNC_MODE_FULL,
    persist: bool = True,
) -> None:
    if not plan_id or plan_store is None:
        return
    plan = plan_store.load(plan_id)
    if plan is not None:
        apply_plan_wm_sync(
            plan,
            wm=wm,
            team_manager=team_manager,
            plan_store=plan_store,
            agent_dir=agent_dir,
            mode=mode,
            persist=persist,
        )
