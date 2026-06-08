"""Guardrails against monorepo dual-.venv confusion."""

from __future__ import annotations

from pathlib import Path

from nls.tools.agent_tools.project_runtime import (
    check_venv_location_allowed,
    dual_venv_conflict_message,
    ensure_project_venv,
    list_nested_python_package_dirs,
    resolve_effective_venv_root,
    resolve_guardrail_workspace,
    root_requirements_is_runtime_only,
)


def test_list_nested_python_package_dirs_finds_backend(tmp_path: Path):
    root = tmp_path / "app"
    backend = root / "backend"
    backend.mkdir(parents=True)
    (backend / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (root / "requirements.txt").write_text("anthropic\n", encoding="utf-8")

    nested = list_nested_python_package_dirs(str(root))
    assert backend in nested


def test_root_requirements_is_runtime_only_detects_anthropic(tmp_path: Path):
    req = tmp_path / "requirements.txt"
    req.write_text("anthropic\n", encoding="utf-8")
    assert root_requirements_is_runtime_only(req) is True

    req.write_text("fastapi\nuvicorn\n", encoding="utf-8")
    assert root_requirements_is_runtime_only(req) is False


def test_resolve_effective_venv_root_prefers_backend_in_monorepo(tmp_path: Path):
    root = tmp_path / "app"
    backend = root / "backend"
    backend.mkdir(parents=True)
    (backend / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (root / "requirements.txt").write_text("anthropic\n", encoding="utf-8")

    assert resolve_effective_venv_root(str(backend), str(root)) == str(backend)
    assert resolve_effective_venv_root(str(root), str(root)) == str(backend)


def test_resolve_effective_venv_root_ambiguous_multi_nested_at_root(tmp_path: Path):
    root = tmp_path / "app"
    backend = root / "backend"
    worker = root / "worker"
    backend.mkdir(parents=True)
    worker.mkdir(parents=True)
    (backend / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (worker / "requirements.txt").write_text("celery\n", encoding="utf-8")
    (root / "requirements.txt").write_text("anthropic\n", encoding="utf-8")

    assert resolve_effective_venv_root(str(root), str(root)) is None
    assert resolve_effective_venv_root(str(backend), str(root)) == str(backend)
    assert resolve_effective_venv_root(str(worker), str(root)) == str(worker)


def test_dual_venv_conflict_when_root_and_nested_exist(tmp_path: Path):
    root = tmp_path / "app"
    backend = root / "backend"
    backend.mkdir(parents=True)
    (backend / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (backend / ".venv").mkdir()

    msg = dual_venv_conflict_message(str(root), target_venv_root=str(backend))
    assert msg is not None
    assert "Dual .venv conflict" in msg


def test_check_venv_location_allowed_blocks_root_creation(tmp_path: Path):
    root = tmp_path / "app"
    backend = root / "backend"
    backend.mkdir(parents=True)
    (backend / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    blocked = check_venv_location_allowed(str(root), str(root))
    assert blocked is not None
    assert "monorepo root" in blocked.lower()


def test_resolve_guardrail_workspace_uses_project_not_agent_home(tmp_path: Path):
    agent_ws = tmp_path / "agent_ws"
    project = agent_ws / "my-app"
    backend = project / "backend"
    backend.mkdir(parents=True)
    (backend / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    scope = resolve_guardrail_workspace(str(backend), str(agent_ws))
    assert scope == str(project.resolve())


def test_ensure_project_venv_skips_blocked_root(tmp_path: Path):
    root = tmp_path / "app"
    backend = root / "backend"
    backend.mkdir(parents=True)
    (backend / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    bin_dir, python_exe = ensure_project_venv(str(root), workspace_root=str(root))
    assert bin_dir is None
    assert python_exe is None
    assert not (root / ".venv").exists()
