"""Tests for orchestrator verification breadcrumbs."""

from __future__ import annotations

from nls.agentic.orchestration_policy import (
    build_evaluating_action_breadcrumb,
    build_orchestration_wake_message,
    build_plan_wake_breadcrumbs,
)
from nls.agentic.verification_hints import (
    completion_review_verify_breadcrumb,
    pre_plan_verify_reminder,
)
from nls.agentic.wake_coordination import build_batched_completion_review_message


class _Member:
    def __init__(self, delegate_number: int, task: str, status: str = "running"):
        self.delegate_number = delegate_number
        self.task = task
        self.status = status
        self.step_id = "step-1"


class _Team:
    def __init__(self, team_id: str):
        self.id = team_id
        self.name = "Wave 1"
        self.wave_index = 0
        self.members = [_Member(1, "Build backend API")]


class _TM:
    def __init__(self, team_id: str):
        self._teams = {team_id: _Team(team_id)}
        self._pending_completion_reviews = {
            1: {"team_id": team_id, "member_idx": 0},
        }
        self._agent_id = "agent-1"


def test_completion_review_breadcrumb_mentions_spot_check():
    text = completion_review_verify_breadcrumb(team_id="team_abc")
    assert "spot-check" in text.lower() or "verify" in text.lower()
    assert "hint" in text.lower()
    assert "team_abc" in text


def test_pre_plan_verify_reminder_not_blocking():
    text = pre_plan_verify_reminder()
    assert "BREADCRUMB" in text
    assert "plan(verify)" in text
    assert "BLOCKED" not in text


def test_batched_completion_review_includes_verify_breadcrumb():
    tm = _TM("team_x")
    msg = build_batched_completion_review_message(tm, "team_x")
    assert "BREADCRUMB" in msg
    assert "spot-check" in msg.lower() or "verify" in msg.lower()
    assert "intervene" in msg


def test_orchestration_wake_completion_review_nudge():
    msg = build_orchestration_wake_message(
        dispatch_source="team_completion_review:team_x",
        dual_wm=None,
    )
    assert "spot-check" in msg.lower() or "verify" in msg.lower()
    assert "hint" in msg.lower()


def test_evaluating_breadcrumb_completion_review_verify():
    msg = build_evaluating_action_breadcrumb(
        None,
        dispatch_source="team_completion_review:team_x",
    )
    assert "BREADCRUMB" in msg
    assert "read/list_dir" in msg or "spot-check" in msg.lower()


def test_plan_wake_breadcrumbs_suggest_verify_when_no_blockers():
    lines = build_plan_wake_breadcrumbs(audit_issues=[], incomplete_steps=[])
    joined = "\n".join(lines)
    assert "plan(action='verify')" in joined
