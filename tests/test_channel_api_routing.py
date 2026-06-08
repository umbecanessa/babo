"""Tests for channel-agnostic REST API routing hints."""

from __future__ import annotations

import json
from pathlib import Path

from nls.runtime.channel_api_routing import (
    command_matches_channel_rest,
    detect_configured_channel_rest_in_command,
    discover_agent_channel_keys,
    format_channel_rest_bash_hint,
)
from nls.runtime.channel_agent_config import load_agent_channel_config


def _write_agent_channel_cfg(
    data_root: Path,
    agent_id: str,
    channel: str,
    cfg: dict,
) -> Path:
    skill_dir = f"{channel}-channel"
    path = data_root / "skills" / skill_dir / "agents" / f"{agent_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_discover_bundled_and_custom_channels(tmp_path: Path):
    data_root = tmp_path / "data"
    agent_id = "agent-1"
    _write_agent_channel_cfg(
        data_root, agent_id, "discord",
        {"enabled": True, "bot_token": "secret"},
    )
    _write_agent_channel_cfg(
        data_root, agent_id, "matrix",
        {"enabled": True, "api_token": "mx", "rest_api_hosts": ["matrix.example.com"]},
    )

    keys = discover_agent_channel_keys(data_root, agent_id)
    assert "discord" in keys
    assert "matrix" in keys


def test_custom_channel_rest_host_from_config(tmp_path: Path):
    data_root = tmp_path / "data"
    agent_id = "agent-2"
    _write_agent_channel_cfg(
        data_root, agent_id, "acme",
        {
            "enabled": True,
            "api_key": "k",
            "rest_api_hosts": ["api.acme.chat/v1"],
        },
    )
    cfg = load_agent_channel_config(data_root, agent_id, "acme")
    assert command_matches_channel_rest(
        "curl https://api.acme.chat/v1/channels",
        "acme",
        cfg,
    )


def test_detect_configured_channel_rest_in_command(tmp_path: Path):
    data_root = tmp_path / "data"
    agent_id = "agent-3"
    agent_dir = data_root / "agents" / agent_id
    agent_dir.mkdir(parents=True)
    _write_agent_channel_cfg(
        data_root, agent_id, "slack",
        {"enabled": True, "bot_token": "xoxb-test"},
    )

    channel = detect_configured_channel_rest_in_command(
        "curl https://slack.com/api/conversations.list",
        str(agent_dir),
    )
    assert channel == "slack"


def test_format_hint_is_channel_agnostic():
    hint = format_channel_rest_bash_hint("matrix")
    assert "channel_manage(channel='matrix'" in hint
    assert "discord.com" not in hint
