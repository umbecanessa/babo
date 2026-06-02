"""Tests for EM IC block while delegates run."""

from __future__ import annotations

from unittest.mock import MagicMock

from nls.agentic.orchestration_policy import block_tool_call
from nls.agentic.types import AgentMode, LoopState


def test_edit_blocked_in_evaluating_while_delegates_run():
    state = LoopState()
    state.coordinator_mode = True
    dm = MagicMock()
    dm.has_active_delegates.return_value = True

    msg = block_tool_call(
        "edit",
        {"path": "backend/main.py"},
        state,
        AgentMode.EVALUATING,
        dm,
    )
    assert msg is not None
    assert "BLOCKED" in msg
    assert "Delegate" in msg
