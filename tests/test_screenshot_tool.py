"""Screenshot tool reads Visual Cortex user/agent channels (not bogus 'tool')."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from nls.tools.agent_tools.screenshot import ScreenshotTool


@dataclass
class _FakeEvent:
    timestamp: float
    channel: str
    description: str = "Discord window visible"


class _FakeBuffer:
    def __init__(self, events: list[_FakeEvent]):
        self._events = events

    @property
    def latest(self):
        return self._events[-1] if self._events else None


class _FakeVC:
    def __init__(self, events: list[_FakeEvent]):
        self._events = events
        self.buffer = _FakeBuffer(events)

    def get_visual_context(self, *, channel: str | None = None) -> str:
        for ev in reversed(self._events):
            if channel is not None and ev.channel != channel:
                continue
            if ev.description:
                return f"[VISUAL|{ev.channel}] {ev.description}"
        return ""


@pytest.mark.asyncio
async def test_screenshot_reads_user_channel_not_tool():
    vc = _FakeVC([
        _FakeEvent(timestamp=1_000_000.0, channel="user", description="Discord #general"),
    ])
    tool = ScreenshotTool(vc)
    result = await tool.execute({"question": "What is on screen?"})
    assert not result.is_error
    assert "Discord" in result.content


@pytest.mark.asyncio
async def test_screenshot_falls_back_to_look_now_when_buffer_empty():
    vc = MagicMock()
    vc.get_visual_context.return_value = ""
    vc.buffer = MagicMock(latest=None)
    vc.look_now = AsyncMock(return_value="Fresh capture: chrome.exe Discord")

    tool = ScreenshotTool(vc)
    result = await tool.execute({})
    assert not result.is_error
    assert "Fresh capture" in result.content
    vc.look_now.assert_awaited_once()
