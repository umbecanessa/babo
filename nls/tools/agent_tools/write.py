"""Write tool -- Create or overwrite files with auto-directory creation.

The simplest of the four core tools.  Writes content to a file,
creating parent directories as needed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from .base import ToolResult

_logger = logging.getLogger(__name__)

_SENSITIVE_SKILL_FILES = {"__init__.py", "adapter.py", "webhook.py"}

import re

_NLS_INTERNAL_PATTERN = re.compile(
    r"(?:from|import)\s+nls\.engine\.(?!agent_tools\.base\b)"
    r"|(?:from|import)\s+nls\.(?:cli|core|internal)\b"
)


def _resolve_path(path_str: str, cwd: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p

    # If the agent writes "workspace/…" or "workspace\…" it is addressing
    # a path relative to the agent workspace root, NOT relative to a locked
    # project CWD.  Detect this by checking whether the CWD is a subdirectory
    # of something named "workspace" and the path starts with "workspace".
    # When the plan tool locks CWD to workspace/my-project/, a write of
    # workspace/research_notes/report.md must land at workspace/research_notes/
    # (KL #403) — not workspace/my-project/workspace/research_notes/.
    cwd_path = Path(cwd)
    _cwd_parts = cwd_path.parts
    if p.parts and p.parts[0] in ("workspace", "Workspace"):
        # Find the workspace root: walk up CWD until we find the "workspace" dir.
        for _i in range(len(_cwd_parts) - 1, -1, -1):
            if _cwd_parts[_i] in ("workspace", "Workspace"):
                _ws_root = Path(*_cwd_parts[: _i + 1])
                _relative_under_ws = Path(*p.parts[1:]) if len(p.parts) > 1 else Path(".")
                _resolved = _ws_root / _relative_under_ws
                if _resolved != cwd_path / p:  # only log when it actually differs
                    _logger.info(
                        "Path anchored to workspace root: %s → %s "
                        "(CWD was locked to %s)",
                        path_str, _resolved, cwd,
                    )
                return _resolved

    resolved = Path(cwd) / p
    # Guard against nested duplicate project dirs: if the first path
    # component matches the CWD's directory name, the agent is
    # redundantly prepending the project folder (e.g. writing to
    # "my-project/backend/main.py" when CWD is already "my-project/").
    cwd_name = Path(cwd).name
    if cwd_name and p.parts and p.parts[0] == cwd_name:
        if len(p.parts) > 1:
            stripped = Path(*p.parts[1:])
            _logger.warning(
                "Auto-stripped duplicate project dir from path: %s → %s "
                "(CWD already is %s)",
                path_str, str(stripped), cwd_name,
            )
            resolved = Path(cwd) / stripped
        else:
            # Path is exactly the project dir name → resolve to CWD itself
            resolved = Path(cwd)
    return resolved


def format_path_for_agent(
    path: Path,
    *,
    workspace_root: str,
    effective_cwd: str,
) -> str:
    """Format a path for tool output — prefer CWD-relative when inside the project.

    When plan locks CWD to ``workspace/my-app/``, listing/glob should show
    ``frontend/src/App.tsx`` not ``my-app/frontend/src/App.tsx`` so the model
    does not re-prefix the project folder on read/bash.
    """
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    for base in (Path(effective_cwd), Path(workspace_root)):
        try:
            base_resolved = base.resolve()
        except Exception:
            base_resolved = base
        try:
            rel = resolved.relative_to(base_resolved)
            display = str(rel).replace("\\", "/")
            return "." if display in ("", ".") else display
        except ValueError:
            continue
    return str(resolved).replace("\\", "/")


def _is_system_skill_path(path: Path) -> bool:
    """Return True if *path* would overwrite a core skill file."""
    norm = path.as_posix().lower()
    return "/skills/" in norm and path.name.lower() in _SENSITIVE_SKILL_FILES


class WriteTool:
    """Write content to a file, creating parent directories as needed.

    Parameters
    ----------
    cwd : str
        Working directory for resolving relative paths.
    shared_cwd : SharedCWD | None
        Shared mutable CWD holder updated by bash tool.
    ledger : FileLedger | None
        Optional file-change ledger for provenance tracking.
    ledger_meta : dict | None
        Author metadata attached to each ledger entry (role, delegate_index, etc.).
    """

    def __init__(self, cwd: str, shared_cwd: object | None = None,
                 file_state_cache: object | None = None,
                 ledger: object | None = None,
                 ledger_meta: dict | None = None,
                 on_repeated_write_escalation: Callable[
                     [str, int],
                     Awaitable[tuple[str, bool]] | tuple[str, bool] | None,
                 ] | None = None,
                 block_full_rewrite_after_first: bool = False) -> None:
        self._cwd = cwd
        self._shared_cwd = shared_cwd
        self._file_state_cache = file_state_cache
        self._write_counts: dict[str, int] = {}
        self._ledger = ledger
        self._ledger_meta: dict = ledger_meta or {"role": "agent"}
        self._on_repeated_write_escalation = on_repeated_write_escalation
        self._block_full_rewrite_after_first = block_full_rewrite_after_first

    @property
    def _effective_cwd(self) -> str:
        if self._shared_cwd is not None:
            return str(self._shared_cwd)
        return self._cwd

    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Creates the file and any parent "
            "directories if they don't exist. Overwrites existing files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write (relative or absolute)",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        }

    @staticmethod
    def _unescape_content(text: str) -> str:
        """Normalize literal escape sequences that models often produce.

        Many models double-escape newlines/tabs in tool call arguments,
        producing '\\n' instead of actual newlines.  Detect and fix this
        so files are written with proper formatting.
        """
        if "\\n" not in text and "\\t" not in text:
            return text
        # Only fix if the text looks like it has literal escape sequences
        # (contains \\n but very few actual newlines relative to length)
        actual_newlines = text.count("\n")
        escaped_newlines = text.count("\\n")
        if escaped_newlines > 0 and actual_newlines <= 1:
            text = text.replace("\\n", "\n")
            text = text.replace("\\t", "\t")
            text = text.replace("\\\\", "\\")
        return text

    _SHRINK_WARN_RATIO = 0.5
    _SHRINK_MIN_EXISTING = 200

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        path_str = params.get("path", "")
        content = self._unescape_content(params.get("content", ""))

        if not path_str:
            return ToolResult(content="Error: 'path' is required.", is_error=True)

        from .file_ledger import normalize_ledger_path
        path_str = normalize_ledger_path(path_str) or path_str

        path = _resolve_path(path_str, self._effective_cwd)

        if path.suffix == ".py" and _NLS_INTERNAL_PATTERN.search(content):
            return ToolResult(
                content=(
                    "BLOCKED: You are importing from nls.engine internals "
                    "(autonomic, server_runtime, agentic_loop, etc.). "
                    "These are YOUR OWN engine code — not callable APIs.\n\n"
                    "Allowed skill imports:\n"
                    "  from nls.skills import SkillMeta, SkillContext, ...\n"
                    "  from nls.engine.agent_tools.base import ToolResult\n\n"
                    "For CLI tasks (gh, git, docker, npm …), use "
                    "bash(command='...') directly — do NOT wrap them in "
                    "a Python script."
                ),
                is_error=True,
            )

        try:
            if self._ledger is not None:
                ledger_err = self._ledger.check_mutation_allowed(
                    path_str,
                    self._ledger_meta,
                    file_exists=path.exists(),
                )
                if ledger_err:
                    ctx = self._ledger.format_path_context(
                        path_str, self._ledger_meta,
                    )
                    return ToolResult(
                        content=f"{ledger_err}\n\n{ctx}",
                        is_error=True,
                    )

            # Staleness guard: refuse if file changed since last read.
            if path.exists() and self._file_state_cache is not None:
                stale_err = self._file_state_cache.check(str(path.resolve()))
                if stale_err:
                    return ToolResult(content=stale_err, is_error=True)

            # Safety net: detect destructive overwrites where the new
            # content is dramatically smaller than what already exists.
            existing_bytes = 0
            if path.exists():
                try:
                    existing_bytes = path.stat().st_size
                except OSError:
                    pass

            # Snapshot existing content for the file ledger (before write).
            _before: str | None = None
            if self._ledger is not None and path.exists():
                try:
                    _before = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

            path.parent.mkdir(parents=True, exist_ok=True)

            path.write_text(content, encoding="utf-8")

            # Record to ledger after successful write.
            if self._ledger is not None:
                try:
                    self._ledger.record(
                        path_str, _before, content, "write", self._ledger_meta,
                    )
                except Exception:
                    pass

            if self._file_state_cache is not None:
                self._file_state_cache.update(str(path.resolve()))

            line_count = content.count("\n") + (1 if content else 0)
            byte_count = len(content.encode("utf-8"))
            msg = f"Successfully wrote {line_count} lines ({byte_count} bytes) to {path_str}."

            resolved_key = str(path.resolve())
            prev_count = self._write_counts.get(resolved_key, 0)
            self._write_counts[resolved_key] = prev_count + 1

            if prev_count == 0:
                msg += (
                    " Future changes to this file should use edit() "
                    "for targeted modifications, not write()."
                )
            elif prev_count >= 1:
                msg += (
                    f"\n\n⚠ REPEATED WRITE: You have now written this "
                    f"same file {prev_count + 1} times this session. "
                    f"STOP rewriting it from scratch. The file is already "
                    f"on disk. If you need to modify specific parts, use "
                    f"edit() for surgical changes. If you are unsure "
                    f"whether the file is complete, read() it first — "
                    f"do NOT regenerate the entire file. Move on to your "
                    f"next task."
                )
                _logger.warning(
                    "Repeated write #%d to %s (%d lines, %d bytes)",
                    prev_count + 1, path_str, line_count, byte_count,
                )
                if self._block_full_rewrite_after_first:
                    msg += (
                        "\n\nBLOCKED: Delegates must not fully rewrite the "
                        "same file twice. Use read() + edit() for fixes."
                    )
                    return ToolResult(content=msg, is_error=True)
                if prev_count >= 2 and self._on_repeated_write_escalation:
                    _escalation_msg = (
                        "\n\n⚠ Third full rewrite — waiting for orchestrator "
                        "guidance (up to 2 minutes)..."
                    )
                    try:
                        _cb = self._on_repeated_write_escalation(
                            path_str, prev_count + 1,
                        )
                        if asyncio.iscoroutine(_cb):
                            _extra, _terminate = await _cb
                        elif _cb is not None:
                            _extra, _terminate = _cb
                        else:
                            _extra, _terminate = (
                                "Orchestrator notified. Use edit() or escalate().",
                                False,
                            )
                        msg += _escalation_msg + "\n\n" + _extra
                        if _terminate:
                            return ToolResult(content=msg, is_error=True)
                    except Exception:
                        _logger.debug(
                            "repeated-write escalation failed",
                            exc_info=True,
                        )
                        msg += (
                            _escalation_msg
                            + "\n\nEscalation failed — use edit() or escalate()."
                        )

            if (
                existing_bytes >= self._SHRINK_MIN_EXISTING
                and byte_count < existing_bytes * self._SHRINK_WARN_RATIO
            ):
                msg += (
                    f"\n\n⚠ CAUTION: This file previously had "
                    f"{existing_bytes} bytes but you just wrote "
                    f"{byte_count} bytes ({byte_count * 100 // existing_bytes}% "
                    f"of original). You may have accidentally overwritten "
                    f"important content. The write tool REPLACES the "
                    f"entire file — it does not append or patch. If you "
                    f"meant to modify part of the file, use the edit "
                    f"tool instead. Read the file now to verify its "
                    f"contents are correct."
                )

            if _is_system_skill_path(path):
                msg += (
                    "\n\n⚠ WARNING: You wrote to a SYSTEM SKILL file. "
                    "This may override a bundled skill. Double-check that "
                    "your code follows the SkillMeta/register(app,ctx) "
                    "contract and exports tools, adapters, and webhooks "
                    "correctly. Call request_restart to load the changes."
                )

            return ToolResult(
                content=msg,
                details={
                    "lines": line_count,
                    "bytes": byte_count,
                    "path": str(path),
                },
            )
        except PermissionError:
            return ToolResult(
                content=f"Error: Permission denied writing to {path_str}",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                content=f"Error writing file: {e}",
                is_error=True,
            )


def create_write_tool(cwd: str, shared_cwd: object | None = None,
                      file_state_cache: object | None = None,
                      ledger: object | None = None,
                      ledger_meta: dict | None = None) -> WriteTool:
    """Factory: create a write tool configured for a working directory."""
    return WriteTool(cwd, shared_cwd=shared_cwd, file_state_cache=file_state_cache,
                     ledger=ledger, ledger_meta=ledger_meta)
