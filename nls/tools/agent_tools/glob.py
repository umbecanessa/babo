"""Glob tool -- Find files by name/path pattern in the workspace.

Wraps Python's pathlib glob with workspace-aware path resolution and
sensible defaults (skip .git, __pycache__, node_modules, etc.).

Typical usage by the agent:
    glob(pattern="**/*.py")
    glob(pattern="src/**/*.ts")
    glob(pattern="*.test.*", path="frontend/src")
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .base import ToolResult, format_size

logger = logging.getLogger(__name__)

_MAX_RESULTS = 500

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist",
    "build", ".next", ".nuxt", "coverage", ".turbo",
})


from .write import _resolve_path  # shared dedup-aware resolver


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


class GlobTool:
    """Find files matching a glob pattern.

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
        return "glob"

    @property
    def description(self) -> str:
        return (
            "Find files matching a glob pattern. "
            "Supports ** for recursive matching (e.g. '**/*.py', 'src/**/*.ts'). "
            "Results are sorted by modification time (newest first). "
            "Use `path` to limit search to a subdirectory. "
            "Skips .git, node_modules, __pycache__, and other noise dirs automatically."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern to match, e.g. '**/*.py', 'src/**/*.ts', "
                        "'*.{js,ts}'. Patterns not starting with '**/' are "
                        "automatically treated as recursive."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Base directory to search in (default: current working directory)",
                },
                "include_dirs": {
                    "type": "boolean",
                    "description": "Include directories in results (default: false, files only)",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Maximum number of results (default: {_MAX_RESULTS})",
                },
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        pattern = params.get("pattern", "").strip()
        if not pattern:
            return ToolResult(content="Error: 'pattern' is required.", is_error=True)

        path_str = params.get("path", "")
        include_dirs = bool(params.get("include_dirs", False))
        max_results = min(int(params.get("max_results", _MAX_RESULTS)), _MAX_RESULTS * 2)

        base = (
            _resolve_path(path_str, self._effective_cwd)
            if path_str
            else Path(self._effective_cwd)
        )

        if not base.exists():
            return ToolResult(
                content=f"Error: Path not found: {path_str or self._effective_cwd}",
                is_error=True,
            )
        if not base.is_dir():
            return ToolResult(
                content=f"Error: Not a directory: {path_str}",
                is_error=True,
            )

        # Normalise pattern: if no slash, make it recursive
        glob_pattern = pattern
        if "/" not in pattern and "**" not in pattern:
            glob_pattern = f"**/{pattern}"

        try:
            matches_raw = list(base.glob(glob_pattern))
        except Exception as e:
            return ToolResult(content=f"Error: Invalid glob pattern: {e}", is_error=True)

        # Filter noise dirs and optionally dirs
        matches: list[Path] = []
        for p in matches_raw:
            if _should_skip(p.relative_to(base)):
                continue
            if not include_dirs and p.is_dir():
                continue
            matches.append(p)

        # Sort by modification time (newest first) — useful for recent files
        try:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            matches.sort()

        ws_root = Path(self._workspace_root).resolve()
        lines: list[str] = []
        for p in matches[:max_results]:
            try:
                rel = p.relative_to(ws_root)
                path_display = str(rel).replace("\\", "/")
            except ValueError:
                path_display = str(p)

            if p.is_file():
                try:
                    size = format_size(p.stat().st_size)
                    lines.append(f"{path_display}  ({size})")
                except Exception:
                    lines.append(path_display)
            else:
                lines.append(f"{path_display}/")

        total = len(matches)
        truncated = total > max_results

        if not lines:
            return ToolResult(
                content=f"No files found matching: {pattern!r}",
                details={"match_count": 0},
            )

        content = "\n".join(lines)
        summary = f"\n\n[{total} file(s) found"
        if truncated:
            summary += f", showing first {max_results}"
        summary += "]"

        return ToolResult(
            content=content + summary,
            details={"match_count": total, "truncated": truncated},
        )


def create_glob_tool(cwd: str, shared_cwd: object | None = None) -> GlobTool:
    """Factory: create a glob tool configured for a working directory."""
    return GlobTool(cwd, shared_cwd=shared_cwd)
