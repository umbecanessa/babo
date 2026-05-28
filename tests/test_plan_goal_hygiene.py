"""Plan-aligned goal hygiene (no keyword blocklists)."""

from __future__ import annotations

from nls.agentic.plan_goal_hygiene import (
    filter_stale_tactical_goals,
    goal_is_stale_for_plan,
    goal_references_plan_step,
    prune_stale_tactical_goals_for_plan,
)


class _Step:
    def __init__(self, label: str, status: str = "pending"):
        self.label = label
        self.status = status
        self.id = label.replace(" ", "-").lower()


class _Plan:
    def __init__(self, steps):
        self.steps = steps


def test_goal_references_done_step_by_label_overlap():
    plan = _Plan([
        _Step("Repository Setup & GitHub Creation", "done"),
        _Step("Upload & Processing Page", "pending"),
    ])
    assert goal_is_stale_for_plan(
        "Repository Setup & GitHub Creation", plan,
    )
    assert not goal_is_stale_for_plan(
        "Upload & Processing Page", plan,
    )


def test_filter_without_plan_is_passthrough():
    goals = ["Anything about kubernetes deployment"]
    assert filter_stale_tactical_goals(goals, None) == goals


def test_filter_with_plan_removes_only_done_step_goals():
    plan = _Plan([_Step("Database Schema & Migration Files", "done")])
    goals = [
        "Database Schema & Migration Files",
        "Build interactive evaluation interface",
    ]
    filtered = filter_stale_tactical_goals(goals, plan)
    assert filtered == ["Build interactive evaluation interface"]


class _Goal:
    def __init__(self, content: str):
        self.content = content


class _WM:
    def __init__(self, goals):
        self.goals = goals

    def remove_goals_where(self, pred):
        before = len(self.goals)
        self.goals = [g for g in self.goals if not pred(g)]
        return before - len(self.goals)


class _Store:
    def load(self, plan_id: str):
        return _Plan([_Step("Backend Server Foundation", "done")])


def test_prune_wm_goals_plan_aligned():
    wm = _WM([
        _Goal("Backend Server Foundation"),
        _Goal("Session & Evidence API Endpoints"),
    ])
    n = prune_stale_tactical_goals_for_plan(wm, _Store(), "p1")
    assert n == 1
    assert len(wm.goals) == 1
    assert "Session" in wm.goals[0].content


def test_unrelated_goal_not_matched_by_short_token():
    step = _Step("API", "done")
    assert not goal_references_plan_step("Build REST API for sessions", step)
