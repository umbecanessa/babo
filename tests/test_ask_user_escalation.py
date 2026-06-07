"""ask_user + team member escalation integration guards."""

from __future__ import annotations

import asyncio
import time

import pytest

from nls.agentic.events import AgentEvent, EventType
from nls.agentic.executor import _handle_ask_user
from nls.agentic.bridge import LoopHooks
from nls.agentic.orchestration_policy import (
    note_escalation_from_dispatch_source,
    note_escalation_from_text,
    parse_escalation_steering,
)
from nls.agentic.types import LoopState


def test_parse_escalation_steering_team_bracket():
    meta = parse_escalation_steering(
        "[TEAM MEMBER HELP REQUEST — PROACTIVE]\n"
        "Team: Wave 2 [team_abc123def]\n"
        "Member #0 (delegate #1): verify backend\n"
        "Reason: ask_user: What is the python path?\n",
    )
    assert meta["team_id"] == "team_abc123def"
    assert meta["member_idx"] == 0
    assert meta.get("ask_user") is True


def test_note_escalation_from_text_sets_pending_fields():
    state = LoopState(user_input="")
    help_msg = (
        "[TEAM MEMBER HELP REQUEST — PROACTIVE]\n"
        "Team: Scaffold [team_wave0]\n"
        "Member #2 (delegate #5): task\n"
        "Reason: ask_user: need token\n"
    )
    assert note_escalation_from_text(state, help_msg) is True
    assert state.has_pending_escalation is True
    assert state.pending_escalation_team_id == "team_wave0"
    assert state.pending_escalation_member_idx == 2


def test_note_escalation_from_dispatch_source_resolves_member():
    class _Member:
        def __init__(self, delegate_number: int):
            self.delegate_number = delegate_number

    class _Team:
        members = [_Member(1), _Member(3)]

    class _TM:
        _teams = {"team_x": _Team()}

    state = LoopState(user_input="")
    assert note_escalation_from_dispatch_source(
        state,
        "team_member_escalation:team_x:3",
        _TM(),
    )
    assert state.has_pending_escalation is True
    assert state.pending_escalation_team_id == "team_x"
    assert state.pending_escalation_member_idx == 1


@pytest.mark.asyncio
async def test_handle_ask_user_skips_escalation_then_accepts_user_answer():
    events: list[AgentEvent] = []
    queue: asyncio.Queue = asyncio.Queue()
    hooks = LoopHooks(copilot_queue=queue)
    state = LoopState(user_input="")

    async def on_event(event: AgentEvent) -> None:
        events.append(event)

    async def _feeder() -> None:
        await asyncio.sleep(0.05)
        queue.put_nowait(
            "[TEAM MEMBER HELP REQUEST — PROACTIVE]\n"
            "Team: T [team_feed]\nMember #0: x\n",
        )
        await asyncio.sleep(0.05)
        queue.put_nowait("use the project .venv")

    feeder = asyncio.create_task(_feeder())
    t0 = time.perf_counter()
    result = await _handle_ask_user(
        {"question": "Python path?"},
        on_event,
        hooks,
        iteration=1,
        tool_call_id="c1",
        state=state,
    )
    await feeder
    elapsed = time.perf_counter() - t0

    assert elapsed < 5.0
    assert "use the project .venv" in result.content
    assert state.has_pending_escalation is True
    assert state.pending_escalation_team_id == "team_feed"
    assert EventType.ASK_USER in [e.type for e in events]
    assert EventType.COMMUNICATE not in [e.type for e in events]


@pytest.mark.asyncio
async def test_handle_ask_user_accepts_dict_user_answer():
    events: list[AgentEvent] = []

    async def on_event(event: AgentEvent) -> None:
        events.append(event)

    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait({"role": "user", "content": "use the project .venv"})
    hooks = LoopHooks(copilot_queue=queue)

    result = await _handle_ask_user(
        {"question": "Python path?"},
        on_event,
        hooks,
        iteration=1,
        tool_call_id="c2",
    )

    assert "use the project .venv" in result.content
