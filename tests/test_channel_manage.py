"""Tests for channel_manage dispatch."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from nls.runtime.channel_manage import (
    dispatch_channel_manage,
    format_scoped_channel_status,
    register_channel_manage_handler,
)


def test_format_scoped_channel_status():
    text = format_scoped_channel_status("Discord", {
        "bot_username": "Babo",
        "bot_id": "1",
        "active_channel_count": 1,
        "scoped_channel_count": 2,
        "channels": [
            {"id": "ch1", "name": "general", "effective_enabled": True},
        ],
    })
    assert "Babo" in text
    assert "#general" in text


@pytest.mark.asyncio
async def test_custom_channel_handler():
    async def _handler(agent_id: str, action: str, params: dict):
        return True, f"custom:{agent_id}:{action}"

    _handler.manage_actions = ["ping"]  # type: ignore[attr-defined]
    register_channel_manage_handler("mybridge", _handler)
    ok, msg = await dispatch_channel_manage("a1", "mybridge", "ping", {})
    assert ok and msg == "custom:a1:ping"


@pytest.mark.asyncio
async def test_discord_manage_via_adapter():
    adapter_mod = importlib.import_module(
        "nls.skills.bundled.discord-channel.adapter",
    )
    adapter = adapter_mod.DiscordAdapter({}, MagicMock())
    adapter.sync_channels_from_platform = AsyncMock(return_value={})
    adapter.get_status = MagicMock(return_value={
        "bot_username": "bot",
        "bot_id": "9",
        "active_channel_count": 0,
        "scoped_channel_count": 0,
        "channels": [],
        "sync_error": "",
    })

    mock_sk = MagicMock()
    mock_sk.context.adapter = adapter
    mock_app = MagicMock()
    mock_app.state.skill_loader.skills = {"discord-channel": mock_sk}

    import server.main as main_mod
    original = getattr(main_mod, "app", None)
    main_mod.app = mock_app
    try:
        ok, msg = await dispatch_channel_manage("agent-x", "discord", "list", {})
    finally:
        if original is not None:
            main_mod.app = original

    assert ok
    assert "Discord" in msg
