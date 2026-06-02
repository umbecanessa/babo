"""Stale completion-review wake prevention."""

from __future__ import annotations

from dataclasses import dataclass, field

from nls.agentic.wake_coordination import (
    pending_completion_review_count,
    schedule_batched_completion_review_wake,
    skip_stale_completion_review_wake,
    team_completion_review_is_stale,
)


@dataclass
class _Member:
    delegate_number: int
    status: str
    task: str = "Scaffold repo"
    step_id: str = "step-0"


@dataclass
class _Team:
    id: str
    name: str
    plan_id: str
    wave_index: int
    status: str
    members: list[_Member]
    is_terminal: bool = False
    completion_reported: bool = False


class _DelegateState:
    def __init__(self, state: str = "running") -> None:
        self.state = state


class _DelegateManager:
    def __init__(self, delegates: dict[int, _DelegateState] | None = None) -> None:
        self._delegates = delegates or {}


@dataclass
class _TM:
    _teams: dict[str, _Team] = field(default_factory=dict)
    _pending_completion_reviews: dict[int, dict] = field(default_factory=dict)
    _delegate_manager: _DelegateManager | None = None
    _scheduled: list[tuple[str, str]] = field(default_factory=list)
    _queue_prefixes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        def _schedule(prompt: str, source: str) -> None:
            self._scheduled.append((prompt, source))

        self._schedule_orchestration_wake = _schedule
        self._dispatch_has_prefix = lambda prefix: any(
            p.startswith(prefix) for p in self._queue_prefixes
        )

    def clear_completion_review(self, delegate_number: int) -> None:
        self._pending_completion_reviews.pop(delegate_number, None)

    def _notify_completion_review_required(
        self, team: _Team, *, is_reminder: bool = False,
    ) -> bool:
        return schedule_batched_completion_review_wake(
            self, team, is_reminder=is_reminder,
        )

    def reconcile_pending_completion_reviews(
        self,
        current_dispatch_source: str = "",
    ) -> int:
        from nls.agentic.team_manager import TeamManager

        return TeamManager.reconcile_pending_completion_reviews(
            self, current_dispatch_source=current_dispatch_source,
        )


def test_reconcile_skips_team_already_in_current_completion_review_loop():
    team = _Team(
        id="team_w0",
        name="Wave 0",
        plan_id="plan_x",
        wave_index=0,
        status="active",
        members=[_Member(0, "running")],
    )
    tm = _TM(
        _teams={"team_w0": team},
        _pending_completion_reviews={0: {"team_id": "team_w0", "member_idx": 0}},
        _delegate_manager=_DelegateManager({0: _DelegateState("running")}),
    )
    n = tm.reconcile_pending_completion_reviews(
        current_dispatch_source="team_completion_review:team_w0",
    )
    assert n == 0
    assert tm._scheduled == []


def test_reconcile_clears_pending_when_member_already_done():
    team = _Team(
        id="team_w0",
        name="Wave 0",
        plan_id="plan_x",
        wave_index=0,
        status="active",
        members=[_Member(0, "done")],
    )
    tm = _TM(
        _teams={"team_w0": team},
        _pending_completion_reviews={0: {"team_id": "team_w0", "member_idx": 0}},
        _delegate_manager=_DelegateManager({0: _DelegateState("running")}),
    )
    n = tm.reconcile_pending_completion_reviews()
    assert n == 0
    assert tm._pending_completion_reviews == {}


def test_schedule_skips_terminal_team_with_no_pending():
    team = _Team(
        id="team_w0",
        name="Wave 0",
        plan_id="plan_x",
        wave_index=0,
        status="completed",
        members=[_Member(0, "done")],
        is_terminal=True,
    )
    tm = _TM(_teams={"team_w0": team})
    assert pending_completion_review_count(tm, "team_w0") == 0
    assert schedule_batched_completion_review_wake(tm, team) is False
    assert tm._scheduled == []


def test_schedule_reminder_skips_when_completion_review_already_queued():
    team = _Team(
        id="team_w0",
        name="Wave 0",
        plan_id="plan_x",
        wave_index=0,
        status="active",
        members=[_Member(0, "running")],
    )
    tm = _TM(
        _teams={"team_w0": team},
        _pending_completion_reviews={0: {"team_id": "team_w0", "member_idx": 0}},
        _queue_prefixes=["team_completion_review:team_w0"],
    )
    assert schedule_batched_completion_review_wake(
        tm, team, is_reminder=True,
    ) is False
    assert tm._scheduled == []


def test_schedule_skips_team_already_advanced():
    team = _Team(
        id="team_w1",
        name="Wave 1",
        plan_id="plan_x",
        wave_index=1,
        status="completed",
        members=[_Member(1, "done")],
        is_terminal=True,
        completion_reported=True,
    )
    tm = _TM(
        _teams={"team_w1": team},
        _pending_completion_reviews={1: {"team_id": "team_w1", "member_idx": 0}},
    )
    assert team_completion_review_is_stale(tm, "team_w1") is True
    assert schedule_batched_completion_review_wake(tm, team) is False
    assert tm._pending_completion_reviews == {}
    assert tm._scheduled == []


def test_skip_stale_clears_pending_and_returns_true():
    team = _Team(
        id="team_w2",
        name="Wave 2",
        plan_id="plan_x",
        wave_index=2,
        status="completed",
        members=[_Member(2, "done")],
        is_terminal=True,
        completion_reported=True,
    )
    tm = _TM(
        _teams={"team_w2": team},
        _pending_completion_reviews={
            2: {"team_id": "team_w2", "member_idx": 0},
        },
    )
    assert skip_stale_completion_review_wake(tm, "team_w2", context="test") is True
    assert tm._pending_completion_reviews == {}
