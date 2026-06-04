"""channel_inspect tool and runtime helpers."""

from __future__ import annotations

import json
from pathlib import Path

from nls.runtime.channel_inspect import inspect_all_channels, inspect_channel


def _write_discord_cfg(tmp_path: Path, agent_id: str) -> None:
    path = tmp_path / "skills" / "discord-channel" / "agents" / f"{agent_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "bot_token": "test-token",
                "owner_identity": "owner-user",
                "dm_policy": "disabled",
                "scoped_channels": {
                    "guilds": {
                        "1": {"id": "1", "name": "Babo"},
                    },
                    "channels": {
                        "10": {
                            "id": "10",
                            "name": "general",
                            "guild_id": "1",
                            "effective_enabled": True,
                            "require_mention": True,
                        },
                        "11": {
                            "id": "11",
                            "name": "bug-reports",
                            "guild_id": "1",
                            "effective_enabled": True,
                            "require_mention": False,
                        },
                        "12": {
                            "id": "12",
                            "name": "archived",
                            "guild_id": "1",
                            "effective_enabled": False,
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_inspect_channel_discord_detail(tmp_path: Path):
    agent_id = "agent-inspect-1"
    _write_discord_cfg(tmp_path, agent_id)

    text = inspect_channel(tmp_path, agent_id, "discord")
    assert "configured: yes" in text
    assert "credentials: bot token saved" in text
    assert "owner_identity: owner-user" in text
    assert "#general" in text
    assert "#bug-reports" in text
    assert "bot_token" not in text.lower() or "masked" in text.lower()


def test_inspect_channel_active_only(tmp_path: Path):
    agent_id = "agent-inspect-2"
    _write_discord_cfg(tmp_path, agent_id)

    text = inspect_channel(tmp_path, agent_id, "discord", active_only=True)
    assert "#general" in text
    assert "#bug-reports" in text
    assert "#archived" not in text


def test_inspect_all_channels_marks_configured(tmp_path: Path):
    agent_id = "agent-inspect-3"
    _write_discord_cfg(tmp_path, agent_id)

    text = inspect_all_channels(tmp_path, agent_id)
    assert "discord: CONFIGURED" in text
    assert "telegram:" in text
