"""Tests for wave-complete dedup, cap, and auto-reconcile."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nls.agentic.orchestration_policy import should_auto_launch_next_wave
from nls.agentic.team_manager import (
    WAVE_EMPTY_REVIEW_AUTO_RECONCILE,
    WAVE_REVIEW_GRACE_SECONDS,
    WAVE_REVIEW_MAX_WAKES,
    WAVE_REVIEW_WAKE_COOLDOWN_SECONDS,
    Team,
    TeamManager,
    TeamMember,
)
from nls.tools.agent_tools.plan import Plan, PlanStep, PlanStore


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent"
    (d / "workspace").mkdir(parents=True)
    return d


@pytest.fixture
def plan_store(agent_dir: Path) -> PlanStore:
    store = PlanStore(agent_dir)
    plan = Plan(
        id="plan_test",
        title="Test Plan",
        project_dir="proj",
        steps=[
            PlanStep(id="s1", label="A", delegatable=True),
            PlanStep(id="s2", label="B", delegatable=True, depends_on=["s1"]),
        ],
    )
    store.save(plan)
    return store


def _partial_team(**overrides) -> Team:
    base = dict(
        id="team_partial",
        name="Wave 1",
        plan_id="plan_test",
        wave_index=1,
        status="partial",
        completion_reported=False,
        completed_at=time.time() - 10,
        members=[
            TeamMember(step_id="s1", task="t1", status="done"),
            TeamMember(step_id="s2", task="t2", status="failed"),
        ],
    )
    base.update(overrides)
    return Team(**base)


@pytest.fixture
def team_manager(agent_dir: Path, plan_store: PlanStore) -> TeamManager:
    tm = TeamManager(agent_dir, plan_store, delegate_manager=MagicMock())
    scheduled: list[tuple[str, str]] = []

    def _schedule(prompt: str, source: str) -> None:
        scheduled.append((prompt, source))

    tm.set_schedule_orchestration_wake(_schedule)
    tm._test_scheduled = scheduled  # type: ignore[attr-defined]
    return tm


def test_notify_wave_review_respects_cooldown(team_manager: TeamManager):
    team = _partial_team()
    team_manager._teams[team.id] = team

    assert team_manager._notify_wave_review_required(team) is True
    assert len(team_manager._test_scheduled) == 1  # type: ignore[attr-defined]
    assert team.wave_review_wakes == 1

    assert team_manager._notify_wave_review_required(team) is False
    assert len(team_manager._test_scheduled) == 1  # type: ignore[attr-defined]


def test_notify_wave_review_cap_triggers_auto_reconcile(team_manager: TeamManager):
    team = _partial_team(
        wave_review_wakes=WAVE_REVIEW_MAX_WAKES,
        wave_review_last_wake_at=time.time() - WAVE_REVIEW_WAKE_COOLDOWN_SECONDS - 1,
    )
    team_manager._teams[team.id] = team

    assert team_manager._notify_wave_review_required(team) is False
    assert team.completion_reported is True
    assert len(team_manager._test_scheduled) == 0  # type: ignore[attr-defined]


def test_auto_reconcile_after_grace(team_manager: TeamManager):
    team = _partial_team(completed_at=time.time() - WAVE_REVIEW_GRACE_SECONDS - 1)
    team_manager._teams[team.id] = team

    result = team_manager.try_auto_reconcile_wave_sync(team.id, reason="test")
    assert result is not None
    assert team.completion_reported is True


@pytest.mark.asyncio
async def test_empty_em_reviews_auto_reconcile(team_manager: TeamManager):
    team = _partial_team(wave_empty_reviews=WAVE_EMPTY_REVIEW_AUTO_RECONCILE - 1)
    team_manager._teams[team.id] = team

    await team_manager.handle_wave_review_loop_end(team.id, tool_calls=0)

    assert team.completion_reported is True
    assert team.wave_empty_reviews == WAVE_EMPTY_REVIEW_AUTO_RECONCILE


def test_finalize_offers_pending_auto_launch(team_manager: TeamManager):
    completed = Team(
        id="team_w0",
        name="Wave 0",
        plan_id="plan_test",
        wave_index=0,
        status="completed",
        completion_reported=False,
        completed_at=time.time() - WAVE_REVIEW_GRACE_SECONDS - 1,
        members=[TeamMember(step_id="s1", task="t1", status="done")],
    )
    team_manager._teams[completed.id] = completed
    plan = team_manager._plan_store.load("plan_test")
    plan.get_step("s1").status = "done"
    team_manager._plan_store.save(plan)

    team_manager.try_auto_reconcile_wave_sync(completed.id, reason="test_grace")

    pending = team_manager.pop_pending_auto_launch()
    assert pending is not None
    assert pending.team_id.startswith("team_")
    assert pending.reason == "test_grace"


def test_enqueue_unlaunched_discovers_created_team(team_manager: TeamManager):
    stuck = Team(
        id="team_stuck",
        name="Wave 2",
        plan_id="plan_test",
        wave_index=1,
        status="created",
        members=[TeamMember(step_id="s2", task="t2", status="pending")],
    )
    team_manager._teams[stuck.id] = stuck
    team_manager.save(stuck)

    assert team_manager.enqueue_unlaunched_for_auto_launch() == 1
    pending = team_manager.pop_pending_auto_launch()
    assert pending is not None
    assert pending.team_id == "team_stuck"


def test_should_auto_launch_blocks_active_delegates(team_manager: TeamManager):
    team_manager._delegate_manager.has_active_delegates.return_value = True
    ok, reason = should_auto_launch_next_wave(
        team_manager, team_manager._delegate_manager, "team_x",
    )
    assert ok is False
    assert "delegates" in reason.lower()


def test_reconcile_unreported_skips_cooldown_spam(team_manager: TeamManager):
    team = _partial_team(
        wave_review_wakes=1,
        wave_review_last_wake_at=time.time(),
    )
    team_manager._teams[team.id] = team

    n = team_manager.reconcile_unreported_terminal_teams()
    assert n == 0
    assert len(team_manager._test_scheduled) == 0  # type: ignore[attr-defined]
