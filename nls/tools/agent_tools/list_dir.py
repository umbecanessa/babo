"""List-directory tool -- Structured directory listing.

Returns a tree-like listing of a directory with file sizes, types, and
counts.  Much faster and more portable than bash('ls') or bash('dir'),
and returns consistent output across Windows, macOS, and Linux.

Typical usage by the agent:
    list_dir()                     -- list current working directory
    list_dir(path="frontend/src")  -- list a subdirectory
    list_dir(path=".", depth=2)    -- list two levels deep
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .base import ToolResult, format_size

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 300

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})


from .write import _resolve_path  # shared dedup-aware resolver


class ListDirTool:
    """List the contents of a directory.

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
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. Shows files and subdirectories "
            "with sizes, entry counts, and modification dates. "
            "Use `depth` for a recursive tree view (default depth=1). "
            "Faster and more reliable than bash('ls') on all platforms."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list (default: current working directory)",
                },
                "depth": {
                    "type": "integer",
                    "description": "How many levels deep to recurse (default: 1, max: 4)",
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files and directories (names starting with '.') (default: false)",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        path_str = params.get("path", "")
        depth = min(int(params.get("depth", 1)), 4)
        show_hidden = bool(params.get("show_hidden", False))

        target = (
            _resolve_path(path_str, self._effective_cwd)
            if path_str
            else Path(self._effective_cwd)
        )

        if not target.exists():
            return ToolResult(
                content=f"Error: Path not found: {path_str or self._effective_cwd}",
                is_error=True,
            )
        if not target.is_dir():
            return ToolResult(
                content=f"Error: Not a directory: {path_str}",
                is_error=True,
            )

        ws_root = Path(self._workspace_root).resolve()
        try:
            display_path = str(target.resolve().relative_to(ws_root)).replace("\\", "/") or "."
        except ValueError:
            display_path = str(target)

        lines: list[str] = [f"{display_path}/"]
        entry_count = [0]

        self._render_tree(
            target, "", depth, 0, show_hidden, lines, entry_count,
        )

        truncated = entry_count[0] >= _MAX_ENTRIES
        summary = f"\n[{entry_count[0]} entries"
        if truncated:
            summary += f" (truncated at {_MAX_ENTRIES})"
        summary += "]"

        return ToolResult(
            content="\n".join(lines) + summary,
            details={"entry_count": entry_count[0], "truncated": truncated},
        )

    def _render_tree(
        self,
        directory: Path,
        prefix: str,
        max_depth: int,
        current_depth: int,
        show_hidden: bool,
        lines: list[str],
        entry_count: list[int],
    ) -> None:
        if entry_count[0] >= _MAX_ENTRIES:
            return

        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except PermissionError:
            lines.append(f"{prefix}  [permission denied]")
            return

        # Dirs first, then files
        dirs = [e for e in entries if e.is_dir()]
        files = [e for e in entries if e.is_file()]
        ordered = dirs + files

        visible = []
        for e in ordered:
            if not show_hidden and e.name.startswith("."):
                continue
            visible.append(e)

        for i, entry in enumerate(visible):
            if entry_count[0] >= _MAX_ENTRIES:
                lines.append(f"{prefix}  ... (truncated)")
                break

            is_last = i == len(visible) - 1
            connector = "+-- "
            child_prefix = prefix + ("    " if is_last else "|   ")
            entry_count[0] += 1

            if entry.is_dir():
                skip = entry.name in _SKIP_DIRS
                try:
                    child_count = sum(1 for _ in entry.iterdir())
                except Exception:
                    child_count = 0

                marker = " [skipped]" if skip else f"  ({child_count} entries)"
                lines.append(f"{prefix}{connector}{entry.name}/{marker}")

                if not skip and current_depth + 1 < max_depth:
                    self._render_tree(
                        entry, child_prefix, max_depth,
                        current_depth + 1, show_hidden, lines, entry_count,
                    )
            else:
                try:
                    size = format_size(entry.stat().st_size)
                except Exception:
                    size = "?"
                lines.append(f"{prefix}{connector}{entry.name}  ({size})")


def create_list_dir_tool(cwd: str, shared_cwd: object | None = None) -> ListDirTool:
    """Factory: create a list_dir tool configured for a working directory."""
    return ListDirTool(cwd, shared_cwd=shared_cwd)
