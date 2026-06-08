"""Channel dispatch policy — executing mode without message regex heuristics."""

from __future__ import annotations

import json
from pathlib import Path

from nls.agentic.channel_dispatch_policy import (
    agent_has_job_charter,
    apply_channel_loop_policy,
    channel_dispatch_requires_execution,
)
from nls.agentic.types import AgentMode, LoopState


def test_agent_has_job_charter_requires_persisted_file(tmp_path: Path):
    assert not agent_has_job_charter(tmp_path)
    assert not agent_has_job_charter(None)

    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "title": "QA Bot",
                "mission": "Triage bugs",
                "playbook": "Ack bugs, create todos, investigate.",
                "updated_at": 123.0,
            }
        ),
        encoding="utf-8",
    )
    assert agent_has_job_charter(tmp_path)


def test_channel_dispatch_requires_execution_job_or_task_intent(tmp_path: Path):
    assert not channel_dispatch_requires_execution(agent_dir=None, triage_intent="CHAT_NOTHINK")
    assert channel_dispatch_requires_execution(agent_dir=None, triage_intent="TASK_THINK")

    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps({"title": "QA Bot", "mission": "Triage", "updated_at": 1.0}),
        encoding="utf-8",
    )
    assert channel_dispatch_requires_execution(agent_dir=tmp_path, triage_intent="CHAT_NOTHINK")


def test_apply_channel_loop_policy_forces_executing_with_task_triage():
    state = LoopState(
        user_input="casual ping",
        orchestration_profile="conversational",
        active_mode=AgentMode.CHAT,
    )
    profile = apply_channel_loop_policy(
        state,
        user_input="Hey @bot",
        dispatch_source="user:channel",
        profile="conversational",
        agent_dir=None,
        triage_intent="TASK_THINK",
    )
    assert profile == "solo_structured"
    assert state.orchestration_profile == "solo_structured"
    assert state.active_mode == AgentMode.EXECUTING
    assert state.goals


def test_apply_channel_loop_policy_respects_job_charter(tmp_path: Path):
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "title": "QA Bot",
                "mission": "Triage bugs",
                "default_profile": "solo_structured",
                "updated_at": 1.0,
            }
        ),
        encoding="utf-8",
    )
    state = LoopState(
        orchestration_profile="conversational",
        active_mode=AgentMode.CHAT,
    )
    profile = apply_channel_loop_policy(
        state,
        user_input="Hey @bot",
        dispatch_source="user:channel",
        profile="conversational",
        agent_dir=tmp_path,
        triage_intent="CHAT_NOTHINK",
    )
    assert profile == "solo_structured"
    assert state.active_mode == AgentMode.EXECUTING


def test_apply_channel_loop_policy_leaves_casual_channel_without_job():
    state = LoopState(
        orchestration_profile="conversational",
        active_mode=AgentMode.CHAT,
    )
    profile = apply_channel_loop_policy(
        state,
        user_input="Hey @bot",
        dispatch_source="user:channel",
        profile="conversational",
        agent_dir=None,
        triage_intent="CHAT_NOTHINK",
    )
    assert profile == "conversational"
    assert state.active_mode == AgentMode.CHAT


def test_home_chat_dispatch_untouched():
    state = LoopState(
        orchestration_profile="conversational",
        active_mode=AgentMode.CHAT,
    )
    profile = apply_channel_loop_policy(
        state,
        user_input="what was the last message?",
        dispatch_source="user",
        profile="conversational",
        agent_dir=None,
    )
    assert profile == "conversational"
    assert state.active_mode == AgentMode.CHAT
