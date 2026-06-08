"""Tests for soft channel API bash hints (not hard blocks)."""

from __future__ import annotations

import json
from pathlib import Path

from nls.tools.agent_tools.shell_hints import configured_channel_api_bash_hint


def test_hints_discord_api_curl_when_configured(tmp_path: Path):
    data_root = tmp_path / "data"
    agent_id = "agent-123"
    agent_dir = data_root / "agents" / agent_id
    agent_dir.mkdir(parents=True)
    cfg_path = (
        data_root / "skills" / "discord-channel" / "agents" / f"{agent_id}.json"
    )
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        json.dumps({"enabled": True, "bot_token": "secret"}),
        encoding="utf-8",
    )

    msg = configured_channel_api_bash_hint(
        "curl -H 'Authorization: Bot x' https://discord.com/api/v10/guilds/1",
        str(agent_dir),
    )
    assert msg is not None
    assert "channel_manage" in msg
    assert not msg.startswith("Error:")


def test_no_hint_discord_api_curl_when_not_configured(tmp_path: Path):
    data_root = tmp_path / "data"
    agent_id = "agent-456"
    agent_dir = data_root / "agents" / agent_id
    agent_dir.mkdir(parents=True)

    msg = configured_channel_api_bash_hint(
        "curl https://discord.com/api/v10/gateway",
        str(agent_dir),
    )
    assert msg is None
