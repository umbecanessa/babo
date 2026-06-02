"""NLS Execution Context -- Sandboxed vs. local tool execution.

Determines WHERE tool side effects land, based on how the user connects:

- Electron desktop app  -> LocalContext  (full host access)
- Web frontend / API    -> SandboxContext (scoped per-agent workspace)
- Telegram / messaging  -> SandboxContext (remote = sandboxed)

Every tool that touches the filesystem or runs commands receives an
ExecutionContext and uses it for path resolution and shell execution
instead of raw ``Path()`` / ``subprocess.run()`` calls.

Design principle: tools never know or care which context they're in.
They call ``ctx.resolve_path(p)`` and ``ctx.run_shell(cmd)`` and the
context enforces the rules.  This means the same tool code works in
both the Electron app (full power) and the web app (safe sandbox).

Future upgrade path: swap SandboxContext internals with Docker
container execution for a full Code Interpreter / Devin experience.
The tool code stays identical.
"""

from __future__ import annotations

import logging
import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shell result (lightweight, context-level)
# ---------------------------------------------------------------------------


@dataclass
class ShellResult:
    """Result from a shell command execution within a context."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Combined stdout + stderr for tool consumption."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr}")
        return "\n".join(parts) or "(no output)"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ExecutionContext(ABC):
    """Where and how tool side effects execute.

    Subclasses define the security boundary.  Tools receive a context
    instance and delegate all filesystem / shell operations to it.
    """

    @abstractmethod
    def get_context_type(self) -> str:
        """Return 'local' or 'sandbox'."""

    @abstractmethod
    def resolve_path(self, path: str) -> Path:
        """Resolve a user-provided path to an absolute path within scope.

        Raises ``PermissionError`` if the path escapes the allowed scope
        (only relevant for SandboxContext).
        """

    @abstractmethod
    def is_path_allowed(self, path: str) -> bool:
        """Check if a path is within the allowed scope (non-throwing)."""

    @abstractmethod
    def run_shell(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 30,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        """Execute a shell command within this context's boundaries."""

    @abstractmethod
    def get_working_directory(self) -> Path:
        """Return the default working directory for this context."""

    def get_info(self) -> dict[str, Any]:
        """Return metadata about this context for tool status / debugging."""
        return {
            "type": self.get_context_type(),
            "working_directory": str(self.get_working_directory()),
        }


# ---------------------------------------------------------------------------
# LocalContext -- full host access (Electron desktop app)
# ---------------------------------------------------------------------------


