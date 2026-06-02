"""Stale member-escalation wake prevention."""

from __future__ import annotations

from dataclasses import dataclass, field

from nls.agentic.wake_coordination import (
    member_escalation_is_stale,
    parse_member_escalation_source,
    schedule_member_escalation_wake,
    skip_stale_member_escalation_wake,
)


@dataclass
class _Member:
    delegate_number: int
    status: str
    task: str = "Task"


@dataclass
class _Team:
    id: str
    members: list[_Member]

    def member_by_delegate(self, delegate_number: int) -> _Member | None:
        for m in self.members:
            if m.delegate_number == delegate_number:
                return m
        return None


@dataclass
class _TM:
    _teams: dict[str, _Team] = field(default_factory=dict)
    _scheduled: list[tuple[str, str]] = field(default_factory=list)
    _drained: list[tuple[str, int]] = field(default_factory=list)
    _hooks: object | None = None
    _reconciled: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._schedule_orchestration_wake = lambda prompt, source: (
            self._scheduled.append((prompt, source))
        )

    def reconcile_with_delegates(self, *, team_id: str | None = None, persist: bool = True) -> int:
        if team_id:
            self._reconciled.append(team_id)
        return 0

    def _drain_member_escalation_dispatch(self, team_id: str, delegate_number: int) -> int:
        self._drained.append((team_id, delegate_number))
        return 1

    def clear_completion_review(self, delegate_number: int) -> None:
        pass


class _Hooks:
    def __init__(self) -> None:
        self.resolved: list[tuple[str, int, str]] = []

    def wm_orch_resolve_escalation(self, team_id: str, member_idx: int, outcome: str) -> None:
        self.resolved.append((team_id, member_idx, outcome))


def test_parse_member_escalation_source():
    assert parse_member_escalation_source(
        "team_member_escalation:team_abc:3",
    ) == ("team_abc", 3)


def test_member_escalation_stale_when_member_done():
    team = _Team(
        id="team_x",
        members=[_Member(delegate_number=0, status="done")],
    )
    tm = _TM(_teams={"team_x": team})
    assert member_escalation_is_stale(tm, "team_x", 0) is True
    assert "team_x" in tm._reconciled


def test_schedule_skips_wake_for_done_member():
    team = _Team(
        id="team_x",
        members=[_Member(delegate_number=1, status="done")],
    )
    tm = _TM(_teams={"team_x": team})
    ok = schedule_member_escalation_wake(tm, team, 1, "help please")
    assert ok is False
    assert tm._scheduled == []
    assert ("team_x", 1) in tm._drained


def test_skip_stale_resolves_wm_escalation():
    team = _Team(
        id="team_x",
        members=[_Member(delegate_number=2, status="failed")],
    )
    hooks = _Hooks()
    tm = _TM(_teams={"team_x": team}, _hooks=hooks)
    assert skip_stale_member_escalation_wake(tm, "team_x", 2, context="test") is True
    assert hooks.resolved == [("team_x", 0, "member_done_stale_wake")]
