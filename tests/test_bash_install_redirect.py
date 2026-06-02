"""Tests for bash pip/npm auto-routing to install tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nls.tools.agent_tools.base import ToolResult
from nls.tools.agent_tools.bash import BashTool


@pytest.mark.asyncio
async def test_pip_in_project_routes_to_project_install(tmp_path: Path):
    proj = tmp_path / "myapp"
    proj.mkdir()
    (proj / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    bash = BashTool(str(proj))
    pi = MagicMock()
    pi.execute = AsyncMock(
        return_value=ToolResult(content="Installed fastapi", is_error=False),
    )
    bash.set_install_tools(project_install=pi, server_install=MagicMock())

    result = await bash.execute({"command": "pip install fastapi"})
    assert not result.is_error
    assert "project_install" in result.content
    pi.execute.assert_awaited_once()
    assert pi.execute.call_args[0][0].get("package") == "fastapi"


@pytest.mark.asyncio
async def test_pip_outside_project_routes_to_server_install(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()

    bash = BashTool(str(ws))
    si = MagicMock()
    si.execute = AsyncMock(
        return_value=ToolResult(content="Installed requests", is_error=False),
    )
    bash.set_install_tools(project_install=MagicMock(), server_install=si)
    bash.set_plan_blocks_server_install_fn(lambda: False)

    result = await bash.execute({"command": "pip install requests"})
    assert not result.is_error
    assert "server_install" in result.content
    si.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_pip_during_active_plan_routes_to_project_install(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "myapp"
    proj.mkdir()
    (proj / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    bash = BashTool(str(ws))
    pi = MagicMock()
    pi.execute = AsyncMock(
        return_value=ToolResult(content="Installed fastapi", is_error=False),
    )
    bash.set_install_tools(project_install=pi, server_install=MagicMock())
    bash.set_plan_blocks_server_install_fn(lambda: True)
    bash.set_plan_project_dir_fn(lambda: "myapp")

    result = await bash.execute({"command": "pip install fastapi"})
    assert not result.is_error
    assert "project_install" in result.content
    pi.execute.assert_awaited_once()
    assert pi.execute.call_args[0][0].get("package") == "fastapi"


@pytest.mark.asyncio
async def test_npm_in_project_routes_to_project_install(tmp_path: Path):
    proj = tmp_path / "webapp"
    proj.mkdir()
    (proj / "package.json").write_text("{}", encoding="utf-8")

    bash = BashTool(str(proj))
    pi = MagicMock()
    pi.execute = AsyncMock(
        return_value=ToolResult(content="npm install ok", is_error=False),
    )
    bash.set_install_tools(project_install=pi, server_install=MagicMock())

    result = await bash.execute({"command": "npm install"})
    assert not result.is_error
    assert "project_install" in result.content
    pi.execute.assert_awaited_once()
