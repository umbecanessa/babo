"""Tests for todo idle eligibility and WM todo dispatch completion."""

from __future__ import annotations

import importlib

import pytest

from nls.engine.inner_loop import InnerLoop

_idle_policy = importlib.import_module("nls.skills.bundled.todo-list.idle_policy")
infer_idle_eligible = _idle_policy.infer_idle_eligible
looks_like_investigation_todo = _idle_policy.looks_like_investigation_todo


@pytest.mark.parametrize(
    ("params", "title", "description", "expected"),
    [
        ({}, "Black screen on login for user @aseap12", "Need to investigate logs", True),
        ({"idle_eligible": False}, "Black screen on login", "crash report", False),
        ({"source": "channel"}, "Follow up with tester", "", True),
        ({}, "Buy milk", "groceries", False),
        ({"tags": ["bug"]}, "Login issue", "", True),
    ],
)
def test_infer_idle_eligible(params, title, description, expected):
    assert infer_idle_eligible(params, title=title, description=description) is expected


def test_looks_like_investigation_todo():
    assert looks_like_investigation_todo(
        "Black screen on login for user @aseap12",
        "Need to investigate the source code",
    )
    assert not looks_like_investigation_todo("Configure telegram bot", "")


@pytest.mark.parametrize(
    "meta,expected",
    [
        (
            {
                "aborted": False,
                "tool_calls": 3,
                "exit_reason": "task_complete",
                "final": "Investigated logs and filed fix plan.",
            },
            True,
        ),
        (
            {
                "aborted": True,
                "tool_calls": 0,
                "exit_reason": "user_abort",
                "final": "[Loop stopped: user_abort. 2 iterations, 0 tool calls.]",
            },
            False,
        ),
        (
            {
                "aborted": False,
                "tool_calls": 0,
                "exit_reason": "task_complete",
                "final": "Done",
            },
            False,
        ),
    ],
)
def test_wm_todo_dispatch_succeeded(meta, expected):
    assert InnerLoop._wm_todo_dispatch_succeeded(meta) is expected
