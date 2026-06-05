"""Integration-style tests for orchestrator budget extension wait/resume."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nls.agentic.budget_prompt import classify_budget_response, parse_budget_decision
from nls.agentic.events import AgentEvent, EventType
from nls.agentic.loop import _await_user_budget_extend
from nls.agentic.types import LoopConfig, LoopState


def _cfg(**overrides):
    base = dict(
        prompt_user_on_budget_exhaust=True,
        enable_delegation=True,
        escalate_on_limit=False,
        max_iterations=40,
        max_iterations_extension=50,
        max_total_iterations=300,
        budget_prompt_wait_seconds=2.0,
        budget_prompt_options=(10, 20, 40),
        max_user_budget_prompts=3,
        total_timeout_seconds=1800.0,
    )
    base.update(overrides)
    return LoopConfig(**base)


@pytest.mark.asyncio
async def test_await_user_budget_extend_accepts_queue_extend():
    state = LoopState(user_input="read repo")
    state.dispatch_source = "user"
    state.iteration = 40
    config = _cfg()
    queue: asyncio.Queue = asyncio.Queue()
    context: list[dict] = []
    events: list[AgentEvent] = []

    async def on_event(ev: AgentEvent) -> None:
        events.append(ev)

    async def _decide_later() -> None:
        await asyncio.sleep(0.05)
        await queue.put({"action": "extend", "extra_iterations": 20})

    asyncio.create_task(_decide_later())
    continued = await _await_user_budget_extend(
        "max_iterations",
        state,
        config,
        queue,
        context,
        on_event,
        has_active_team=False,
    )

    assert continued is True
    assert config.max_iterations == 60
    assert state.user_budget_prompts == 1
    assert any(e.type == EventType.LOOP_BUDGET_PROMPT for e in events)
    assert any(e.type == EventType.BUDGET_DECISION for e in events)


@pytest.mark.asyncio
async def test_await_user_budget_extend_user_stop_grants_wrap_up():
    state = LoopState(user_input="read repo")
    state.dispatch_source = "user"
    state.iteration = 40
    config = _cfg()
    queue: asyncio.Queue = asyncio.Queue()
    context: list[dict] = []
    events: list[AgentEvent] = []

    async def on_event(ev: AgentEvent) -> None:
        events.append(ev)

    await queue.put({"action": "terminate"})
    continued = await _await_user_budget_extend(
        "max_iterations",
        state,
        config,
        queue,
        context,
        on_event,
        has_active_team=False,
    )

    assert continued is True
    assert state.budget_declined_by_user is True
    assert config.max_iterations == 43
    assert any("USER STOP" in (m.get("content") or "") for m in context if m.get("role") == "system")


@pytest.mark.asyncio
async def test_await_user_budget_extend_steering_string_not_budget():
    state = LoopState(user_input="read repo")
    state.dispatch_source = "user"
    state.iteration = 40
    config = _cfg(budget_prompt_wait_seconds=0.15)
    queue: asyncio.Queue = asyncio.Queue()
    context: list[dict] = []

    async def on_event(ev: AgentEvent) -> None:
        pass

    async def _send_steering() -> None:
        await asyncio.sleep(0.02)
        await queue.put("continue reading the auth module in parallel")

    asyncio.create_task(_send_steering())
    continued = await _await_user_budget_extend(
        "max_iterations",
        state,
        config,
        queue,
        context,
        on_event,
        has_active_team=False,
    )

    assert continued is True
    assert state.budget_prompt_timed_out is True
    assert any(m.get("role") == "user" for m in context)


def test_classify_rejects_copilot_continue_guidance():
    assert classify_budget_response("continue reading the auth module", [10, 20, 40]) is None


def test_classify_accepts_exact_affirmative():
    d = classify_budget_response("yes", [10, 20, 40])
    assert d is not None
    assert d.action == "extend"
    assert d.extra_iterations == 10


def test_parse_user_answer_string_via_queue_pattern():
    d = parse_budget_decision("40", [10, 20, 40])
    assert d is not None
    assert d.extra_iterations == 40
