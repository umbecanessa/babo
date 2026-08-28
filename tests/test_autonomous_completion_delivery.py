"""Tests for autonomous completion delivery to channels and Home."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nls.runtime.autonomous_completion_delivery import (
    deliver_autonomous_completion,
    extract_todo_id,
    format_completion_summary,
    parse_session_key_from_text,
    resolve_report_session_key,
    should_deliver_autonomous_completion,
)
from nls.runtime.todo_report_targets import (
    ensure_todo_report_session_key,
    resolve_explicit_report_session_key,
)


def _runtime_mock(**attrs):
    rt = MagicMock()
    rt._session_router = None
    rt.agent_id = attrs.get("agent_id", "agent-1")
    rt._foreground_session_key = attrs.get("_foreground_session_key", "")
    rt._foreground_source = attrs.get("_foreground_source", "")
    rt.default_home_session_key = attrs.get("default_home_session_key", "websocket:main")
    rt.agent_dir = attrs.get("agent_dir", MagicMock())
    if "channel_registry" in attrs:
        rt.channel_registry = attrs["channel_registry"]
    return rt


def test_extract_todo_id_from_prompt():
    assert extract_todo_id("Work on todo [fe270c2e]: black screen") == "fe270c2e"
    assert extract_todo_id("no todo here") is None


def test_parse_session_key_from_channel_header():
    text = (
        "[CHANNEL: telegram | reply_to: -1003721736976 | sender: @test]\n"
        "Bug report"
    )
    assert parse_session_key_from_text(text) == "telegram:group:-1003721736976"


def test_parse_session_key_from_explicit_key():
    assert parse_session_key_from_text(
        "Report back to telegram:group:-1003721736976 when done"
    ) == "telegram:group:-1003721736976"


def test_format_completion_summary_prefers_findings():
    raw = (
        "## Investigation Complete\n\n"
        "### Root Cause\nCache corruption causes black screen.\n\n"
        "### Recommendations\nAdd clear-cache button."
    )
    out = format_completion_summary(raw, title="Black screen bug")
    assert "Black screen bug" in out
    assert "Root Cause" in out or "Cache corruption" in out


def test_should_deliver_for_job_background_task_complete():
    assert should_deliver_autonomous_completion(
        source="job_background:agent-1",
        final_response="Investigation complete. Root cause: stale JWT.",
        exit_reason="task_complete",
        aborted=False,
    )
    assert not should_deliver_autonomous_completion(
        source="job_background:agent-1",
        final_response="NOOP",
        exit_reason="task_complete",
        aborted=False,
    )
    assert not should_deliver_autonomous_completion(
        source="user",
        final_response="Done",
        exit_reason="task_complete",
        aborted=False,
    )
    assert should_deliver_autonomous_completion(
        source="todo-list",
        final_response="Investigation complete. Root cause: stale JWT token in cache.",
        exit_reason="task_complete",
        aborted=False,
    )


def test_resolve_report_session_key_never_guesses_single_group():
    rt = _runtime_mock()
    rt.channel_registry.session_router.list_sessions.return_value = {
        "telegram:group:-1003721736976": {"channel": "telegram"},
    }
    key = resolve_report_session_key(
        rt,
        prompt="Investigate login bug",
        final_response="Done",
        todo_id=None,
    )
    assert key == "websocket:main"


def test_resolve_report_session_key_from_todo_field():
    rt = _runtime_mock()

    todo = MagicMock()
    todo.report_session_key = "telegram:group:-1003721736976"
    todo.tags = []
    todo.title = "QA bug"
    todo.description = ""
    todo.notes = ""

    with patch(
        "nls.runtime.autonomous_completion_delivery._load_todo_item",
        return_value=todo,
    ):
        key = resolve_report_session_key(
            rt,
            prompt="Work on todo [fe270c2e]",
            final_response="Done",
            todo_id="fe270c2e",
        )
    assert key == "telegram:group:-1003721736976"


def test_resolve_explicit_from_job_report_channels():
    rt = _runtime_mock()

    job = MagicMock()
    job.report_channels = [{
        "session_key": "telegram:group:-1003721736976",
        "purpose": "qa_reports",
    }]

    with patch("nls.runtime.job_trust.load_job", return_value=job):
        key = resolve_explicit_report_session_key(
            rt,
            title="Investigate black screen QA bug",
        )
    assert key == "telegram:group:-1003721736976"


def test_ensure_todo_report_session_key_backfills_from_text():
    rt = _runtime_mock()

    todo = MagicMock()
    todo.id = "fe270c2e"
    todo.report_session_key = ""
    todo.tags = []
    todo.title = "QA bug"
    todo.description = "Report to telegram:group:-1003721736976"
    todo.notes = ""

    store = MagicMock()
    store.update.return_value = todo

    mgr = MagicMock()
    mgr.get_store.return_value = store

    skill = MagicMock()
    skill.context.adapter = mgr

    app = MagicMock()
    app.state.skill_loader.skills = {"todo-list": skill}

    with patch("server.main.app", app):
        key = ensure_todo_report_session_key(rt, todo)

    assert key == "telegram:group:-1003721736976"
    store.update.assert_called_once_with(
        "fe270c2e", report_session_key="telegram:group:-1003721736976",
    )


def test_runtime_channel_session_only_during_channel_turn():
    rt = _runtime_mock(_foreground_session_key="telegram:group:-100111", _foreground_source="user")
    assert resolve_explicit_report_session_key(rt, title="Home todo") == "websocket:main"

    rt._foreground_source = "user:channel"
    assert resolve_explicit_report_session_key(rt, title="Channel todo") == (
        "telegram:group:-100111"
    )


def test_inbox_single_unhandled_requires_content_overlap():
    from nls.runtime.surface_inbox import resolve_report_session_from_inbox

    agent_id = "agent-1"
    with patch("nls.runtime.surface_inbox._inboxes", {
        agent_id: [
            MagicMock(
                session_key="telegram:group:-100111",
                handled=False,
                content="unrelated admin ping",
                preview="unrelated admin ping",
                received_at=1.0,
            ),
        ],
    }), patch("nls.runtime.surface_inbox.load_agent_inbox"):
        key = resolve_report_session_from_inbox(
            agent_id,
            content_blob="black screen QA regression bug",
        )
    assert key is None


@pytest.mark.asyncio
async def test_deliver_autonomous_completion_broadcasts_home_and_channel(tmp_path):
    rt = _runtime_mock(agent_dir=tmp_path)
    rt.get_default_home_session_key.return_value = "websocket:thread:home1"

    todo = MagicMock()
    todo.report_session_key = "telegram:group:-1003721736976"
    todo.tags = []
    todo.title = "QA bug"
    todo.description = ""
    todo.notes = ""

    cm = AsyncMock()
    final = (
        "Investigation complete.\n"
        "Root cause: corrupted PlayerPrefs.\n"
        "Workaround: clear AppData\\LocalLow\\PinkMoon."
    )

    with patch(
        "nls.runtime.autonomous_completion_delivery._load_todo_item",
        return_value=todo,
    ):
        with patch(
            "nls.skills.surface_send.send_surface_message",
            new_callable=AsyncMock,
        ) as send_surface:
            send_surface.return_value = {"ok": True}
            with patch(
                "nls.runtime.autonomous_completion_delivery.OutboundNotifyLedger",
            ) as ledger_cls:
                ledger = MagicMock()
                ledger.should_skip.return_value = False
                ledger_cls.return_value = ledger

                outcome = await deliver_autonomous_completion(
                    rt,
                    source="job_background:agent-1",
                    prompt="Work on todo [fe270c2e]",
                    final_response=final,
                    exit_reason="task_complete",
                    aborted=False,
                    todo_id="fe270c2e",
                    connection_manager=cm,
                )

    assert outcome["delivered"] is True
    assert outcome["channel"] == "telegram:group:-1003721736976"
    assert outcome["home"] is True
    send_surface.assert_awaited_once()
    cm.broadcast.assert_awaited()
    broadcast_payload = cm.broadcast.await_args.args[1]
    assert broadcast_payload.get("session_key") == "websocket:thread:home1"
