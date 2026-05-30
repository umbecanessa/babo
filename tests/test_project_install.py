"""Tests for project_runtime and project_install."""

from __future__ import annotations

from pathlib import Path

import pytest

from nls.tools.agent_tools.project_runtime import (
    detect_ecosystem,
    detect_node_package_manager,
    find_package_json,
    find_requirements_file,
    parse_pip_requirements_ref,
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


def test_resolve_project_root_finds_single_child_under_workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "icf-app"
    proj.mkdir()
    (proj / "package.json").write_text("{}", encoding="utf-8")

    assert resolve_project_root(str(ws), str(ws)) == str(proj)


def test_resolve_project_root_ambiguous_when_multiple_children(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    for name in ("app-a", "app-b"):
        p = ws / name
        p.mkdir()
        (p / "package.json").write_text("{}", encoding="utf-8")

    assert resolve_project_root(str(ws), str(ws)) is None


def test_resolve_project_root_uses_plan_project_dir(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    for name in ("app-a", "app-b"):
        p = ws / name
        p.mkdir()
        (p / "package.json").write_text("{}", encoding="utf-8")

    assert resolve_project_root(
        str(ws), str(ws), plan_project_dir="app-b",
    ) == str(ws / "app-b")


def test_resolve_project_root_monorepo_backend_requirements(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "backend").mkdir()
    (ws / "backend" / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    assert resolve_project_root(str(ws), str(ws)) == str(ws)


def test_resolve_project_root_plan_dir_before_scaffold_markers(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    planned = ws / "icf-gemini-platform"
    assert resolve_project_root(
        str(ws), str(ws), plan_project_dir="icf-gemini-platform",
    ) == str(planned)


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


def test_detect_ecosystem_monorepo_backend_requirements(tmp_path: Path):
    root = tmp_path / "app"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    assert detect_ecosystem(str(root)) == "python"
    assert find_requirements_file(str(root)) == root / "backend" / "requirements.txt"
    assert find_package_json(str(root)) == root / "frontend" / "package.json"


def test_parse_pip_requirements_ref():
    assert parse_pip_requirements_ref("-r backend/requirements.txt") == "backend/requirements.txt"
    assert parse_pip_requirements_ref("--requirement backend/requirements.txt") == "backend/requirements.txt"
    assert parse_pip_requirements_ref("fastapi") is None


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


@pytest.mark.asyncio
async def test_project_install_python_from_requirements_txt(tmp_path: Path):
    pytest.importorskip("pip")
    root = tmp_path / "app"
    root.mkdir()
    (root / "requirements.txt").write_text("six\n", encoding="utf-8")

    from nls.tools.agent_tools.project_install import ProjectInstallTool

    tool = ProjectInstallTool(str(root))
    result = await tool.execute({"ecosystem": "python"})
    assert not result.is_error, result.content
    assert "requirements.txt" in result.content.lower()
    assert ".venv" in result.content


@pytest.mark.asyncio
async def test_project_install_python_from_backend_requirements(tmp_path: Path):
    pytest.importorskip("pip")
    root = tmp_path / "app"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "requirements.txt").write_text("six\n", encoding="utf-8")

    from nls.tools.agent_tools.project_install import ProjectInstallTool

    tool = ProjectInstallTool(str(root))
    result = await tool.execute({"ecosystem": "python"})
    assert not result.is_error, result.content
    assert "backend/requirements.txt" in result.content


@pytest.mark.asyncio
async def test_project_install_accepts_r_flag_in_package(tmp_path: Path):
    pytest.importorskip("pip")
    root = tmp_path / "app"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "requirements.txt").write_text("six\n", encoding="utf-8")

    from nls.tools.agent_tools.project_install import ProjectInstallTool

    tool = ProjectInstallTool(str(root))
    result = await tool.execute({
        "ecosystem": "python",
        "package": "-r backend/requirements.txt",
    })
    assert not result.is_error, result.content
    assert "backend/requirements.txt" in result.content
