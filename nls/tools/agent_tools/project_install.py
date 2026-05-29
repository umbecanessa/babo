"""project_install tool — Install dependencies into the agent's project.

Unlike ``server_install`` (Babo/NLS agent runtime), this targets the
**project** the agent is building: Python packages land in ``.venv/``
(the same interpreter ``bash`` uses for ``python``), and Node packages
use ``npm`` / ``pnpm`` / ``yarn`` in the project root.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .base import ToolResult
from .project_runtime import (
    detect_ecosystem,
    detect_node_package_manager,
    ensure_project_venv,
    format_project_root_hint,
    list_workspace_project_candidates,
    resolve_project_root,
)
from .server_install import _BLOCKED_PACKAGES, _CLI_NOT_PYTHON

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_MAX_INSTALL_DIAG_LINES = 24
_MAX_INSTALL_DIAG_CHARS = 4000


def _format_install_failure_output(stdout: str, stderr: str) -> str:
    """Include enough pip/npm output for the model to fix the command."""
    chunks: list[str] = []
    for label, text in (("stdout", stdout), ("stderr", stderr)):
        lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
        if not lines:
            continue
        tail = lines[-_MAX_INSTALL_DIAG_LINES:]
        body = "\n".join(tail)
        if len(body) > _MAX_INSTALL_DIAG_CHARS:
            body = body[-_MAX_INSTALL_DIAG_CHARS:]
        chunks.append(f"{label}:\n{body}")
    return "\n".join(chunks) if chunks else "(no output)"


class ProjectInstallTool:
    """Install a dependency into the current project environment."""

    def __init__(
        self,
        cwd: str,
        shared_cwd: object | None = None,
    ) -> None:
        self._cwd = cwd
        self._workspace_root = cwd
        self._shared_cwd = shared_cwd
        self._plan_project_dir_fn: Callable[[], str] | None = None

    def set_plan_project_dir_fn(self, fn: Callable[[], str] | None) -> None:
        """Wire plan store lookup (active plan → project_dir)."""
        self._plan_project_dir_fn = fn

    def _plan_project_dir(self) -> str:
        if self._plan_project_dir_fn is None:
            return ""
        try:
            return (self._plan_project_dir_fn() or "").strip()
        except Exception:
            return ""

    @property
    def _effective_cwd(self) -> str:
        if self._shared_cwd is not None:
            return str(self._shared_cwd)
        return self._cwd

    @property
    def name(self) -> str:
        return "project_install"

    @property
    def description(self) -> str:
        return (
            "Install a dependency into the PROJECT you are building — not "
            "into Babo's agent runtime.\n"
            "- Python (PyPI): creates/uses project/.venv and runs pip there. "
            "Pass package= for one library, or omit package when requirements.txt "
            "exists to run pip install -r requirements.txt.\n"
            "- Node: runs npm/pnpm/yarn in the project root (auto-detected "
            "from lockfiles).\n"
            "Use this for libraries your generated app needs (e.g. "
            "assemblyai, express). Use server_install ONLY when YOU (the "
            "agent) need a new capability in Babo's runtime."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": (
                        "Package specifier: pip syntax for Python "
                        "(e.g. 'assemblyai', 'requests>=2.0') or npm package "
                        "name for Node (e.g. 'express'). Omit for Node to run "
                        "a full install from package.json. For Python, omit "
                        "when requirements.txt exists in the project — installs "
                        "all pins via pip install -r requirements.txt."
                    ),
                },
                "ecosystem": {
                    "type": "string",
                    "enum": ["auto", "python", "node"],
                    "description": (
                        "Target ecosystem. Default 'auto' detects from "
                        "requirements.txt / pyproject.toml vs package.json."
                    ),
                },
                "dev": {
                    "type": "boolean",
                    "description": (
                        "Node only: install as devDependency (--save-dev)."
                    ),
                },
            },
            "required": [],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        package = (params.get("package") or "").strip()
        ecosystem = (params.get("ecosystem") or "auto").strip().lower()
        dev = bool(params.get("dev", False))

        plan_dir = self._plan_project_dir()
        project_root = resolve_project_root(
            self._effective_cwd,
            self._workspace_root,
            plan_project_dir=plan_dir or None,
        )
        if not project_root:
            candidates = list_workspace_project_candidates(self._workspace_root)
            hint = format_project_root_hint(
                self._workspace_root,
                candidates,
                plan_project_dir=plan_dir,
            )
            return ToolResult(
                content=(
                    "Error: No project root found from current directory.\n"
                    f"{hint}\n"
                    "project_install targets the app you are building — not "
                    "Babo's agent runtime (use server_install for that)."
                ),
                is_error=True,
            )

        project_path = Path(project_root)
        if plan_dir and not project_path.exists():
            try:
                project_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        if ecosystem == "auto":
            detected = detect_ecosystem(project_root)
            if detected == "unknown":
                rel = Path(project_root).name
                return ToolResult(
                    content=(
                        f"Error: Could not detect ecosystem in {project_root}.\n"
                        "Add requirements.txt / pyproject.toml (Python) or "
                        "package.json (Node) under the project folder, or pass "
                        "ecosystem='python' or ecosystem='node' explicitly.\n"
                        f"If your app lives in a subfolder, run from that "
                        f"directory (e.g. cd {rel}) before project_install."
                    ),
                    is_error=True,
                )
            ecosystem = detected

        if ecosystem == "python":
            if not package:
                req_file = project_path / "requirements.txt"
                if req_file.is_file():
                    return await self._install_python_requirements(
                        project_root, str(req_file),
                    )
                return ToolResult(
                    content=(
                        "Error: 'package' is required for Python installs, "
                        "or create requirements.txt in the project root and "
                        "call project_install() with no package to install from it."
                    ),
                    is_error=True,
                )
            return await self._install_python(project_root, package)

        if ecosystem == "node":
            return await self._install_node(project_root, package, dev=dev)

        return ToolResult(
            content=f"Error: Unknown ecosystem '{ecosystem}'.",
            is_error=True,
        )

    async def _install_python(
        self,
        project_root: str,
        package: str,
    ) -> ToolResult:
        base_name = (
            package.split(">=")[0]
            .split("<=")[0]
            .split("==")[0]
            .split("[")[0]
            .strip()
            .lower()
        )
        if base_name in _BLOCKED_PACKAGES:
            return ToolResult(
                content=f"Error: Cannot modify core package '{base_name}'.",
                is_error=True,
            )
        if base_name in _CLI_NOT_PYTHON:
            hint = _CLI_NOT_PYTHON[base_name]
            return ToolResult(
                content=(
                    f"Error: '{base_name}' is a CLI tool, not a Python library.\n"
                    f"Hint: {hint}"
                ),
                is_error=True,
            )

        bin_dir, python_exe = ensure_project_venv(project_root)
        if not python_exe:
            return ToolResult(
                content=(
                    f"Error: Could not create or find project venv under "
                    f"{project_root}/.venv"
                ),
                is_error=True,
            )

        logger.info(
            "project_install: installing '%s' via %s",
            package,
            python_exe,
        )

        try:
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [python_exe, "-m", "pip", "install", package],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    cwd=project_root,
                ),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                content=f"Error: Installation of '{package}' timed out after 180s.",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                content=f"Error installing '{package}': {exc}",
                is_error=True,
            )

        importlib.invalidate_caches()

        if proc.returncode != 0:
            diag = _format_install_failure_output(proc.stdout or "", proc.stderr or "")
            return ToolResult(
                content=(
                    f"Error: pip install failed (exit {proc.returncode}), "
                    f"python={python_exe}:\n{diag}\n\n"
                    "Fix the package spec or dependency conflict above, then "
                    "retry project_install — do not use bash pip."
                ),
                is_error=True,
            )

        installed_lines = [
            ln for ln in proc.stdout.splitlines()
            if ln.startswith("Successfully installed")
            or ln.startswith("Requirement already satisfied")
        ]
        summary = installed_lines[0] if installed_lines else f"Installed {package}"

        verify = ""
        if base_name.replace("-", "_").isidentifier() or base_name.isidentifier():
            mod = base_name.replace("-", "_")
            try:
                vproc = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [python_exe, "-c", f"import {mod}; print(getattr({mod}, '__version__', 'ok'))"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=project_root,
                    ),
                )
                if vproc.returncode == 0:
                    verify = (
                        f"\nVerified import in project venv: {mod} "
                        f"({vproc.stdout.strip()})"
                    )
            except Exception:
                pass

        return ToolResult(
            content=(
                f"{summary}\n"
                f"Project: {project_root}\n"
                f"Python: {python_exe}"
                f"{verify}\n\n"
                "bash `python` in this project directory uses the same venv."
            ),
        )

    async def _install_python_requirements(
        self,
        project_root: str,
        requirements_path: str,
    ) -> ToolResult:
        bin_dir, python_exe = ensure_project_venv(project_root)
        if not python_exe:
            return ToolResult(
                content=(
                    f"Error: Could not create or find project venv under "
                    f"{project_root}/.venv"
                ),
                is_error=True,
            )

        req = Path(requirements_path)
        logger.info(
            "project_install: pip install -r %s via %s",
            req.name,
            python_exe,
        )

        try:
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [python_exe, "-m", "pip", "install", "-r", str(req)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=project_root,
                ),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                content=(
                    f"Error: pip install -r {req.name} timed out after 300s."
                ),
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                content=f"Error installing from {req.name}: {exc}",
                is_error=True,
            )

        importlib.invalidate_caches()

        if proc.returncode != 0:
            diag = _format_install_failure_output(proc.stdout or "", proc.stderr or "")
            return ToolResult(
                content=(
                    f"Error: pip install -r {req.name} failed "
                    f"(exit {proc.returncode}), python={python_exe}:\n{diag}\n\n"
                    "Fix requirements.txt, then retry project_install — "
                    "do not use bash pip."
                ),
                is_error=True,
            )

        installed_lines = [
            ln for ln in (proc.stdout or "").splitlines()
            if ln.startswith("Successfully installed")
            or ln.startswith("Requirement already satisfied")
            or ln.startswith("Collecting ")
        ]
        summary = (
            installed_lines[0]
            if installed_lines
            else f"Installed dependencies from {req.name}"
        )
        return ToolResult(
            content=(
                f"{summary}\n"
                f"Project: {project_root}\n"
                f"Python: {python_exe}\n"
                f"Requirements: {req.name}\n\n"
                "bash `python` in this project directory uses the same venv."
            ),
        )

    async def _install_node(
        self,
        project_root: str,
        package: str,
        *,
        dev: bool,
    ) -> ToolResult:
        if not (Path(project_root) / "package.json").exists():
            return ToolResult(
                content=(
                    f"Error: No package.json in {project_root}.\n"
                    "Create package.json before installing Node packages."
                ),
                is_error=True,
            )

        pm = detect_node_package_manager(project_root)
        exe = shutil.which(pm)
        if not exe:
            return ToolResult(
                content=f"Error: '{pm}' not found on PATH.",
                is_error=True,
            )

        cmd: list[str] = [exe]
        if pm == "npm":
            cmd.append("install")
            if package:
                cmd.append(package)
            if dev:
                cmd.append("--save-dev")
        elif pm == "pnpm":
            cmd.append("add")
            if dev:
                cmd.append("-D")
            if package:
                cmd.append(package)
            else:
                cmd = [exe, "install"]
        elif pm == "yarn":
            if package:
                cmd.extend(["add", package])
                if dev:
                    cmd.append("--dev")
            else:
                cmd = [exe, "install"]

        logger.info("project_install: %s in %s", " ".join(cmd), project_root)

        try:
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=project_root,
                ),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                content="Error: Node install timed out after 300s.",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                content=f"Error running {' '.join(cmd)}: {exc}",
                is_error=True,
            )

        if proc.returncode != 0:
            diag = _format_install_failure_output(proc.stdout or "", proc.stderr or "")
            return ToolResult(
                content=(
                    f"Error: {' '.join(cmd)} failed (exit {proc.returncode}):\n"
                    f"{diag}\n\n"
                    "Fix package.json / lockfile issues above, then retry "
                    "project_install from the project directory."
                ),
                is_error=True,
            )

        tail = "\n".join((proc.stdout or "").strip().splitlines()[-3:])
        label = package or "dependencies from package.json"
        return ToolResult(
            content=(
                f"Installed {label} via {pm} in {project_root}.\n"
                + (tail + "\n" if tail else "")
                + "Use bash npm/node commands from the project directory."
            ),
        )


def create_project_install_tool(
    cwd: str,
    shared_cwd: object | None = None,
) -> ProjectInstallTool:
    return ProjectInstallTool(cwd, shared_cwd=shared_cwd)
