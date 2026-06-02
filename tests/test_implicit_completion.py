"""Implicit loop exit must not fire after lookup-only tool use on setup tasks."""

from __future__ import annotations

import pytest

from nls.agentic.evaluator import should_complete
from nls.agentic.types import LoopConfig, LoopState


@pytest.mark.asyncio
async def test_instruction_skill_setup_not_complete_after_read_only():
    """Regression: Discord setup loop exited after read+contacts+clawhub only."""
    state = LoopState(user_input="setup discord bot with token")
    state.goals = ["Install Discord tool from ClawHub"]
    state.hints = ["setup:instruction_skill"]
    state.orchestration_profile = "solo_structured"
    state.consecutive_text_only = 1
    state.total_tool_calls = 4
    state.tool_successes = {"contacts": 1, "clawhub": 1, "read": 1}
    state._last_iter_text = (
        "You're absolutely right — let's get Babo connected to Discord! 🎉"
    )

    done = await should_complete(state, LoopConfig(), None, vllm_client=None)
    assert done is False


@pytest.mark.asyncio
async def test_instruction_skill_setup_blocks_prose_only_exit_even_after_bash():
    """Setup tasks must call task_complete after verify — not prose-only implicit exit."""
    state = LoopState(user_input="setup discord")
    state.goals = ["Configure discord-admin skill"]
    state.hints = ["setup:instruction_skill"]
    state.orchestration_profile = "solo_structured"
    state.consecutive_text_only = 1
    state.total_tool_calls = 5
    state.tool_successes = {"read": 1, "bash": 1}
    state._last_iter_text = "Bot token verified and discord-admin.sh is ready to run."

    done = await should_complete(state, LoopConfig(), None, vllm_client=None)
    assert done is False
