"""Channel connection awareness for agent context and triage."""

from __future__ import annotations

import json
from pathlib import Path

from nls.runtime.agent_runtime import AgentRuntime


def _make_runtime(data_root: Path, agent_id: str) -> AgentRuntime:
    agent_dir = data_root / "agents" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    rt = AgentRuntime.__new__(AgentRuntime)
    rt.agent_id = agent_id
    rt.agent_dir = str(agent_dir)
    return rt


def test_discord_connected_from_agent_config(tmp_path: Path):
    agent_id = "agent-discord-1"
    rt = _make_runtime(tmp_path, agent_id)
    cfg_path = (
        tmp_path / "skills" / "discord-channel" / "agents" / f"{agent_id}.json"
    )
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "bot_token": "test-token",
                "scoped_channels": {
                    "guilds": {"1": {"name": "Babo"}},
                    "channels": {
                        "2": {
                            "name": "bug-reports",
                            "effective_enabled": True,
                        },
                        "3": {
                            "name": "help",
                            "effective_enabled": True,
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    assert rt._channel_is_connected("discord") is True
    summary = rt._discord_config_summary(rt._load_channel_agent_config("discord") or {})
    assert "Babo" in summary
    assert "bug-reports" in summary

    triage_block = rt._channel_status_for_triage()
    assert "discord: CONNECTED" in triage_block
    assert "bug-reports" in triage_block
    assert "do not ask" in triage_block.lower()


def test_discord_not_connected_without_token(tmp_path: Path):
    agent_id = "agent-discord-2"
    rt = _make_runtime(tmp_path, agent_id)
    cfg_path = (
        tmp_path / "skills" / "discord-channel" / "agents" / f"{agent_id}.json"
    )
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps({"enabled": True}),
        encoding="utf-8",
    )

    assert rt._channel_is_connected("discord") is False
    assert rt._channel_status_for_triage() == ""


def test_global_discord_config_does_not_leak_to_other_agents(tmp_path: Path):
    """Legacy config.json must not mark unrelated agents as connected."""
    agent_id = "fresh-agent-no-config"
    rt = _make_runtime(tmp_path, agent_id)
    global_cfg = tmp_path / "skills" / "discord-channel" / "config.json"
    global_cfg.parent.mkdir(parents=True, exist_ok=True)
    global_cfg.write_text(
        json.dumps({"enabled": True, "bot_token": "shared-global-token"}),
        encoding="utf-8",
    )

    assert rt._load_channel_agent_config("discord") is None
    assert rt._channel_is_connected("discord") is False


def test_merge_global_does_not_leak_credentials():
    from nls.runtime.channel_agent_config import merge_global_and_agent_channel_config

    merged = merge_global_and_agent_channel_config(
        {"enabled": True, "bot_token": "global-token", "dm_policy": "disabled"},
        {"owner_identity": "owner1"},
    )
    assert "bot_token" not in merged
    assert merged.get("dm_policy") == "disabled"
    assert merged.get("owner_identity") == "owner1"


def test_read_skill_config_strips_global_credentials_for_channel_skills(tmp_path: Path):
    from server.routes.skills import _read_skill_config_for_agent

    cfg_path = tmp_path / "skills" / "discord-channel" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "bot_token": "shared-global-token",
                "bot_username": "BaboBot",
            }
        ),
        encoding="utf-8",
    )

    config, per_agent = _read_skill_config_for_agent(
        cfg_path, "discord-channel", "new-agent-id",
    )
    assert per_agent is False
    assert "bot_token" not in config
    assert config.get("enabled") is True
    assert config.get("bot_username") == "BaboBot"


def test_contacts_reports_discord_connected_from_per_agent_config(tmp_path: Path):
    from nls.tools.agent_tools.contacts import ContactsTool

    agent_id = "agent-discord-contacts"
    cfg_path = (
        tmp_path / "skills" / "discord-channel" / "agents" / f"{agent_id}.json"
    )
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "bot_token": "test-token",
                "owner_identity": "wasnaga",
            }
        ),
        encoding="utf-8",
    )

    class _FakeDiscordAdapter:
        name = "discord"
        _connected_agents: set[str] = set()

    tool = ContactsTool(agent_id, data_dir=tmp_path)
    assert tool._is_connected(_FakeDiscordAdapter(), "discord") is True


def test_bundled_skill_ring_guidance_connected():
    from nls.skills_setup_policy import bundled_skill_ring_guidance

    headline, guidance = bundled_skill_ring_guidance(
        "discord-channel",
        "Discord integration",
        enabled=True,
        configured=True,
    )
    assert "[connected]" in headline
    assert "do not skill_configure" in guidance.lower()
