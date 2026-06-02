"""Triage boost: shell work without forcing solo_structured profile."""

from __future__ import annotations

from nls.agentic.goals import TurnTriage
from nls.agentic.profile_guard_policy import (
    boost_triage_for_work_continuation,
    conversational_tool_surface,
)


def test_switch_to_execution_mode_keeps_conversational_profile():
    triage = TurnTriage(
        intent="CHAT_THINK",
        profile="conversational",
        thinking=True,
    )
    boost_triage_for_work_continuation(
        triage,
        "switch to execution mode, should unlock bash no ?",
    )
    assert triage.profile == "conversational"
    assert triage.intent == "TASK_THINK"
    assert triage.needs_tools is True


def test_ok_done_after_discord_context_keeps_conversational():
    triage = TurnTriage(
        intent="CHAT_NOTHINK",
        profile="conversational",
        thinking=False,
    )
    history = [
        {"role": "user", "content": "Set up discord-admin with my bot token"},
        {"role": "assistant", "content": "Grant admin permissions in server settings"},
    ]
    boost_triage_for_work_continuation(triage, "ok done", history=history)
    assert triage.profile == "conversational"
    assert triage.intent == "TASK_THINK"
    assert conversational_tool_surface("ok done", history=history) == "executing"
