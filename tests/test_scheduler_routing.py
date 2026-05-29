"""Tests for shared scheduler agent_message routing."""

from __future__ import annotations

from nls.tools.agent_tools.scheduler import (
    ScheduledJob,
    SchedulerManager,
    parse_agent_message_target,
    tag_agent_message,
)


def test_tag_agent_message_adds_routing_prefix():
    tagged = tag_agent_message("agent-123", "SCHEDULER_OK", owner="agent")
    assert tagged.startswith("[AGENT_MSG|agent_id=agent-123|")
    assert tagged.endswith("SCHEDULER_OK")


def test_tag_agent_message_idempotent():
    tagged = tag_agent_message("agent-123", "SCHEDULER_OK")
    again = tag_agent_message("other", tagged)
    assert again == tagged


def test_parse_agent_message_target():
    msg = tag_agent_message("abc-def", "hello")
    assert parse_agent_message_target(msg) == "abc-def"
    assert parse_agent_message_target("plain message") is None


def test_scheduled_job_persists_owner_agent_id(tmp_path):
    mgr = SchedulerManager(str(tmp_path))
    mgr.add_job(ScheduledJob(
        name="job1",
        schedule_type="once",
        run_at=9999999999.0,
        action="agent_message",
        action_message="ping",
        owner_agent_id="agent-xyz",
    ))
    mgr2 = SchedulerManager(str(tmp_path))
    assert mgr2.jobs["job1"].owner_agent_id == "agent-xyz"
