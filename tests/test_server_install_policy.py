"""Tests for install_policy and server_install plan gating."""

from __future__ import annotations

import pytest

from nls.tools.agent_tools.install_policy import (
    SERVER_INSTALL_BLOCKED_MSG,
    plan_blocks_server_install,
    should_block_server_install,
)
from nls.tools.agent_tools.server_install import ServerInstallTool


def test_should_block_server_install():
    assert should_block_server_install(True) is True
    assert should_block_server_install(False) is False
    assert should_block_server_install(True, for_agent_runtime=True) is False


def test_plan_blocks_server_install_callback():
    assert plan_blocks_server_install(None) is False
    assert plan_blocks_server_install(lambda: True) is True
    assert plan_blocks_server_install(lambda: False) is False


@pytest.mark.asyncio
async def test_server_install_blocked_during_active_plan():
    tool = ServerInstallTool()
    tool.set_plan_blocks_server_install_fn(lambda: True)
    result = await tool.execute({"package": "fastapi"})
    assert result.is_error
    assert "project_install" in result.content


@pytest.mark.asyncio
async def test_server_install_for_agent_runtime_bypasses_gate():
    assert should_block_server_install(True, for_agent_runtime=True) is False


@pytest.mark.asyncio
async def test_server_install_blocked_message_during_plan():
    tool = ServerInstallTool()
    tool.set_plan_blocks_server_install_fn(lambda: True)
    result = await tool.execute({"package": "requests"})
    assert result.is_error
    assert result.content == SERVER_INSTALL_BLOCKED_MSG