class LocalContext(ExecutionContext):
    """Full host filesystem and shell access.

    Used when the agent is accessed through the Electron desktop app.
    Paths are resolved relative to ``root`` (defaults to user home).
    No path restrictions -- the agent has the same access as the user.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root else Path.home()
        logger.debug("LocalContext created with root: %s", self._root)

    def get_context_type(self) -> str:
        return "local"

    def resolve_path(self, path: str) -> Path:
        """Resolve path: absolute paths pass through, relative paths
        resolve against the configured root."""
        p = Path(path)
        if p.is_absolute():
            return p.resolve()
        return (self._root / p).resolve()

    def is_path_allowed(self, path: str) -> bool:
        """Local context allows all paths."""
        return True

    def run_shell(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 30,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        """Execute shell command with no restrictions."""
        effective_cwd = cwd or str(self._root)

        shell_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        if env:
            shell_env.update(env)

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=effective_cwd,
                encoding="utf-8",
                errors="replace",
                env=shell_env,
            )
            return ShellResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ShellResult(
                stderr=f"Command timed out after {timeout}s",
                exit_code=-1,
                timed_out=True,
            )
        except Exception as e:
            return ShellResult(stderr=str(e), exit_code=-1)

    def get_working_directory(self) -> Path:
        return self._root

    def get_info(self) -> dict[str, Any]:
        return {
            "type": "local",
            "root": str(self._root),
            "working_directory": str(self._root),
            "restrictions": "none",
        }


# ---------------------------------------------------------------------------
# SandboxContext -- scoped per-agent workspace (web / messaging)
# ---------------------------------------------------------------------------


class SandboxContext(ExecutionContext):
    """Scoped workspace for web and messaging channel access.

    All filesystem operations are confined to::

        {base_dir}/agents/{agent_id}/workspace/

    Path traversal (``../``) is blocked.  Shell commands execute with
    their cwd locked to the workspace root.

    Parameters
    ----------
    agent_id : str
        The agent identifier (used for workspace isolation).
    base_dir : str | Path
        The NLS data directory (default: ``data/``).
    shell_enabled : bool
        Whether shell commands are allowed (default True with cwd lock).
        Set False to fully disable shell access for this context.
    """

    def __init__(
        self,
        agent_id: str,
        base_dir: str | Path = "data",
        shell_enabled: bool = True,
    ) -> None:
        self._agent_id = agent_id
        self._base_dir = Path(base_dir).resolve()
        self._workspace = (self._base_dir / "agents" / agent_id / "workspace").resolve()
        self._shell_enabled = shell_enabled

        # Ensure workspace exists
        self._workspace.mkdir(parents=True, exist_ok=True)
        logger.debug(
            "SandboxContext created for agent %s: %s (shell=%s)",
            agent_id, self._workspace, shell_enabled,
        )

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def workspace(self) -> Path:
        return self._workspace

    def get_context_type(self) -> str:
        return "sandbox"

    def resolve_path(self, path: str) -> Path:
        """Resolve path within the sandbox workspace.

        Relative paths resolve against the workspace root.
        Absolute paths are rebased to the workspace.
        Path traversal (``../`` escaping) raises PermissionError.
        """
        p = Path(path)

        if p.is_absolute():
            # Rebase: /some/abs/path -> workspace/some/abs/path
            # Strip the root so it becomes relative
            try:
                relative = p.relative_to("/")
            except ValueError:
                # Windows absolute path -- strip drive letter
                relative = Path(str(p)[3:]) if len(str(p)) > 2 and str(p)[1] == ":" else p
            resolved = (self._workspace / relative).resolve()
        else:
            resolved = (self._workspace / p).resolve()

        # Security: verify the resolved path is inside the workspace
        try:
            resolved.relative_to(self._workspace)
        except ValueError:
            raise PermissionError(
                f"Path escapes sandbox: '{path}' resolves to '{resolved}' "
                f"which is outside '{self._workspace}'"
            )

        return resolved

    def is_path_allowed(self, path: str) -> bool:
        """Check if path stays within sandbox (non-throwing)."""
        try:
            self.resolve_path(path)
            return True
        except (PermissionError, ValueError):
            return False

    def run_shell(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 30,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        """Execute shell command with cwd locked to workspace."""
        if not self._shell_enabled:
            return ShellResult(
                stderr="Shell access is disabled in this sandbox.",
                exit_code=-1,
            )

        # Resolve cwd within sandbox
        if cwd:
            try:
                effective_cwd = str(self.resolve_path(cwd))
            except PermissionError as e:
                return ShellResult(stderr=str(e), exit_code=-1)
        else:
            effective_cwd = str(self._workspace)

        shell_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        if env:
            shell_env.update(env)

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=effective_cwd,
                encoding="utf-8",
                errors="replace",
                env=shell_env,
            )
            return ShellResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ShellResult(
                stderr=f"Command timed out after {timeout}s",
                exit_code=-1,
                timed_out=True,
            )
        except Exception as e:
            return ShellResult(stderr=str(e), exit_code=-1)

    def get_working_directory(self) -> Path:
        return self._workspace

    def get_info(self) -> dict[str, Any]:
        return {
            "type": "sandbox",
            "agent_id": self._agent_id,
            "workspace": str(self._workspace),
            "working_directory": str(self._workspace),
            "shell_enabled": self._shell_enabled,
            "restrictions": "path_confined",
        }


# ---------------------------------------------------------------------------
# Factory: create the right context based on connection source
# ---------------------------------------------------------------------------


def create_context(
    source: str,
    agent_id: str = "default",
    root: str | Path | None = None,
    base_dir: str | Path = "data",
    shell_enabled: bool = True,
) -> ExecutionContext:
    """Create an ExecutionContext based on the connection source.

    Parameters
    ----------
    source : str
        Connection source identifier:
        - ``"electron"`` or ``"desktop"`` -> LocalContext
        - ``"web"``, ``"api"``, ``"telegram"``, ``"discord"``, etc. -> SandboxContext
    agent_id : str
        Agent identifier (used for sandbox workspace isolation).
    root : str | Path | None
        Root directory for LocalContext (default: user home).
    base_dir : str | Path
        Base data directory for SandboxContext (default: ``data/``).
    shell_enabled : bool
        Whether shell is enabled in sandbox mode.
    """
    source_lower = source.lower().strip()

    if source_lower in ("electron", "desktop", "local"):
        return LocalContext(root=root)
    else:
        return SandboxContext(
            agent_id=agent_id,
            base_dir=base_dir,
            shell_enabled=shell_enabled,
        )
