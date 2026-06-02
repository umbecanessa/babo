"""Tests for team advance / post-approve breadcrumbs."""

from __future__ import annotations

from dataclasses import dataclass, field

from nls.agentic.team_advance_hints import (
    format_advance_blocked_message,
    format_post_approve_breadcrumb,
)


@dataclass
class _Member:
    delegate_number: int
    status: str
    task: str = "Run pytest"


@dataclass
class _Team:
    members: list[_Member] = field(default_factory=list)


@dataclass
class _TM:
    _pending_completion_reviews: dict[int, dict] = field(default_factory=dict)


def test_advance_blocked_lists_running_delegates():
    team = _Team(members=[_Member(7, "running"), _Member(8, "pending")])
    msg = format_advance_blocked_message(
        "team_abc",
        reason="2 member(s) still active",
        team=team,
    )
    assert "Delegate #7" in msg
    assert "Delegate #8" in msg
    assert "Do NOT call team(advance)" in msg
    assert "await_delegates" in msg


def test_post_approve_no_advance_while_siblings_running():
    team = _Team(
        members=[
            _Member(7, "done"),
            _Member(8, "running"),
        ],
    )
    msg = format_post_approve_breadcrumb(
        "team_abc",
        team=team,
        approved_delegate_number=7,
    )
    assert "Do NOT team(advance)" in msg
    assert "#8" in msg


def test_post_approve_nudges_advance_when_wave_quiet():
    team = _Team(members=[_Member(7, "done")])
    msg = format_post_approve_breadcrumb("team_abc", team=team)
    assert "team(action='advance'" in msg


def test_post_approve_pending_completion_review():
    tm = _TM(_pending_completion_reviews={
        9: {"team_id": "team_abc", "team_name": "Wave", "member_idx": 1},
    })
    team = _Team(members=[_Member(7, "done")])
    msg = format_post_approve_breadcrumb(
        "team_abc", team=team, team_manager=tm,
    )
    assert "completion review" in msg
    assert "#9" in msg
