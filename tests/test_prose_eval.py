"""Prose-turn micro-inference and loop exit for repeated credential prompts."""

from __future__ import annotations

import pytest

from nls.agentic.evaluator import (
    detect_stall,
    prose_stream_text,
    refresh_prose_verdict,
    should_complete,
    should_run_prose_eval,
)
from nls.agentic.types import AgentMode
from nls.agentic.goals import evaluate_prose_turn, prose_fingerprint
from nls.agentic.loop import apply_final_response_backfill
from nls.agentic.types import LoopConfig, LoopState


@pytest.mark.asyncio
async def test_awaiting_user_input_after_401_heuristic():
    state = LoopState(user_input="setup discord bot")
    state.goals = ["Connect bot to Discord"]
    state.hints = ["setup:instruction_skill"]
    state.orchestration_profile = "solo_structured"
    state.consecutive_text_only = 2
    state.total_tool_calls = 6
    state.last_error_preview = "HTTP 401 Unauthorized"
    state._last_iter_text = (
        "The bot token returned 401. Please reset your Discord bot token "
        "and paste the new one here."
    )

    await refresh_prose_verdict(state, vllm_client=None)
    assert state.last_prose_verdict == "awaiting_user_input"

    done = await should_complete(state, LoopConfig(), None, vllm_client=None)
    assert done is True


@pytest.mark.asyncio
async def test_duplicate_prose_suppressed():
    text = "Please provide a new Discord bot token so I can continue."
    state = LoopState()
    state.consecutive_text_only = 3
    state.last_prose_hash = prose_fingerprint(text)
    state._last_iter_text = text
    state.last_prose_verdict = "duplicate"
    state.prose_show_to_user = False

    assert prose_stream_text(state, text) == ""

    done = await should_complete(state, LoopConfig(), None, vllm_client=None)
    assert done is True


@pytest.mark.asyncio
async def test_first_prose_held_when_eval_says_not_to_show():
    """First prose-only turn must respect show_to_user=false from micro-inference."""
    state = LoopState(user_input="setup discord")
    state.goals = ["Install Discord tool from ClawHub"]
    state.hints = ["setup:instruction_skill"]
    state.consecutive_text_only = 1
    state._last_iter_text = (
        "Paste your Discord bot token here and I will connect immediately."
    )
    state.last_prose_verdict = "should_continue"
    state.prose_show_to_user = False

    assert prose_stream_text(state, state._last_iter_text) == ""

    done = await should_complete(state, LoopConfig(), None, vllm_client=None)
    assert done is False


@pytest.mark.asyncio
async def test_refresh_prose_verdict_runs_on_first_text_only_turn():
    state = LoopState(user_input="setup discord")
    state.goals = ["Connect bot"]
    state.consecutive_text_only = 1
    state.last_error_preview = "HTTP 401 Unauthorized"
    state._last_iter_text = (
        "The bot token returned 401. Please reset your Discord bot token "
        "and paste the new one here."
    )

    await refresh_prose_verdict(state, vllm_client=None)
    assert state.last_prose_verdict == "awaiting_user_input"
    assert state.prose_show_to_user is True


@pytest.mark.asyncio
async def test_instruction_skill_still_blocks_premature_exit():
    """First prose after lookup-only tools must not exit without verify."""
    state = LoopState(user_input="setup discord")
    state.goals = ["Install Discord tool from ClawHub"]
    state.hints = ["setup:instruction_skill"]
    state.orchestration_profile = "solo_structured"
    state.consecutive_text_only = 1
    state.total_tool_calls = 4
    state.tool_successes = {"contacts": 1, "clawhub": 1, "read": 1}
    state._last_iter_text = "Let's get Babo connected to Discord!"

    await refresh_prose_verdict(state, vllm_client=None)
    done = await should_complete(state, LoopConfig(), None, vllm_client=None)
    assert done is False


@pytest.mark.asyncio
async def test_evaluate_prose_turn_duplicate_without_llm():
    text = "Need your token please."
    verdict, show = await evaluate_prose_turn(
        None,
        goals=["Setup discord"],
        action_summary="bash: curl 401",
        prose=text,
        prior_prose_hash=prose_fingerprint(text),
        consecutive_text_only=2,
    )
    assert verdict == "duplicate"
    assert show is False


def test_read_reread_stall_nudge():
    state = LoopState()
    state.tool_successes = {"read": 5}
    state.tool_call_signatures = [
        'read:{"path":"skills/discord-admin/SKILL.md"}',
        'read:{"path":"skills/discord-admin/SKILL.md"}',
        'read:{"path":"skills/discord-admin/SKILL.md"}',
        'read:{"path":"skills/discord-admin/SKILL.md"}',
    ]
    msg = detect_stall(state, LoopConfig(max_iterations=40))
    assert msg is not None
    low = msg.lower()
    assert "re-reading" in low or "repeating" in low


def test_max_iterations_backfill_uses_last_prose():
    state = LoopState()
    state.exit_reason = "max_iterations"
    state._last_iter_text = "Bot is online in #general."
    apply_final_response_backfill(state, "")
    assert "Bot is online" in state.final_response


def test_skip_prose_eval_pure_conversational_chat():
    state = LoopState(user_input="Your name is Babo")
    state.orchestration_profile = "conversational"
    state.active_mode = AgentMode.CHAT
    state.consecutive_text_only = 1
    state.total_tool_calls = 0
    state._last_iter_text = "Thank you! I'm Babo."

    assert should_run_prose_eval(state) is False


def test_prose_eval_runs_after_conversational_used_tools():
    state = LoopState(user_input="look up discord docs")
    state.orchestration_profile = "conversational"
    state.active_mode = AgentMode.EXECUTING
    state.consecutive_text_only = 1
    state.total_tool_calls = 2
    state._last_iter_text = "Paste your bot token here."

    assert should_run_prose_eval(state) is True
