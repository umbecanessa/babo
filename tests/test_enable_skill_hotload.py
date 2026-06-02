"""enable_skill must persist string skill names (ClawHub hot-load)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nls.tools.skill_manager import get_enabled_skills


def test_enable_skill_persists_string_name(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    rt = MagicMock()
    rt.agent_dir = agent_dir
    rt.agent_id = "test-agent"
    rt.refresh_tools = MagicMock()
    rt._populate_skills_ring = MagicMock()
    rt._populate_channels_ring = MagicMock()

    from nls.runtime.agent_runtime import AgentRuntime

    AgentRuntime.enable_skill(rt, "discord-admin")

    assert "discord-admin" in get_enabled_skills(agent_dir)
    rt.refresh_tools.assert_called_once()


def test_looks_like_native_skill_discord_channel():
    from nls.skills_setup_policy import looks_like_native_skill_authoring

    assert looks_like_native_skill_authoring(
        "Build a live discord-channel skill like telegram with gateway listener"
    )
