"""Tests for bash git-init workspace guard."""

from __future__ import annotations

from nls.tools.agent_tools.bash import BashTool


def test_git_init_blocked_at_workspace_root(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    tool = BashTool(cwd=str(ws))
    assert tool._is_git_init_at_workspace_root("git init")


def test_git_init_allowed_after_cd_into_subfolder(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "gh-smoke-test").mkdir()
    tool = BashTool(cwd=str(ws))
    assert not tool._is_git_init_at_workspace_root(
        "cd gh-smoke-test && git init && git add README.md"
    )
