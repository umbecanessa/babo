"""Move-file tool -- Move or rename files and directories.

Cross-platform alternative to bash('mv ...') / bash('Rename-Item ...').
Uses shutil.move so it works across filesystem boundaries (e.g. moving
a file from a temp dir to the workspace).

Typical usage by the agent:
    move_file(source="old_name.py", destination="new_name.py")
    move_file(source="src/utils.py", destination="src/helpers/utils.py")
    move_file(source="build/output/", destination="dist/")
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)


from .write import _resolve_path  # shared dedup-aware resolver


class MoveFileTool:
    """Move or rename a file or directory.

    Parameters
    ----------
    cwd : str
        Working directory for resolving relative paths.
    shared_cwd : object | None
        Shared mutable CWD updated by the bash tool.
    """

    def __init__(self, cwd: str, shared_cwd: object | None = None) -> None:
        self._cwd = cwd
        self._workspace_root = cwd
        self._shared_cwd = shared_cwd

    @property
    def _effective_cwd(self) -> str:
        if self._shared_cwd is not None:
            return str(self._shared_cwd)
        return self._cwd

    @property
    def name(self) -> str:
        return "move_file"

    @property
    def description(self) -> str:
        return (
            "Move or rename a file or directory. "
            "Works across directories and filesystem boundaries. "
            "If the destination is an existing directory, the source is moved inside it. "
            "Cross-platform — use instead of bash mv/Rename-Item."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Source file or directory path (relative or absolute)",
                },
                "destination": {
                    "type": "string",
                    "description": (
                        "Destination path. If an existing directory, the source "
                        "is moved inside it. Otherwise treated as the new name/path."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Overwrite destination if it already exists as a file (default: false)",
                },
            },
            "required": ["source", "destination"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        source_str = params.get("source", "").strip()
        dest_str = params.get("destination", "").strip()
        overwrite = bool(params.get("overwrite", False))

        if not source_str:
            return ToolResult(content="Error: 'source' is required.", is_error=True)
        if not dest_str:
            return ToolResult(content="Error: 'destination' is required.", is_error=True)

        from .tool_path_args import normalize_tool_path_arg

        source_str, src_err = normalize_tool_path_arg(
            source_str, cwd=self._effective_cwd, key="source",
        )
        if src_err:
            return ToolResult(content=src_err, is_error=True)
        dest_str, dst_err = normalize_tool_path_arg(
            dest_str, cwd=self._effective_cwd, key="destination",
        )
        if dst_err:
            return ToolResult(content=dst_err, is_error=True)

        source = _resolve_path(source_str, self._effective_cwd)
        dest = _resolve_path(dest_str, self._effective_cwd)

        if not source.exists() and not source.is_symlink():
            return ToolResult(
                content=f"Error: Source not found: {source_str}",
                is_error=True,
            )

        # If dest is an existing directory, the final path is dest/source_name
        if dest.is_dir():
            final_dest = dest / source.name
        else:
            final_dest = dest

        # Conflict check for files
        if final_dest.exists() and final_dest.is_file() and not overwrite:
            return ToolResult(
                content=(
                    f"Error: Destination already exists: {dest_str}. "
                    "Set overwrite=true to replace it."
                ),
                is_error=True,
            )

        # Ensure destination parent directory exists
        final_dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.move(str(source), str(final_dest))
            return ToolResult(
                content=f"Moved: {source_str} → {dest_str}",
                details={"source": source_str, "destination": dest_str},
            )
        except PermissionError as e:
            return ToolResult(
                content=f"Error: Permission denied moving '{source_str}': {e}",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                content=f"Error moving '{source_str}' to '{dest_str}': {e}",
                is_error=True,
            )


def create_move_file_tool(cwd: str, shared_cwd: object | None = None) -> MoveFileTool:
    """Factory: create a move_file tool configured for a working directory."""
    return MoveFileTool(cwd, shared_cwd=shared_cwd)
