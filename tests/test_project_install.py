"""Tests for project_runtime and project_install."""

from __future__ import annotations

from pathlib import Path

import pytest

from nls.tools.agent_tools.project_runtime import (
    detect_ecosystem,
    detect_node_package_manager,
    resolve_project_root,
)


def test_resolve_project_root_finds_git(tmp_path: Path):
    ws = tmp_path / "workspace"
    proj = ws / "my-app"
    proj.mkdir(parents=True)
    (proj / ".git").mkdir()

    assert resolve_project_root(str(proj), str(ws)) == str(proj)


def test_resolve_project_root_falls_back_to_cwd_when_not_workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "nested" / "pkg"
    proj.mkdir(parents=True)
    (proj / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    assert resolve_project_root(str(proj), str(ws)) == str(proj)


def test_detect_ecosystem_python(tmp_path: Path):
    root = tmp_path / "pyproj"
    root.mkdir()
    (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
    assert detect_ecosystem(str(root)) == "python"


def test_detect_ecosystem_node(tmp_path: Path):
    root = tmp_path / "nodeproj"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    assert detect_ecosystem(str(root)) == "node"


def test_detect_node_package_manager_prefers_pnpm(tmp_path: Path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert detect_node_package_manager(str(root)) == "pnpm"


@pytest.mark.asyncio
async def test_project_install_python_creates_venv_and_installs(tmp_path: Path):
    pytest.importorskip("pip")
    root = tmp_path / "app"
    root.mkdir()
    (root / "requirements.txt").write_text("six\n", encoding="utf-8")

    from nls.tools.agent_tools.project_install import ProjectInstallTool

    tool = ProjectInstallTool(str(root))
    result = await tool.execute({"package": "six", "ecosystem": "python"})
    assert not result.is_error, result.content
    assert ".venv" in result.content
    assert "six" in result.content.lower()

    venv_python = root / ".venv" / (
        "Scripts/python.exe" if __import__("sys").platform == "win32" else "bin/python"
    )
    assert venv_python.exists()
