"""Tests for request_restart skill discovery across data/skills and workspace."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nls.skills_setup_policy import (
    iter_skill_package_dirs,
    pick_skill_package_candidate,
    resolve_skill_scan_roots,
)
from nls.tools.agent_tools.request_restart import RequestRestartTool


def test_iter_skill_package_dirs_workspace_and_nested_skills(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    top = ws / "discord-channel"
    top.mkdir()
    (top / "__init__.py").write_text("x=1\n", encoding="utf-8")

    nested_root = ws / "skills"
    nested_root.mkdir()
    nested = nested_root / "telegram-channel"
    nested.mkdir()
    (nested / "__init__.py").write_text("x=1\n", encoding="utf-8")

    names = {p.name for p in iter_skill_package_dirs(ws)}
    assert names == {"discord-channel", "telegram-channel"}


def test_pick_skill_package_prefers_newer_workspace_copy(tmp_path: Path):
    data_pkg = tmp_path / "data" / "discord-channel"
    ws_pkg = tmp_path / "workspace" / "discord-channel"
    data_pkg.mkdir(parents=True)
    ws_pkg.mkdir(parents=True)
    data_init = data_pkg / "__init__.py"
    ws_init = ws_pkg / "__init__.py"
    data_init.write_text("old\n", encoding="utf-8")
    time.sleep(0.02)
    ws_init.write_text("new\n", encoding="utf-8")

    picked = pick_skill_package_candidate(
        "discord-channel",
        [(data_pkg, "data"), (ws_pkg, "workspace")],
    )
    assert picked == (ws_pkg, "workspace_newer")


def test_scan_new_skills_finds_workspace_only_package(tmp_path: Path):
    data_dir = tmp_path / "data"
    skills_dir = data_dir / "skills"
    skills_dir.mkdir(parents=True)
    ws = tmp_path / "workspace"
    ws.mkdir()
    pkg = ws / "discord-channel"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from nls.skills import SkillMeta\nmeta = SkillMeta(name='discord-channel')\n"
        "def register(app, ctx): pass\n",
        encoding="utf-8",
    )

    tool = RequestRestartTool(
        data_dir=str(data_dir),
        agent_id="agent-1",
        workspace=str(ws),
    )
    found = tool._scan_new_skills(data_dir, skills_dir)
    assert len(found) == 1
    assert found[0]["name"] == "discord-channel"
    assert found[0]["scan_source"] == "workspace"


@pytest.mark.asyncio
async def test_stage_workspace_skills_copies_into_data_skills(tmp_path: Path):
    data_dir = tmp_path / "data"
    skills_dir = data_dir / "skills"
    skills_dir.mkdir(parents=True)
    ws = tmp_path / "workspace"
    ws.mkdir()
    pkg = ws / "discord-channel"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    (pkg / "adapter.py").write_text("# updated\n", encoding="utf-8")

    tool = RequestRestartTool(
        data_dir=str(data_dir),
        agent_id="agent-1",
        workspace=str(ws),
    )
    new_skills = [{
        "name": "discord-channel",
        "scan_source": "workspace",
        "scan_path": str(pkg),
        "files": [],
    }]
    await tool._stage_workspace_skills(skills_dir, new_skills)
    dest = skills_dir / "discord-channel"
    assert (dest / "__init__.py").is_file()
    assert (dest / "adapter.py").is_file()
    assert new_skills[0]["scan_source"] == "data"


def test_resolve_skill_scan_roots_deduplicates_data_paths(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "data"
    (data_root / "skills").mkdir(parents=True)
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("NLS_DATA_DIR", str(data_root))
    roots = resolve_skill_scan_roots(
        data_dir=data_root,
        workspace=ws,
    )
    labels = [label for _, label in roots]
    assert labels.count("data") == 1
    assert "workspace" in labels
