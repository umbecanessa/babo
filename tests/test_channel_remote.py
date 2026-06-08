"""Tests for channel_remote dispatch."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from nls.tools.agent_tools.base import ToolResult
from nls.runtime.channel_remote import (
    channel_remote_actions,
    dispatch_channel_remote,
    format_discord_message_content,
    format_message_rows,
    list_channel_remote_channels,
)


def test_format_message_rows_empty():
    assert "No messages" in format_message_rows("discord", [])


def test_format_discord_message_content_includes_embeds():
    text = format_discord_message_content({
        "content": "",
        "embeds": [{
            "title": "Hello",
            "description": "World",
            "fields": [{"name": "Status", "value": "ok"}],
        }],
        "attachments": [{"filename": "report.pdf"}],
    })
    assert "[embed]" in text
    assert "Hello" in text
    assert "report.pdf" in text


def test_format_message_rows_truncates_long_content():
    text = format_message_rows("discord", [{
        "id": "1",
        "timestamp": "2026-01-01",
        "author": "user",
        "content": "x" * 500,
    }])
    assert "…" in text
    assert "id=1" in text


def test_list_channel_remote_channels():
    channels = list_channel_remote_channels()
    assert "discord" in channels
    assert "slack" in channels


@pytest.mark.asyncio
async def test_dispatch_help_unknown_channel():
    ok, msg = await dispatch_channel_remote("a1", "nosuch", "help", {})
    assert not ok
    assert "not loaded" in msg.lower() or "no remote" in msg.lower()


@pytest.mark.asyncio
async def test_dispatch_read_missing_channel_id():
    adapter_mod = importlib.import_module(
        "nls.skills.bundled.discord-channel.adapter",
    )
    adapter = adapter_mod.DiscordAdapter({}, MagicMock())
    adapter.fetch_channel_messages = AsyncMock(return_value=(True, "ok"))

    mock_sk = MagicMock()
    mock_sk.context.adapter = adapter
    mock_app = MagicMock()
    mock_app.state.skill_loader.skills = {"discord-channel": mock_sk}

    import server.main as main_mod
    original = getattr(main_mod, "app", None)
    main_mod.app = mock_app
    try:
        ok, msg = await dispatch_channel_remote("agent-x", "discord", "read", {})
    finally:
        if original is not None:
            main_mod.app = original

    assert not ok
    assert "channel_id" in msg.lower()
    adapter.fetch_channel_messages.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_read_via_adapter():
    adapter_mod = importlib.import_module(
        "nls.skills.bundled.discord-channel.adapter",
    )
    adapter = adapter_mod.DiscordAdapter({}, MagicMock())
    adapter.fetch_channel_messages = AsyncMock(
        return_value=(True, "discord messages (1):\n[id=9]"),
    )

    mock_sk = MagicMock()
    mock_sk.context.adapter = adapter
    mock_app = MagicMock()
    mock_app.state.skill_loader.skills = {"discord-channel": mock_sk}

    import server.main as main_mod
    original = getattr(main_mod, "app", None)
    main_mod.app = mock_app
    try:
        ok, msg = await dispatch_channel_remote(
            "agent-x",
            "discord",
            "read",
            {"channel_id": "123", "limit": 10},
        )
    finally:
        if original is not None:
            main_mod.app = original

    assert ok
    assert "discord messages" in msg
    adapter.fetch_channel_messages.assert_awaited_once_with(
        "agent-x", "123", limit=10, before=None,
    )


def test_discord_adapter_declares_remote_actions():
    adapter_mod = importlib.import_module(
        "nls.skills.bundled.discord-channel.adapter",
    )
    adapter = adapter_mod.DiscordAdapter({}, MagicMock())
    assert adapter.channel_remote_actions() == ["read", "delete", "send"]


@pytest.mark.asyncio
async def test_dispatch_send_respects_allowed_targets():
    adapter_mod = importlib.import_module(
        "nls.skills.bundled.discord-channel.adapter",
    )
    adapter = adapter_mod.DiscordAdapter({}, MagicMock())
    adapter.get_allowed_target_ids = MagicMock(return_value={"999"})
    adapter._outbound_restricted = MagicMock(return_value=True)
    adapter.send = AsyncMock(return_value=True)

    mock_sk = MagicMock()
    mock_sk.context.adapter = adapter
    mock_app = MagicMock()
    mock_app.state.skill_loader.skills = {"discord-channel": mock_sk}

    import server.main as main_mod
    original = getattr(main_mod, "app", None)
    main_mod.app = mock_app
    try:
        ok, msg = await dispatch_channel_remote(
            "agent-x",
            "discord",
            "send",
            {"channel_id": "123", "text": "hi"},
        )
    finally:
        if original is not None:
            main_mod.app = original

    assert not ok
    assert "allowed" in msg.lower()
    adapter.send.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_send_with_file_path():
    adapter_mod = importlib.import_module(
        "nls.skills.bundled.discord-channel.adapter",
    )
    adapter = adapter_mod.DiscordAdapter({}, MagicMock())
    adapter.get_allowed_target_ids = MagicMock(return_value={"123"})
    adapter._outbound_restricted = MagicMock(return_value=False)
    adapter.send_file = AsyncMock(
        return_value=ToolResult(content="File sent to 123"),
    )

    mock_sk = MagicMock()
    mock_sk.context.adapter = adapter
    mock_app = MagicMock()
    mock_app.state.skill_loader.skills = {"discord-channel": mock_sk}

    import server.main as main_mod
    original = getattr(main_mod, "app", None)
    main_mod.app = mock_app
    try:
        ok, msg = await dispatch_channel_remote(
            "agent-x",
            "discord",
            "send",
            {"channel_id": "123", "file_path": "notes.md", "text": "see attached"},
        )
    finally:
        if original is not None:
            main_mod.app = original

    assert ok
    adapter.send_file.assert_awaited_once()
    call_kwargs = adapter.send_file.await_args.kwargs
    assert call_kwargs.get("caption") == "see attached"


@pytest.mark.asyncio
async def test_slack_fetch_resolves_display_names():
    adapter_mod = importlib.import_module(
        "nls.skills.bundled.slack-channel.adapter",
    )
    adapter = adapter_mod.SlackAdapter({}, MagicMock())
    adapter._agent_configs["agent-x"] = {"bot_token": "xoxb-test", "enabled": True}

    async def _api_post(token, method, payload):
        if method == "conversations.history":
            return {
                "ok": True,
                "messages": [{"ts": "1.0", "user": "U123", "text": "hi"}],
            }
        if method == "users.info":
            return {
                "ok": True,
                "user": {
                    "name": "alice",
                    "profile": {"display_name": "Alice", "real_name": "Alice Smith"},
                },
            }
        raise AssertionError(f"unexpected method {method}")

    adapter._api_post = _api_post  # type: ignore[method-assign]

    ok, msg = await adapter.fetch_channel_messages("agent-x", "C1", limit=5)
    assert ok
    assert "Alice (U123)" in msg
    assert "hi" in msg


@pytest.mark.asyncio
async def test_telegram_read_points_to_ambient():
    adapter_mod = importlib.import_module(
        "nls.skills.bundled.telegram-channel.adapter",
    )
    adapter = adapter_mod.TelegramAdapter({}, MagicMock())

    mock_sk = MagicMock()
    mock_sk.context.adapter = adapter
    mock_app = MagicMock()
    mock_app.state.skill_loader.skills = {"telegram-channel": mock_sk}

    import server.main as main_mod
    original = getattr(main_mod, "app", None)
    main_mod.app = mock_app
    try:
        ok, msg = await dispatch_channel_remote(
            "agent-x",
            "telegram",
            "read",
            {"channel_id": "-100123"},
        )
    finally:
        if original is not None:
            main_mod.app = original

    assert not ok
    assert "channel_history" in msg
    assert "Bot API" in msg or "cannot fetch" in msg
