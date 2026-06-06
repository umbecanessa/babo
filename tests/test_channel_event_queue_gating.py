"""Inner loop event-queue gating while foreground work runs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from nls.engine.execution_slots import ExecutionSlotManager
from nls.engine.inner_loop import InnerLoop


def test_can_dispatch_v2_allows_channel_while_orchestration_busy():
    rt = SimpleNamespace(
        is_busy=True,
        is_user_busy=False,
        is_agentic_enabled=lambda: True,
        inference_available=lambda: True,
        config={"agency": {"agentic_loop": {"use_v2": True}}},
    )
    il = InnerLoop.__new__(InnerLoop)
    il._autonomous_executing = False
    il._use_model_a = False

    assert il._can_dispatch_v2(rt) is True


def test_can_dispatch_v2_blocks_while_user_busy():
    rt = SimpleNamespace(
        is_busy=True,
        is_user_busy=True,
        is_agentic_enabled=lambda: True,
        inference_available=lambda: True,
        config={"agency": {"agentic_loop": {"use_v2": True}}},
    )
    il = InnerLoop.__new__(InnerLoop)
    il._autonomous_executing = False
    il._use_model_a = False

    assert il._can_dispatch_v2(rt) is False


def test_deep_slot_tracks_lock_separately_from_is_busy():
    mgr = ExecutionSlotManager()

    async def _check() -> None:
        assert mgr.deep.is_busy is False
        async with mgr.acquire_deep(source="job"):
            assert mgr.deep.is_busy is True
        assert mgr.deep.is_busy is False

    asyncio.run(_check())
