"""Delete-file tool -- Delete a file or directory from the workspace.

Safer than bash('rm -rf ...') because it:
  - Requires explicit recursive=true to delete non-empty directories
  - Works identically on Windows, macOS, and Linux
  - Returns a clear error if the path doesn't exist

Typical usage by the agent:
    delete_file(path="temp/output.log")
    delete_file(path="old_build/", recursive=True)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)


from .write import (  # noqa: E402
    DELEGATE_MAX_PATH_DELETES,
    _resolve_path,
)


class DeleteFileTool:
    """Delete a file or directory.

    Parameters
    ----------
    cwd : str
        Working directory for resolving relative paths.
    shared_cwd : object | None
        Shared mutable CWD updated by the bash tool.
    write_counts_ref : dict[str, int] | None
        Shared delegate write counters — reset for a path after delete.
    delete_counts_ref : dict[str, int] | None
        Shared delegate delete counters per resolved path.
    """

    def __init__(
        self,
        cwd: str,
        shared_cwd: object | None = None,
        *,
        write_counts_ref: dict[str, int] | None = None,
        delete_counts_ref: dict[str, int] | None = None,
        on_repeated_delete_escalation: Callable[
            [str, int],
            Awaitable[tuple[str, bool]] | tuple[str, bool] | None,
        ] | None = None,
    ) -> None:
        self._cwd = cwd
        self._workspace_root = cwd
        self._shared_cwd = shared_cwd
        self._write_counts_ref = write_counts_ref
        self._delete_counts_ref = delete_counts_ref
        self._on_repeated_delete_escalation = on_repeated_delete_escalation

    @property
    def _effective_cwd(self) -> str:
        if self._shared_cwd is not None:
            return str(self._shared_cwd)
        return self._cwd

    @property
    def name(self) -> str:
        return "delete_file"

    @property
    def description(self) -> str:
        return (
            "Delete a file or directory. "
            "For non-empty directories, set recursive=true. "
            "Safer and cross-platform compared to bash rm/del commands."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file or directory to delete (relative or absolute)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Delete non-empty directories recursively (default: false)",
                },
            },
            "required": ["path"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        path_str = params.get("path", "").strip()
        if not path_str:
            return ToolResult(content="Error: 'path' is required.", is_error=True)

        from .tool_path_args import normalize_tool_path_arg

        path_str, path_err = normalize_tool_path_arg(
            path_str, cwd=self._effective_cwd, key="path",
        )
        if path_err:
            return ToolResult(content=path_err, is_error=True)

        recursive = bool(params.get("recursive", False))
        target = _resolve_path(path_str, self._effective_cwd)

        resolved_key = ""
        prev_delete_count = 0
        if self._delete_counts_ref is not None:
            try:
                resolved_key = str(target.resolve())
                prev_delete_count = self._delete_counts_ref.get(resolved_key, 0)
            except OSError:
                resolved_key = ""

        if (
            resolved_key
            and self._delete_counts_ref is not None
            and prev_delete_count >= DELEGATE_MAX_PATH_DELETES
            and target.is_file()
        ):
            _block_msg = (
                f"BLOCKED: {path_str} was already deleted and rewritten "
                f"{prev_delete_count} time(s) this session. Use edit() for "
                f"fixes or escalate() — do not delete/rewrite this path again."
            )
            if self._on_repeated_delete_escalation:
                try:
                    _cb = self._on_repeated_delete_escalation(
                        path_str, prev_delete_count + 1,
                    )
                    if asyncio.iscoroutine(_cb):
                        _extra, _terminate = await _cb
                    elif _cb is not None:
                        _extra, _terminate = _cb
                    else:
                        _extra, _terminate = ("", False)
                    if _extra:
                        _block_msg += "\n\n" + _extra
                    if _terminate:
                        return ToolResult(
                            content=_block_msg,
                            is_error=True,
                            details={
                                "delete_blocked": True,
                                "path": path_str,
                                "recommended_tool": "edit",
                            },
                        )
                except Exception:
                    logger.debug("delete-block escalation failed", exc_info=True)
            return ToolResult(
                content=_block_msg,
                is_error=True,
                details={
                    "delete_blocked": True,
                    "path": path_str,
                    "recommended_tool": "edit",
                },
            )

        if not target.exists() and not target.is_symlink():
            return ToolResult(
                content=f"Error: Path not found: {path_str}",
                is_error=True,
            )

        # Safety: never delete outside the workspace root
        try:
            target.resolve().relative_to(Path(self._workspace_root).resolve())
        except ValueError:
            return ToolResult(
                content=(
                    f"Error: Refusing to delete '{path_str}' — "
                    "path is outside the workspace root."
                ),
                is_error=True,
            )

        try:
            if target.is_symlink():
                target.unlink()
                return ToolResult(content=f"Deleted symlink: {path_str}")

            if target.is_file():
                target.unlink()
                _msg = f"Deleted file: {path_str}"
                if resolved_key and self._delete_counts_ref is not None:
                    self._delete_counts_ref[resolved_key] = prev_delete_count + 1
                    if self._write_counts_ref is not None:
                        self._write_counts_ref.pop(resolved_key, None)
                    if prev_delete_count == 0:
                        _msg += (
                            " Write counter reset — you may write() this path "
                            "once from scratch, then use edit()."
                        )
                    elif prev_delete_count == 1:
                        _msg += (
                            " Second delete on this path — one more "
                            "delete+rewrite cycle will require orchestrator "
                            "guidance."
                        )
                return ToolResult(content=_msg)

            if target.is_dir():
                child_count = sum(1 for _ in target.iterdir())
                if child_count > 0 and not recursive:
                    return ToolResult(
                        content=(
                            f"Error: Directory '{path_str}' is not empty "
                            f"({child_count} entries). "
                            "Set recursive=true to delete it and all its contents."
                        ),
                        is_error=True,
                    )
                shutil.rmtree(str(target))
                return ToolResult(
                    content=f"Deleted directory: {path_str} ({child_count} entries removed)",
                )

            return ToolResult(
                content=f"Error: Unknown file type at: {path_str}",
                is_error=True,
            )

        except PermissionError as e:
            return ToolResult(
                content=f"Error: Permission denied deleting '{path_str}': {e}",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                content=f"Error deleting '{path_str}': {e}",
                is_error=True,
            )


def create_delete_file_tool(cwd: str, shared_cwd: object | None = None) -> DeleteFileTool:
    """Factory: create a delete_file tool configured for a working directory."""
    return DeleteFileTool(cwd, shared_cwd=shared_cwd)
