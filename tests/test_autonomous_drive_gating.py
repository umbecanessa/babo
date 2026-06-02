"""Autonomous drive gating after user stall."""

from __future__ import annotations

import time
from types import SimpleNamespace

from nls.engine.inner_loop import InnerLoop


def _busy_rt(**overrides):
    base = dict(
        is_user_busy=False,
        is_busy=False,
        working_memory=SimpleNamespace(get_goals=lambda: []),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_disconfirmation_drive_is_gated():
    assert "disconfirmation" in InnerLoop._AUTONOMOUS_DRIVE_NAMES


def test_skip_autonomous_drive_after_runtime_stall():
    il = InnerLoop.__new__(InnerLoop)
    il._last_agentic_stall_ts = 0.0
    rt = _busy_rt(_last_agentic_stall_ts=time.time())
    assert il._should_skip_autonomous_drive(rt, "disconfirmation", "disconfirm")


def test_skip_autonomous_drive_after_inner_loop_stall():
    il = InnerLoop.__new__(InnerLoop)
    il._last_agentic_stall_ts = time.time()
    rt = _busy_rt(_last_agentic_stall_ts=0.0)
    assert il._should_skip_autonomous_drive(rt, "disconfirmation", "disconfirm")


def test_stall_suppression_expires():
    il = InnerLoop.__new__(InnerLoop)
    il._last_agentic_stall_ts = (
        time.time() - InnerLoop._POST_STALL_DRIVE_SUPPRESS_S - 1
    )
    rt = _busy_rt(_last_agentic_stall_ts=0.0)
    assert not il._should_skip_autonomous_drive(rt, "disconfirmation", "disconfirm")


def test_stall_suppression_cleared_when_both_timestamps_zero():
    il = InnerLoop.__new__(InnerLoop)
    il._last_agentic_stall_ts = 0.0
    rt = _busy_rt(_last_agentic_stall_ts=0.0)
    assert not il._should_skip_autonomous_drive(rt, "disconfirmation", "disconfirm")
