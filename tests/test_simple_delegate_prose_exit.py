"""Simple delegate monitoring must not prose-exit while delegates run."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nls.agentic.evaluator import should_complete
from nls.agentic.types import LoopConfig, LoopState


@pytest.mark.asyncio
async def test_implicit_delivery_blocked_without_await_delegates():
    """Regression: check-back loop exited via implicit delivery (no active plan)."""
    state = LoopState(
        user_input="monitor delegates",
        orchestration_profile="solo_structured",
    )
    state.dispatch_source = "scheduler"
    state.simple_delegate_monitoring = True
    state.consecutive_text_only = 1
    state.total_tool_calls = 2
    state.tool_successes = {"delegate_status": 2}
    state.delegate_count = 5
    state._last_iter_text = (
        "All 5 delegates are actively running after ~70 seconds:\n"
        "| # | Task | Status |"
    )

    dm = MagicMock()
    dm.has_active_delegates.return_value = True

    done = await should_complete(
        state, LoopConfig(), None, vllm_client=None, delegate_manager=dm,
    )
    assert done is False


@pytest.mark.asyncio
async def test_implicit_delivery_allowed_after_await_delegates():
    state = LoopState(
        user_input="monitor delegates",
        orchestration_profile="solo_structured",
    )
    state.dispatch_source = "scheduler"
    state.simple_delegate_monitoring = True
    state.consecutive_text_only = 1
    state.total_tool_calls = 3
    state.tool_successes = {"delegate_status": 1, "await_delegates": 1}
    state.cumulative_actions = [
        "delegate_status: OK",
        "await_delegates: OK",
    ]
    state.delegate_count = 5
    state._last_iter_text = "Handing off — delegates still running in background."

    dm = MagicMock()
    dm.has_active_delegates.return_value = True

    done = await should_complete(
        state, LoopConfig(), None, vllm_client=None, delegate_manager=dm,
    )
    assert done is True
