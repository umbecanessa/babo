"""server_install tool -- Install Python packages into the server runtime.

Uses ``sys.executable -m pip install`` so the package lands in the
exact venv the server is running in, regardless of what ``pip`` the
agent's bash shell would resolve to.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import subprocess
import sys
from typing import Any

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

_BLOCKED_PACKAGES = frozenset({
    "pip", "setuptools", "wheel", "distribute",
})

# CLI tools that agents often confuse with Python packages.
# Installing these from PyPI gives useless/wrong packages.
_CLI_NOT_PYTHON: dict[str, str] = {
    "gh": "GitHub CLI — install via 'bash brew install gh' or 'bash apt install gh'. For git auth, use 'bash git clone https://<token>@github.com/...'",
    "docker": "Docker CLI — install from docker.com or via system package manager",
    "kubectl": "Kubernetes CLI — install via 'bash brew install kubectl'",
    "terraform": "Terraform CLI — install via 'bash brew install terraform'",
    "node": "Node.js runtime — already bundled; use 'bash node ...' directly",
    "npm": "npm CLI — already bundled; use 'bash npm ...' directly",
    "git": "Git CLI — install via 'bash brew install git' (Homebrew) or use bash directly",
    "curl": "curl CLI — pre-installed on most systems; use 'bash curl ...' directly",
    "wget": "wget CLI — install via 'bash brew install wget'",
    "ffmpeg": "FFmpeg CLI — install via 'bash brew install ffmpeg'",
    "jq": "jq CLI — install via 'bash brew install jq'",
    "aws": "AWS CLI — install via 'bash pip install awscli' (this one IS a Python package, use 'awscli' not 'aws')",
    "heroku": "Heroku CLI — install via system package manager, not pip",
}


class ServerInstallTool:
    """Install a Python package into the server's own runtime."""

    @property
    def name(self) -> str:
        return "server_install"

    @property
    def description(self) -> str:
        return (
            "Install a PYTHON LIBRARY into Babo's AGENT RUNTIME (the NLS server "
            "venv) — expands what the agent itself can do (tools, skills, "
            "optional imports in agent code). NOT for dependencies of the app "
            "you are building; use project_install for those.\n"
            "Use instead of 'bash pip install'. After install the package is "
            "importable in the server process immediately.\n"
            "Do NOT use for CLI tools ('gh', 'docker', 'kubectl') — use bash. "
            "For libraries your generated project needs (assemblyai, fastapi, "
            "etc.) use project_install."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": (
                        "Package specifier (e.g. 'requests', 'pandas>=2.0', "
                        "'python-docx'). Supports pip syntax."
                    ),
                },
            },
            "required": ["package"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        package = (params.get("package") or "").strip()
        if not package:
            return ToolResult(
                content="Error: 'package' is required.",
                is_error=True,
            )

        base_name = package.split(">=")[0].split("<=")[0].split("==")[0].split("[")[0].strip().lower()
        if base_name in _BLOCKED_PACKAGES:
            return ToolResult(
                content=f"Error: Cannot modify core package '{base_name}'.",
                is_error=True,
            )

        if base_name in _CLI_NOT_PYTHON:
            hint = _CLI_NOT_PYTHON[base_name]
            return ToolResult(
                content=(
                    f"Error: '{base_name}' is a CLI tool, NOT a Python library. "
                    f"server_install only installs Python packages from PyPI.\n"
                    f"Hint: {hint}"
                ),
                is_error=True,
            )

        logger.info("server_install: installing '%s' via %s", package, sys.executable)

        try:
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [sys.executable, "-m", "pip", "install", package],
                    capture_output=True,
                    text=True,
                    timeout=180,
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

        if proc.returncode == 0:
            installed_lines = [
                ln for ln in proc.stdout.splitlines()
                if ln.startswith("Successfully installed")
                or ln.startswith("Requirement already satisfied")
            ]
            summary = installed_lines[0] if installed_lines else f"Installed {package}"
            logger.info("server_install: %s", summary)
            return ToolResult(content=summary)

        stderr_tail = proc.stderr.strip().splitlines()[-5:] if proc.stderr else []
        stdout_tail = proc.stdout.strip().splitlines()[-5:] if proc.stdout else []
        diag_lines = []
        if stdout_tail:
            diag_lines.append("stdout:\n" + "\n".join(stdout_tail))
        if stderr_tail:
            diag_lines.append("stderr:\n" + "\n".join(stderr_tail))
        diag = "\n".join(diag_lines) if diag_lines else "(no output)"
        return ToolResult(
            content=(
                f"Error: pip install failed (exit {proc.returncode}), "
                f"python={sys.executable}:\n{diag}"
            ),
            is_error=True,
        )


def create_server_install_tool() -> ServerInstallTool:
    return ServerInstallTool()
