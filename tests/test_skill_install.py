"""Tests for skill_install tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nls.tools.agent_tools.skill_install import (
    SkillInstallTool,
    _copy_skill_tree,
    _detect_skill_format,
    _resolve_workspace_path,
    _validate_skill_name,
)


def test_validate_skill_name():
    assert _validate_skill_name("discord-channel") is None
    assert _validate_skill_name("Bad Name") is not None


def test_detect_skill_format(tmp_path: Path):
    d = tmp_path / "my-skill"
    d.mkdir()
    assert _detect_skill_format(d) is None
    (d / "__init__.py").write_text("meta = None\n", encoding="utf-8")
    assert _detect_skill_format(d) == "native"


def test_resolve_workspace_path(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    pkg = ws / "discord-channel"
    pkg.mkdir()
    resolved, err = _resolve_workspace_path(ws, "discord-channel")
    assert err is None
    assert resolved == pkg.resolve()


def test_copy_skill_tree_skips_pycache(tmp_path: Path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    cache = src / "__pycache__"
    cache.mkdir()
    (cache / "mod.pyc").write_bytes(b"\x00")
    _copy_skill_tree(src, dest)
    assert (dest / "__init__.py").is_file()
    assert not (dest / "__pycache__").exists()


@pytest.mark.asyncio
async def test_skill_install_copies_and_enables(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    pkg = ws / "discord-channel"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from nls.skills import SkillMeta\nmeta = SkillMeta(name='discord-channel')\n"
        "def register(app, ctx): pass\n",
        encoding="utf-8",
    )

    data_dir = tmp_path / "data"
    loaded = MagicMock()
    loaded.status = "loaded"
    loaded.meta = MagicMock(config_schema=[{"key": "token"}])
    loaded.error = None
    loaded.context = MagicMock(
        bridges=[],
        startup_hooks=[],
    )

    sl = MagicMock()
    sl.reload_skill = AsyncMock(return_value=loaded)

    runtime = MagicMock()
    runtime.enable_skill = MagicMock()

    tool = SkillInstallTool(
        workspace=str(ws),
        data_dir=str(data_dir),
        agent_id="agent-1",
    )

    with patch.object(tool, "_get_skill_loader", return_value=sl), patch.object(
        tool, "_get_runtime", return_value=runtime,
    ), patch(
        "nls.tools.agent_tools.skill_install._broadcast_skill_installed",
        new=AsyncMock(),
    ):
        result = await tool.execute({"source_path": "discord-channel"})

    assert not result.is_error, result.content
    assert (data_dir / "skills" / "discord-channel" / "__init__.py").is_file()
    sl.reload_skill.assert_awaited_once_with("discord-channel")
    runtime.enable_skill.assert_called_once_with("discord-channel")
    assert "skill_configure" in result.content
    assert "skill_name=" in result.content


@pytest.mark.asyncio
async def test_skill_install_replaces_stale_files(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    pkg = ws / "discord-channel"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("x = 2\n", encoding="utf-8")

    data_dir = tmp_path / "data"
    dest = data_dir / "skills" / "discord-channel"
    dest.mkdir(parents=True)
    (dest / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (dest / "old_module.py").write_text("stale = True\n", encoding="utf-8")
    bridge = dest / "bridge-data"
    bridge.mkdir()
    (bridge / "auth.json").write_text('{"ok":true}', encoding="utf-8")

    loaded = MagicMock()
    loaded.status = "loaded"
    loaded.meta = MagicMock(config_schema=None)
    loaded.context = MagicMock(bridges=[], startup_hooks=[])

    sl = MagicMock()
    sl.reload_skill = AsyncMock(return_value=loaded)

    tool = SkillInstallTool(
        workspace=str(ws),
        data_dir=str(data_dir),
        agent_id="agent-1",
    )

    with patch.object(tool, "_get_skill_loader", return_value=sl), patch.object(
        tool, "_get_runtime", return_value=MagicMock(enable_skill=MagicMock()),
    ), patch(
        "nls.tools.agent_tools.skill_install._broadcast_skill_installed",
        new=AsyncMock(),
    ):
        result = await tool.execute({"source_path": "discord-channel"})

    assert not result.is_error
    assert (dest / "__init__.py").read_text(encoding="utf-8") == "x = 2\n"
    assert not (dest / "old_module.py").exists()
    assert (dest / "bridge-data" / "auth.json").read_text(encoding="utf-8") == '{"ok":true}'


@pytest.mark.asyncio
async def test_skill_install_rejects_outside_workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "__init__.py").write_text("x=1\n", encoding="utf-8")

    tool = SkillInstallTool(
        workspace=str(ws),
        data_dir=str(tmp_path / "data"),
        agent_id="agent-1",
    )
    result = await tool.execute({"source_path": str(outside)})
    assert result.is_error
