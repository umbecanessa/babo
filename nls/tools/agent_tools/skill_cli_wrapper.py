"""CLI Wrapper Tool — auto-generated callable tool for instruction-based skills.

When a ClawHub/AgentSkill declares ``requires_bins`` in its metadata,
we generate a real tool that the model can call naturally (e.g.
``gog(command="gmail send ...")``).  Under the hood the tool:

1. Checks if the binary is installed (``shutil.which``)
2. Runs it via ``asyncio.create_subprocess_shell``
3. Returns actionable errors (install commands, auth setup hints)
4. Records every execution in the crystallization tracker

This bridges the gap between instruction-based skills and the model's
natural tool-calling behavior, and feeds the crystallization pipeline
so heavily-used skills eventually become native Python plugins.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from .base import ToolResult, truncate_tail

logger = logging.getLogger(__name__)

_AUTH_PATTERNS = re.compile(
    r"auth|credential|login|token|oauth|unauthorized|permission denied|"
    r"access denied|not authenticated|sign.?in|unauthenticated",
    re.IGNORECASE,
)

_DEFAULT_TIMEOUT = 60

# Extra binary directories that may not be in the default subprocess PATH.
_EXTRA_BIN_DIRS: tuple[str, ...] = (
    "/opt/homebrew/bin",          # macOS ARM Homebrew
    "/opt/homebrew/sbin",
    "/usr/local/bin",             # macOS Intel Homebrew / Linux common
    "/usr/local/sbin",
    str(Path.home() / ".local" / "bin"),  # pip --user, pipx
    "/snap/bin",                  # Linux snap
    str(Path.home() / "go" / "bin"),      # Go installs
    str(Path.home() / ".cargo" / "bin"),  # Rust installs
)


def _enhanced_path() -> str:
    """Build a PATH string that includes common binary directories."""
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep)
    parts_set = set(parts)
    for d in _EXTRA_BIN_DIRS:
        if d not in parts_set and os.path.isdir(d):
            parts.insert(0, d)
    return os.pathsep.join(parts)


def _which(binary: str) -> str | None:
    """Like shutil.which but searches the enhanced PATH."""
    return shutil.which(binary, path=_enhanced_path())


class SkillCLIWrapperTool:
    """Auto-generated AgentTool that wraps a CLI binary from an AgentSkill.

    Constructed by ``SkillLoader.cli_wrappers_for()`` for every enabled
    instruction-based skill that declares ``requires_bins``.
    """

    # Type alias for the optional setup callback.
    SetupCallback = Callable[
        [str, str, str, str],
        Coroutine[Any, Any, ToolResult | None],
    ]

    def __init__(
        self,
        skill_name: str,
        bin_name: str,
        description: str,
        instructions: str = "",
        install_instructions: list[dict[str, str]] | None = None,
        setup_notes: str = "",
        cwd: str | Path = ".",
        calibrator: Any = None,
        on_setup_needed: SetupCallback | None = None,
    ) -> None:
        self._skill_name = skill_name
        self._bin_name = bin_name
        self._description = description
        self._instructions = instructions
        self._install_instructions = install_instructions or []
        self._setup_notes = setup_notes
        self._cwd = str(cwd)
        self._calibrator = calibrator
        self._on_setup_needed = on_setup_needed
        self._setup_attempted = False

    # -- AgentTool protocol --------------------------------------------------

    @property
    def name(self) -> str:
        return self._bin_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        f"Arguments to pass to '{self._bin_name}'. "
                        f"Example: '{self._bin_name} --help' would be "
                        f"command='--help'."
                    ),
                },
            },
            "required": ["command"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        command = params.get("command", "").strip()

        binary_path = _which(self._bin_name)
        if not binary_path:
            setup_result = await self._try_auto_setup()
            if setup_result is not None and setup_result.is_error:
                return setup_result
            binary_path = _which(self._bin_name)
            if not binary_path:
                return self._missing_binary_error()

        full_cmd = f"{self._bin_name} {command}" if command else self._bin_name

        env = {**os.environ, "PATH": _enhanced_path()}
        try:
            proc = await asyncio.create_subprocess_shell(
                full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=_DEFAULT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                self._record_encounter(success=False)
                return ToolResult(
                    content=(
                        f"Command timed out after {_DEFAULT_TIMEOUT}s: "
                        f"`{full_cmd}`\n\n"
                        f"The process was killed. Try a simpler command or "
                        f"increase the scope of what you're asking."
                    ),
                    is_error=True,
                    details={"exit_code": -1, "timeout": True},
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0

            combined = stdout
            if stderr:
                combined += f"\n[stderr]\n{stderr}" if stdout else stderr

            combined, was_truncated, trunc_details = truncate_tail(combined)

            success = exit_code == 0
            self._record_encounter(success=success)

            if not success:
                error_text = f"Command `{full_cmd}` failed (exit {exit_code}):\n{combined}"
                if _AUTH_PATTERNS.search(combined):
                    error_text += self._auth_setup_hint()
                return ToolResult(
                    content=error_text,
                    is_error=True,
                    details={"exit_code": exit_code, **trunc_details},
                )

            return ToolResult(
                content=combined or "(no output)",
                details={"exit_code": exit_code, **trunc_details},
            )

        except FileNotFoundError:
            self._record_encounter(success=False)
            return self._missing_binary_error()
        except Exception as exc:
            self._record_encounter(success=False)
            return ToolResult(
                content=f"Failed to run `{full_cmd}`: {exc}",
                is_error=True,
            )

    # -- Internal helpers ----------------------------------------------------

    async def _try_auto_setup(self) -> ToolResult | None:
        """Attempt to auto-install the missing binary via a focused sub-task.

        Returns a ``ToolResult`` with ``is_error=True`` if setup explicitly
        failed, or ``None`` if setup succeeded / wasn't attempted / no
        callback.  The caller re-checks ``shutil.which`` after ``None``.
        """
        if self._on_setup_needed is None or self._setup_attempted:
            return None
        self._setup_attempted = True

        install_cmd = self._get_install_command()
        try:
            result = await self._on_setup_needed(
                self._skill_name,
                self._bin_name,
                install_cmd,
                self._setup_notes,
            )
            if result is not None and result.is_error:
                return result
        except Exception as exc:
            logger.debug(
                "Auto-setup callback failed for %s: %s",
                self._skill_name, exc,
            )
        return None

    def _record_encounter(self, success: bool) -> None:
        """Feed the crystallization pipeline with this execution."""
        if self._calibrator is None:
            return
        try:
            tracker = getattr(self._calibrator, "domain_tracker", None)
            if tracker and hasattr(tracker, "record_skill_encounter"):
                tracker.record_skill_encounter(
                    skill_name=self._skill_name,
                    success=success,
                )
        except Exception:
            logger.debug(
                "Failed to record skill encounter for %s",
                self._skill_name,
                exc_info=True,
            )

    def _missing_binary_error(self) -> ToolResult:
        """Actionable error when the binary is not on PATH."""
        install_cmd = self._get_install_command()
        msg = (
            f"Binary '{self._bin_name}' is not installed or not on PATH.\n\n"
        )
        if install_cmd:
            msg += f"Install it:\n```\n{install_cmd}\n```\n\n"
        else:
            msg += (
                f"Install '{self._bin_name}' for your platform, then try again.\n"
            )
        if self._setup_notes:
            msg += f"Setup notes:\n{self._setup_notes}\n"
        return ToolResult(content=msg, is_error=True)

    def _get_install_command(self) -> str:
        """Extract the best install command for the current platform."""
        os_name = platform.system().lower()
        for inst in self._install_instructions:
            kind = inst.get("kind", "")
            if kind == "brew" and os_name == "darwin":
                formula = inst.get("formula", "")
                tap = inst.get("tap", "")
                if tap:
                    return f"brew install {tap}/{formula}" if formula else f"brew tap {tap}"
                return f"brew install {formula}"
            elif kind == "npm":
                return f"npm install -g {inst.get('package', self._bin_name)}"
            elif kind == "go":
                return f"go install {inst.get('package', '')}@latest"
            elif kind == "pip":
                return f"pip install {inst.get('package', self._bin_name)}"

        if os_name == "darwin":
            return f"brew install {self._bin_name}"
        elif os_name == "linux":
            return f"apt install {self._bin_name}  # or: snap install {self._bin_name}"
        else:
            return ""

    def _auth_setup_hint(self) -> str:
        """Extract setup/auth hints from the skill instructions."""
        if not self._setup_notes:
            return ""
        return (
            f"\n\n--- Setup / Auth Required ---\n"
            f"{self._setup_notes}\n"
        )

    def __repr__(self) -> str:
        return f"<SkillCLIWrapperTool {self._bin_name} ({self._skill_name})>"
