"""Regression: channel API hint vars must not leak NameError from _run_command."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nls.tools.agent_tools.bash import BashTool


@pytest.mark.asyncio
async def test_bash_runs_without_channel_hint_name_error(tmp_path: Path):
    agent_dir = tmp_path / "agents" / "agent-x"
    agent_dir.mkdir(parents=True)
    tool = BashTool(str(agent_dir))
    tool._workspace_root = str(agent_dir)

    result = await tool.execute({"command": "echo hello"})

    assert result.is_error is False
    assert "hello" in (result.content or "")
    assert "not defined" not in (result.content or "").lower()


@pytest.mark.asyncio
async def test_bash_appends_channel_hint_in_execute_scope(tmp_path: Path):
    data_root = tmp_path / "data"
    agent_id = "agent-y"
    agent_dir = data_root / "agents" / agent_id
    agent_dir.mkdir(parents=True)
    cfg = data_root / "skills" / "discord-channel" / "agents" / f"{agent_id}.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"enabled": True, "bot_token": "secret"}), encoding="utf-8")

    tool = BashTool(str(agent_dir))
    tool._workspace_root = str(agent_dir)

    result = await tool.execute(
        {"command": "curl -s https://discord.com/api/v10/gateway"},
    )

    assert "not defined" not in (result.content or "").lower()
    assert "CHANNEL HINT" in (result.content or "")
    assert result.details.get("channel_api_nudge") == "discord"


def test_bash_isolated_env_sets_python_utf8_on_windows(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("nls.tools.agent_tools.bash.sys.platform", "win32")
    agent_dir = tmp_path / "agents" / "agent-z"
    agent_dir.mkdir(parents=True)
    tool = BashTool(str(agent_dir))
    env = tool._build_isolated_env(str(agent_dir))
    assert env.get("PYTHONUTF8") == "1"
    assert env.get("PYTHONIOENCODING") == "utf-8"
