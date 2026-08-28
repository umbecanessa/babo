"""Session routing resolver and delivery policy tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nls.runtime.session_routing.config import (
    DeliveryExclusion,
    ReportChannelPolicy,
    SessionRoutingConfig,
)
from nls.runtime.session_routing.resolver import resolve_delivery_targets, resolve_report_session_keys
from nls.runtime.session_routing.types import DeliveryIntent, RoutingContext


def _cfg(**kwargs) -> SessionRoutingConfig:
    base = SessionRoutingConfig(
        default_home_session_key="websocket:main",
        primary_reachability_session_key="websocket:main",
    )
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


def test_report_prefers_todo_explicit_session_key():
    rt = MagicMock()
    todo = MagicMock()
    todo.report_session_key = "telegram:group:-100111"
    todo.tags = []
    todo.title = "QA bug"
    todo.description = ""
    todo.notes = ""

    keys = resolve_report_session_keys(
        rt,
        _cfg(),
        ctx=RoutingContext(prompt="Work on todo"),
        todo_item=todo,
    )
    assert keys == ["telegram:group:-100111"]


def test_report_uses_origin_for_channel_foreground():
    rt = MagicMock()
    ctx = RoutingContext(
        foreground_source="user:channel",
        foreground_session_key="telegram:group:-100222",
        origin_session_key="telegram:group:-100222",
        prompt="Investigate login bug",
    )
    keys = resolve_report_session_keys(rt, _cfg(), ctx=ctx)
    assert keys == ["telegram:group:-100222"]


def test_report_never_guesses_from_single_registered_group():
    rt = MagicMock()
    keys = resolve_report_session_keys(
        rt,
        _cfg(report_channels=[
            ReportChannelPolicy(
                session_key="telegram:group:-100333",
                purposes=["qa_reports"],
            ),
        ]),
        ctx=RoutingContext(prompt="Weekly status update"),
    )
    assert keys == ["websocket:main"]


def test_report_broadcast_matching_when_configured():
    rt = MagicMock()
    cfg = _cfg(
        default_report_mode="broadcast_matching",
        report_channels=[
            ReportChannelPolicy(
                session_key="telegram:group:-100111",
                purposes=["qa_reports"],
            ),
            ReportChannelPolicy(
                session_key="telegram:group:-100222",
                purposes=["qa_reports"],
            ),
        ],
    )
    keys = resolve_report_session_keys(
        rt,
        cfg,
        ctx=RoutingContext(prompt="Investigate black screen regression"),
    )
    assert set(keys) == {"telegram:group:-100111", "telegram:group:-100222"}


def test_primary_reachability_fallback_for_progress_without_origin():
    rt = MagicMock()
    cfg = _cfg(primary_reachability_session_key="telegram:group:-100999")
    targets = resolve_delivery_targets(
        rt,
        cfg,
        intent=DeliveryIntent.PROGRESS,
        ctx=RoutingContext(prompt="Background QA sweep"),
    )
    assert [t.session_key for t in targets] == ["telegram:group:-100999"]


def test_exclusion_blocks_report_to_session():
    rt = MagicMock()
    cfg = _cfg(
        report_channels=[
            ReportChannelPolicy(
                session_key="telegram:group:-100111",
                purposes=["qa_reports"],
            ),
        ],
        exclusions=[
            DeliveryExclusion(
                session_key="telegram:group:-100111",
                block_intents=["report"],
            ),
        ],
    )
    todo = MagicMock()
    todo.report_session_key = "telegram:group:-100111"
    todo.tags = []
    todo.title = ""
    todo.description = ""
    todo.notes = ""

    keys = resolve_report_session_keys(rt, cfg, ctx=RoutingContext(), todo_item=todo)
    assert keys == ["websocket:main"]


@pytest.mark.asyncio
async def test_deliver_message_broadcasts_home_with_session_key():
    from nls.runtime.session_routing.delivery import deliver_message

    rt = MagicMock()
    rt.agent_id = "agent-1"
    rt.get_default_home_session_key.return_value = "websocket:thread:abc"

    cm = AsyncMock()
    cfg = _cfg(
        default_home_session_key="websocket:thread:abc",
        primary_reachability_session_key="websocket:thread:abc",
    )

    outcome = await deliver_message(
        rt,
        cfg,
        message="Investigation complete",
        intent=DeliveryIntent.REPORT,
        ctx=RoutingContext(),
        connection_manager=cm,
        include_default_home=True,
    )

    assert outcome.delivered is True
    assert outcome.home is True
    cm.broadcast.assert_awaited_once()
    payload = cm.broadcast.await_args.args[1]
    assert payload["session_key"] == "websocket:thread:abc"
