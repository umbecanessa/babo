"""Monitoring wrap-up: orchestrator should yield when delegates run in background."""

from __future__ import annotations

import pytest

from nls.agentic.evaluator import should_complete
from nls.agentic.types import AgentMode, LoopConfig, LoopState


class _FakeDelegateManager:
    def __init__(self, running: bool) -> None:
        self._running = running

    def has_active_delegates(self) -> bool:
        return self._running

    def running_count(self) -> int:
        return 1 if self._running else 0


class _Hooks:
    def __init__(self, plan_active: bool = True) -> None:
        self._plan_active = plan_active

    def has_active_plan(self) -> bool:
        return self._plan_active


@pytest.mark.asyncio
async def test_monitoring_wrap_up_with_live_delegates_not_loop_count():
  """Follow-up loop: delegate_count=0 but manager still has running work."""
  state = LoopState(user_input="keep me posted")
  state.active_mode = AgentMode.MONITORING
  state.consecutive_text_only = 1
  state.total_tool_calls = 3
  state.delegate_count = 0
  state._last_iter_text = "Wave 0 is running — I'll update you when done." * 3
  state.cumulative_actions = ["await_delegates(summary='wave 0 launched')"]

  dm = _FakeDelegateManager(running=True)
  done = await should_complete(
      state, LoopConfig(), _Hooks(), delegate_manager=dm,
  )
  assert done is True


@pytest.mark.asyncio
async def test_monitoring_wrap_up_at_three_tool_calls():
  state = LoopState(user_input="monitor")
  state.active_mode = AgentMode.MONITORING
  state.consecutive_text_only = 1
  state.total_tool_calls = 3
  state.delegate_count = 1
  state._last_iter_text = (
      "Delegates launched and working in the background. "
      "I'll notify you when the wave completes or hits a milestone."
  )
  state.cumulative_actions = ["await_delegates(summary='delegates running')"]

  done = await should_complete(
      state, LoopConfig(), _Hooks(), delegate_manager=_FakeDelegateManager(True),
  )
  assert done is True


@pytest.mark.asyncio
async def test_coordinator_status_yield_without_background_delegates():
  """After tools + long status text, end loop once (no stall-nudge second reply)."""
  state = LoopState(user_input="build the platform end-to-end on Railway")
  state.active_mode = AgentMode.DELEGATING
  state.consecutive_text_only = 1
  state.total_tool_calls = 5
  state.delegate_count = 0
  state._last_iter_text = (
      "I've read the PRD and created the master plan with 12 steps. "
      "Infrastructure, API, and deploy tracks are outlined. "
      "What would you like me to do first?"
  )

  done = await should_complete(
      state, LoopConfig(), _Hooks(plan_active=True),
      delegate_manager=_FakeDelegateManager(False),
  )
  assert done is True


@pytest.mark.asyncio
async def test_monitoring_wrap_up_not_plain_status_yield():
  """Delegates running + await_delegates → monitoring exit, not idle CONTINUE."""
  state = LoopState(user_input="monitor")
  state.active_mode = AgentMode.MONITORING
  state.consecutive_text_only = 1
  state.total_tool_calls = 5
  state.delegate_count = 0
  state._last_iter_text = "Wave is executing in the background." * 3
  state.cumulative_actions = ["await_delegates(summary='wave running')"]

  done = await should_complete(
      state, LoopConfig(), _Hooks(), delegate_manager=_FakeDelegateManager(True),
  )
  assert done is True
