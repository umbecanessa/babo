"""Tests for await_delegates virtual tool."""

from __future__ import annotations

import pytest

from nls.agentic.executor import _handle_await_delegates, _handle_wait
from nls.agentic.types import AgentMode, LoopState


class _FakeDM:
    def __init__(self, running: bool) -> None:
        self._running = running

    def has_active_delegates(self) -> bool:
        return self._running

    def get_status(self) -> list:
        return []


@pytest.mark.asyncio
async def test_await_delegates_exits_when_running():
    state = LoopState(user_input="monitor")
    state.active_mode = AgentMode.MONITORING
    r = await _handle_await_delegates(
        {"summary": "Wave 2 running — 2 agents on backend/frontend"},
        state,
        _FakeDM(True),
    )
    assert r.stop_loop
    assert r.details["type"] == "awaiting_delegates"
    assert "Wave 2" in r.content


@pytest.mark.asyncio
async def test_await_delegates_rejects_when_nothing_running():
    state = LoopState(user_input="monitor")
    r = await _handle_await_delegates(
        {"summary": "waiting"},
        state,
        _FakeDM(False),
    )
    assert r.is_error
    assert "task_complete" in r.content


@pytest.mark.asyncio
async def test_long_wait_blocked_in_monitoring():
    state = LoopState(user_input="monitor")
    state.active_mode = AgentMode.MONITORING
    r = await _handle_wait(
        {"seconds": 180, "reason": "wave running"},
        None,
        1,
        None,
        _FakeDM(True),
        state=state,
    )
    assert r.is_error
    assert "await_delegates" in r.content


@pytest.mark.asyncio
async def test_short_wait_allowed_in_monitoring():
    state = LoopState(user_input="monitor")
    state.active_mode = AgentMode.MONITORING
    r = await _handle_wait(
        {"seconds": 1, "reason": "quick poll"},
        None,
        1,
        None,
        _FakeDM(True),
        state=state,
    )
    assert not r.is_error
