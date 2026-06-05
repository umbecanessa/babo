"""Sleep negotiation — classify yes/no and apply confirm/deny."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.routes.chat.sleep_negotiation import (
    apply_sleep_confirm,
    apply_sleep_deny,
    classify_sleep_response,
    try_handle_drowsy_text,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", "confirm"),
        ("Yes please", "confirm"),
        ("go ahead", "confirm"),
        ("rest up", "confirm"),
        ("ok", "confirm"),
        ("no", "deny"),
        ("stay awake", "deny"),
        ("not now", "deny"),
        ("maybe later", None),
        ("hello", None),
        ("please help me debug", None),
    ],
)
def test_classify_sleep_response(text: str, expected: str | None) -> None:
    assert classify_sleep_response(text) == expected


@pytest.mark.asyncio
async def test_apply_sleep_confirm_when_drowsy() -> None:
    inner = MagicMock()
    inner.is_drowsy = True
    ws = AsyncMock()
    app = MagicMock()
    app.state.consciousness_scheduler._agents = {
        "a1": MagicMock(inner_loop=inner),
    }

    ok = await apply_sleep_confirm(app, "a1", ws, source="test")

    assert ok is True
    inner.confirm_sleep.assert_called_once()
    assert ws.send_json.await_count == 2


@pytest.mark.asyncio
async def test_apply_sleep_confirm_when_not_drowsy() -> None:
    inner = MagicMock()
    inner.is_drowsy = False
    ws = AsyncMock()
    app = MagicMock()
    app.state.consciousness_scheduler._agents = {
        "a1": MagicMock(inner_loop=inner),
    }

    ok = await apply_sleep_confirm(app, "a1", ws, source="test")

    assert ok is False
    inner.confirm_sleep.assert_not_called()
    payload = ws.send_json.await_args_list[0].args[0]
    assert payload["type"] == "sleep_command_result"
    assert payload["ok"] is False


@pytest.mark.asyncio
async def test_try_handle_drowsy_text_routes_yes() -> None:
    inner = MagicMock()
    inner.is_drowsy = True
    ws = AsyncMock()
    app = MagicMock()
    app.state.consciousness_scheduler._agents = {
        "a1": MagicMock(inner_loop=inner),
    }

    handled = await try_handle_drowsy_text(
        app, "a1", ws, "yes", source="message",
    )

    assert handled is True
    inner.confirm_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_try_handle_drowsy_text_ignores_non_drowsy() -> None:
    inner = MagicMock()
    inner.is_drowsy = False
    ws = AsyncMock()
    app = MagicMock()
    app.state.consciousness_scheduler._agents = {
        "a1": MagicMock(inner_loop=inner),
    }

    handled = await try_handle_drowsy_text(
        app, "a1", ws, "yes", source="message",
    )

    assert handled is False
    inner.confirm_sleep.assert_not_called()
    ws.send_json.assert_not_called()


def test_request_sleep_tool_calls_voluntary_sleep() -> None:
    from nls.engine.tools_builtin import RequestSleepTool

    ans = MagicMock()
    tool = RequestSleepTool(ans=ans)
    result = tool.execute({"reason": "consolidate squad setup"})

    assert result.success is True
    ans.request_voluntary_sleep.assert_called_once_with(
        reason="consolidate squad setup",
    )
