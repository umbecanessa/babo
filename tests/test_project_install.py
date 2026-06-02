"""Tests for project_runtime and project_install."""

from __future__ import annotations

from pathlib import Path

import pytest

from nls.tools.agent_tools.project_runtime import (
    detect_ecosystem,
    detect_node_package_manager,
    find_package_json,
    find_requirements_file,
    looks_like_pypi_package_spec,
    parse_pip_requirements_ref,
    resolve_node_install_dir,
    resolve_project_root,
    split_pip_package_args,
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

    # Multiple app folders → workspace is the monorepo root (use plan project_dir to narrow).
    assert resolve_project_root(str(ws), str(ws)) == str(ws)


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


def test_resolve_project_root_plan_dir_when_cwd_in_backend(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monorepo = ws / "ICF-BABOBENCH"
    monorepo.mkdir()
    (monorepo / "requirements.txt").write_text("fastapi==0.111.0\n", encoding="utf-8")
    backend = monorepo / "backend"
    backend.mkdir()
    (backend / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    assert resolve_project_root(
        str(backend), str(ws), plan_project_dir="ICF-BABOBENCH",
    ) == str(monorepo)


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


def test_node_install_dir_from_cwd_not_heuristic_name(tmp_path: Path):
    root = tmp_path / "app"
    panel = root / "panel"
    panel.mkdir(parents=True)
    (panel / "package.json").write_text("{}", encoding="utf-8")
    (root / "home").mkdir()
    (root / "home" / "package.json").write_text("{}", encoding="utf-8")

    node_dir, err = resolve_node_install_dir(str(root), str(panel))
    assert err is None
    assert node_dir == panel

    node_dir2, err2 = resolve_node_install_dir(
        str(root), str(root), install_dir="home",
    )
    assert err2 is None
    assert node_dir2 == root / "home"


def test_node_install_ambiguous_without_install_dir(tmp_path: Path):
    root = tmp_path / "app"
    root.mkdir()
    for name in ("panel", "home"):
        d = root / name
        d.mkdir()
        (d / "package.json").write_text("{}", encoding="utf-8")

    node_dir, err = resolve_node_install_dir(str(root), str(root))
    assert node_dir is None
    assert err is not None
    assert "install_dir" in err
    assert "panel" in err
    assert "home" in err


@pytest.mark.asyncio
async def test_project_install_node_respects_install_dir(tmp_path: Path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "package.json").write_text('{"name":"app","private":true}', encoding="utf-8")
    api = root / "api"
    api.mkdir(parents=True)
    (api / "package.json").write_text(
        '{"name":"api","version":"1.0.0"}', encoding="utf-8",
    )

    from nls.tools.agent_tools.project_install import ProjectInstallTool

    tool = ProjectInstallTool(str(root))
    result = await tool.execute({
        "package": "left-pad",
        "ecosystem": "node",
        "install_dir": "api",
    })
    assert not result.is_error, result.content
    assert "api" in result.content.replace("\\", "/")


def test_looks_like_pypi_package_spec():
    assert looks_like_pypi_package_spec("SQLAlchemy psycopg2-binary")
    assert looks_like_pypi_package_spec("fastapi")
    assert looks_like_pypi_package_spec("asyncpg")
    assert not looks_like_pypi_package_spec("axios")
    assert not looks_like_pypi_package_spec("@scope/pkg")


def test_split_pip_package_args():
    assert split_pip_package_args("SQLAlchemy psycopg2-binary") == [
        "SQLAlchemy", "psycopg2-binary",
    ]


def test_detect_ecosystem_from_package_name_in_monorepo(tmp_path: Path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    backend = root / "backend" / "src"
    backend.mkdir(parents=True)
    (backend / "database.py").write_text("x = 1\n", encoding="utf-8")

    assert detect_ecosystem(
        str(root),
        cwd=str(backend),
        package="SQLAlchemy psycopg2-binary",
    ) == "python"


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
async def test_project_install_backend_requirements_from_backend_cwd(tmp_path: Path):
    pytest.importorskip("pip")
    ws = tmp_path / "workspace"
    ws.mkdir()
    root = ws / "app"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "requirements.txt").write_text("six\n", encoding="utf-8")

    from nls.tools.agent_tools.project_install import ProjectInstallTool

    tool = ProjectInstallTool(str(ws))
    tool.set_plan_project_dir_fn(lambda: "app")
    tool._shared_cwd = str(root / "backend")  # noqa: SLF001 — simulate bash CWD drift

    result = await tool.execute({
        "ecosystem": "python",
        "requirements_file": "backend/requirements.txt",
    })
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
