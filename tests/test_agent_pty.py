"""Tests for PTY command wrapping and agent session keys."""

from __future__ import annotations

from server.services.agent_pty_pool import AgentPtyPool
from server.services.pty_session import (
    MARKER_CWD,
    MARKER_EXIT,
    build_env_export_script,
    parse_cwd_from_output,
    wrap_command_for_pty,
)


def test_wrap_command_includes_exit_and_cwd_sentinels_windows():
    wrapped = wrap_command_for_pty("Get-ChildItem", windows=True)
    assert MARKER_EXIT in wrapped
    assert MARKER_CWD in wrapped
    assert "Get-ChildItem" in wrapped


def test_wrap_command_includes_exit_and_cwd_sentinels_unix():
    wrapped = wrap_command_for_pty("ls -la", windows=False)
    assert MARKER_EXIT in wrapped
    assert MARKER_CWD in wrapped
    assert "ls -la" in wrapped


def test_agent_pty_pool_session_key_normalizes_workspace():
    key = AgentPtyPool.session_key(
        "agent-1",
        r"C:\data\agents\agent-1\workspace",
    )
    assert key.startswith("agent-1:")
    assert "workspace" in key.lower()


def test_parse_cwd_from_output():
    text = f'ok\n{MARKER_CWD}/tmp/project{MARKER_CWD}\n{MARKER_EXIT}0'
    assert parse_cwd_from_output(text) == "/tmp/project"


def test_build_env_export_script_unix():
    from unittest.mock import patch

    with patch("server.services.pty_session.platform.system", return_value="Linux"):
        script = build_env_export_script([("PATH", "/venv/bin:/usr/bin")])
        assert "export PATH=" in script
        assert "/venv/bin" in script


def test_build_env_export_script_windows():
    from unittest.mock import patch

    with patch("server.services.pty_session.platform.system", return_value="Windows"):
        script = build_env_export_script([("PATH", "C:\\venv\\Scripts")])
        assert "$env:PATH =" in script
        assert "C:\\venv\\Scripts" in script
