"""Tests for PTY command wrapping and agent session keys."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.services.agent_pty_pool import AgentPtyPool, reset_agent_pty_pool
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
    assert "$__nls_code" in wrapped or "LASTEXITCODE" in wrapped


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
    with patch("server.services.pty_session.platform.system", return_value="Linux"):
        script = build_env_export_script([("PATH", "/venv/bin:/usr/bin")])
        assert "export PATH=" in script
        assert "/venv/bin" in script


def test_build_env_export_script_windows():
    with patch("server.services.pty_session.platform.system", return_value="Windows"):
        script = build_env_export_script([("PATH", "C:\\venv\\Scripts")])
        assert "$env:PATH =" in script
        assert "C:\\venv\\Scripts" in script


@pytest.mark.asyncio
async def test_park_long_running_keeps_busy_shell_for_mirror():
    reset_agent_pty_pool()
    pool = AgentPtyPool()

    busy = MagicMock()
    busy.cwd = "/tmp/ws"
    busy.shell_pid = 111
    busy.write = AsyncMock()
    busy.close = AsyncMock()
    busy.sync_env = AsyncMock()
    busy.set_cwd = AsyncMock()
    busy.start = AsyncMock()

    fresh = MagicMock()
    fresh.cwd = "/tmp/ws"
    fresh.shell_pid = 222
    fresh.write = AsyncMock()
    fresh.close = AsyncMock()
    fresh.sync_env = AsyncMock()
    fresh.set_cwd = AsyncMock()
    fresh.start = AsyncMock()

    with patch(
        "server.services.agent_pty_pool.normalize_pty_workspace",
        return_value="/tmp/ws",
    ):
        key = pool.session_key("agent-a", "/tmp/ws")
        pool._sessions[key] = busy
        pool._mirror[key] = busy

        with patch("server.services.agent_pty_pool.PtySession", return_value=fresh):
            out = await pool.park_long_running_and_spawn_fresh(
                agent_id="agent-a",
                workspace="/tmp/ws",
                env={"PATH": "/usr/bin"},
                cwd="/tmp/ws",
            )

        assert out is fresh
        assert pool.get_existing("agent-a", "/tmp/ws") is busy
        assert pool._sessions[key] is fresh
        busy.write.assert_awaited()
        fresh.start.assert_awaited()

        cmd = await pool.get_session(
            agent_id="agent-a",
            workspace="/tmp/ws",
            env={"PATH": "/usr/bin"},
            cwd="/tmp/ws",
        )
        assert cmd is fresh
