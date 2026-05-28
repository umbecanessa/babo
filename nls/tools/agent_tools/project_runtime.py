"""Shared project-root and virtual-environment helpers for agent tools.

Used by ``bash`` (PATH / project ``python``) and ``project_install`` so both
target the same project-local Python environment.
"""

from __future__ import annotations

import logging
import sys
import venv as _venv_mod
from pathlib import Path

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

PROJECT_MARKERS = (
    ".git",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "setup.py",
    "pom.xml",
)

_PYTHON_MARKERS = (
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "Pipfile",
)

_NODE_MARKERS = (
    "package.json",
)


def resolve_project_root(cwd: str, workspace_root: str) -> str | None:
    """Walk up from *cwd* to find a project root (marker file present).

    Stops at *workspace_root*. Falls back to *cwd* when it differs from
    *workspace_root* (typical for plan-locked delegate CWD).
    """
    cwd_path = Path(cwd).resolve()
    workspace = Path(workspace_root).resolve()

    current = cwd_path
    while True:
        for marker in PROJECT_MARKERS:
            if (current / marker).exists():
                return str(current)
        if current == workspace or current.parent == current:
            break
        current = current.parent

    if cwd_path != workspace:
        return str(cwd_path)
    return None


def _venv_paths(project_root: str) -> tuple[Path, Path, Path]:
    venv_dir = Path(project_root) / ".venv"
    bin_dir = venv_dir / ("Scripts" if _IS_WINDOWS else "bin")
    python_exe = bin_dir / ("python.exe" if _IS_WINDOWS else "python")
    return venv_dir, bin_dir, python_exe


def ensure_project_venv(project_root: str) -> tuple[str | None, str | None]:
    """Ensure ``project_root/.venv`` exists.

    Returns ``(bin_dir, python_exe)`` as strings, or ``(None, None)`` on failure.
    """
    if not project_root:
        return None, None

    venv_dir, bin_dir, python_exe = _venv_paths(project_root)

    if python_exe.exists():
        return str(bin_dir), str(python_exe)

    try:
        logger.info("[project_runtime] Creating project venv: %s", venv_dir)
        _venv_mod.create(
            str(venv_dir),
            with_pip=True,
            system_site_packages=False,
        )
        _ensure_gitignore(project_root)
        if python_exe.exists():
            return str(bin_dir), str(python_exe)
    except Exception as exc:
        logger.warning("[project_runtime] Failed to create project venv: %s", exc)

    return None, None


def ensure_gitignore_venv(project_root: str) -> None:
    """Public wrapper for gitignore helper."""
    _ensure_gitignore(project_root)


def _ensure_gitignore(project_root: str) -> None:
    gi = Path(project_root) / ".gitignore"
    try:
        if gi.exists():
            content = gi.read_text(encoding="utf-8", errors="replace")
            if ".venv" in content:
                return
        with gi.open("a", encoding="utf-8") as f:
            f.write("\n.venv/\n")
    except Exception:
        pass


def detect_ecosystem(project_root: str) -> str:
    """Return ``python``, ``node``, or ``unknown`` for *project_root*."""
    root = Path(project_root)
    has_python = any((root / m).exists() for m in _PYTHON_MARKERS)
    has_node = any((root / m).exists() for m in _NODE_MARKERS)
    if has_python and not has_node:
        return "python"
    if has_node and not has_python:
        return "node"
    if has_python and has_node:
        return "python"
    if has_node:
        return "node"
    if has_python:
        return "python"
    return "unknown"


def detect_node_package_manager(project_root: str) -> str:
    """Return ``pnpm``, ``yarn``, or ``npm`` based on lockfiles."""
    root = Path(project_root)
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    return "npm"
