"""Tests for install_dir / venv root resolution."""

from __future__ import annotations

from pathlib import Path

from nls.tools.agent_tools.project_runtime import resolve_venv_project_root


def test_resolve_venv_project_root_avoids_double_nest(tmp_path: Path):
    workspace = tmp_path / "ws"
    project = workspace / "discord_reorganize"
    project.mkdir(parents=True)
    (project / "requirements.txt").write_text("requests\n", encoding="utf-8")

    venv_root = resolve_venv_project_root(
        str(project),
        install_dir="discord_reorganize",
        cwd=str(project),
    )
    assert Path(venv_root) == project


def test_resolve_venv_project_root_honors_install_dir_under_monorepo(tmp_path: Path):
    workspace = tmp_path / "ws"
    backend = workspace / "backend"
    backend.mkdir(parents=True)
    (backend / "requirements.txt").write_text("flask\n", encoding="utf-8")

    venv_root = resolve_venv_project_root(
        str(workspace),
        install_dir="backend",
        cwd=str(workspace),
    )
    assert Path(venv_root) == backend
