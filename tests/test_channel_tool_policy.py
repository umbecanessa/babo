"""Tests for Discord channel tool guidance (plan A vs plan B)."""

from __future__ import annotations

from nls.agentic.channel_tool_policy import (
    discord_channel_primary_guidance,
    skill_discovery_prompt,
    uses_discord_bot_credential_escalation,
)


def test_primary_guidance_when_discord_admin_tools_unlocked():
    text = discord_channel_primary_guidance({"squad", "channel_manage"})
    assert text is not None
    assert "plan a" in text.lower()
    assert "plan b" in text.lower()
    assert "check_channel_readiness" in text


def test_no_primary_guidance_without_discord_tools():
    assert discord_channel_primary_guidance({"bash", "read"}) is None


def test_skill_discovery_surfaces_plan_a_only():
    text = skill_discovery_prompt({"squad", "discord_send"})
    assert "check_channel_readiness" in text
    assert "plan A" in text
    assert "plan B" not in text
    assert "Project.Credential" not in text


def test_skill_discovery_allows_wm_borrow_without_discord_tools():
    text = skill_discovery_prompt({"bash"})
    assert "Project.Credential" in text


def test_detects_bot_token_in_write_for_telemetry():
    token = "MTk4NjIyNDY0NDU2OTQ1Mzg4.ClFz7X.ZRmBn7aWDm6OvUfe8x1Q7j4"
    assert uses_discord_bot_credential_escalation("write", {"content": token})


def test_bash_discord_api_without_credentials_not_flagged():
    assert not uses_discord_bot_credential_escalation(
        "bash",
        {"command": "curl -s https://discord.com/api/v10/gateway"},
    )
