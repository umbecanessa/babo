"""Grep tool -- Fast regex/text search across files in the workspace.

Uses ripgrep (rg) when available for speed; falls back to a pure-Python
implementation so the tool works everywhere without extra installs.

Typical usage by the agent:
    grep(pattern="def run_loop", path="nls/agentic")
    grep(pattern="TODO", glob="*.py", case_insensitive=True)
    grep(pattern="import.*asyncio", glob="**/*.py", context=2)
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_MAX_RESULTS = 200


from .write import _resolve_path  # shared dedup-aware resolver


def _glob_to_rg_glob(pattern: str) -> str:
    """Pass glob patterns through to rg unchanged (rg uses the same syntax)."""
    return pattern


def _match_glob(filename: str, pattern: str) -> bool:
    """Check if a filename matches a glob pattern (basename or full path)."""
    basename = os.path.basename(filename)
    if fnmatch.fnmatch(basename, pattern):
        return True
    # Support ** patterns by matching full path
    if "**" in pattern or "/" in pattern or os.sep in pattern:
        return fnmatch.fnmatch(filename.replace(os.sep, "/"), pattern.replace(os.sep, "/"))
    return False


def _python_grep(
    root: Path,
    pattern: str,
    glob_filter: str | None,
    case_insensitive: bool,
    fixed_strings: bool,
    before: int,
    after: int,
    max_results: int,
) -> tuple[list[str], int]:
    """Pure-Python fallback for environments without ripgrep."""
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        if fixed_strings:
            regex = re.compile(re.escape(pattern), flags)
        else:
            regex = re.compile(pattern, flags)
    except re.error as e:
        return [f"Error: Invalid regex pattern: {e}"], 0

    results: list[str] = []
    total_matches = 0

    if root.is_file():
        files = [root]
    else:
        files = sorted(root.rglob("*"))

    for file_path in files:
        if not file_path.is_file():
            continue
        # Skip binary-looking files and common noise dirs
        if any(part.startswith(".") for part in file_path.parts):
            # Allow .env files and similar but skip .git, __pycache__, node_modules
            if any(
                part in (".git", "__pycache__", "node_modules", ".venv", "venv")
                for part in file_path.parts
            ):
                continue

        rel_path = str(file_path.relative_to(root) if not root.is_file() else file_path)

        if glob_filter and not _match_glob(rel_path, glob_filter):
            continue

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        lines = text.splitlines()
        for i, line in enumerate(lines):
            if regex.search(line):
                # Context lines
                ctx_start = max(0, i - before)
                ctx_end = min(len(lines), i + after + 1)

                for j in range(ctx_start, ctx_end):
                    separator = ":" if j == i else "-"
                    results.append(f"{rel_path}{separator}{j + 1}{separator}{lines[j]}")

                total_matches += 1
                if total_matches >= max_results:
                    return results, total_matches

        if results and results[-1] != "--":
            results.append("--")

    # Remove trailing separator
    while results and results[-1] == "--":
        results.pop()

    return results, total_matches


class GrepTool:
    """Search for a pattern in files using ripgrep (or Python fallback).

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
        self._rg_available: bool | None = None

    @property
    def _effective_cwd(self) -> str:
        if self._shared_cwd is not None:
            return str(self._shared_cwd)
        return self._cwd

    def _check_rg(self) -> bool:
        if self._rg_available is None:
            try:
                subprocess.run(
                    ["rg", "--version"],
                    capture_output=True,
                    timeout=5,
                )
                self._rg_available = True
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                self._rg_available = False
        return self._rg_available

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search for a pattern (regex or literal string) in files. "
            "Returns matching lines with file path and line number. "
            "Use `path` to limit search to a directory or file. "
            "Use `glob` to filter by file type (e.g. '*.py', '**/*.ts'). "
            "Faster and more reliable than bash grep/rg for cross-platform use. "
            "Call with fixed_strings=true for literal text search."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern (or literal string if fixed_strings=true) to search for",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default: current working directory)",
                },
                "glob": {
                    "type": "string",
                    "description": "Glob pattern to filter files, e.g. '*.py', '**/*.ts', '*.{js,ts}'",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive matching (default: false)",
                },
                "fixed_strings": {
                    "type": "boolean",
                    "description": "Treat pattern as a literal string, not a regex (default: false)",
                },
                "context": {
                    "type": "integer",
                    "description": "Number of lines of context to show before and after each match",
                },
                "before": {
                    "type": "integer",
                    "description": "Number of context lines before each match",
                },
                "after": {
                    "type": "integer",
                    "description": "Number of context lines after each match",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Maximum number of matches to return (default: {_MAX_RESULTS})",
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
        glob_filter = params.get("glob", "")
        case_insensitive = bool(params.get("case_insensitive", False))
        fixed_strings = bool(params.get("fixed_strings", False))
        context = int(params.get("context", 0))
        before = int(params.get("before", context))
        after = int(params.get("after", context))
        max_results = int(params.get("max_results", _MAX_RESULTS))
        max_results = min(max_results, _MAX_RESULTS * 2)

        search_root = (
            _resolve_path(path_str, self._effective_cwd)
            if path_str
            else Path(self._effective_cwd)
        )

        if not search_root.exists():
            return ToolResult(
                content=f"Error: Path not found: {path_str}",
                is_error=True,
            )

        if self._check_rg():
            return await self._run_rg(
                pattern, search_root, glob_filter, case_insensitive,
                fixed_strings, before, after, max_results,
            )
        else:
            return self._run_python(
                pattern, search_root, glob_filter, case_insensitive,
                fixed_strings, before, after, max_results,
            )

    async def _run_rg(
        self,
        pattern: str,
        root: Path,
        glob_filter: str,
        case_insensitive: bool,
        fixed_strings: bool,
        before: int,
        after: int,
        max_results: int,
    ) -> ToolResult:
        cmd = [
            "rg",
            "--with-filename",
            "--line-number",
            "--no-heading",
            "--color=never",
        ]
        if case_insensitive:
            cmd.append("--ignore-case")
        if fixed_strings:
            cmd.append("--fixed-strings")
        if before:
            cmd.extend(["-B", str(before)])
        if after:
            cmd.extend(["-A", str(after)])
        if glob_filter:
            cmd.extend(["--glob", glob_filter])
        # rg's --max-count limits per-file; we use per-file=max_results as an
        # approximation and then truncate the total output ourselves.
        cmd.extend(["--max-count", str(max(1, max_results // 5))])
        cmd.append("--")
        cmd.append(pattern)
        cmd.append(str(root))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._effective_cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                content="Error: grep timed out after 30 seconds.", is_error=True,
            )
        except Exception as e:
            return ToolResult(content=f"Error running ripgrep: {e}", is_error=True)

        output = stdout.decode("utf-8", errors="replace")
        lines = [l for l in output.splitlines() if l]

        # Make paths relative to workspace root for cleaner output
        rel_lines: list[str] = []
        try:
            root_str = str(root.resolve())
            ws_str = str(Path(self._workspace_root).resolve())
            for line in lines:
                # Attempt to relativise the file path prefix
                if os.sep in line or "/" in line:
                    # rg format: /abs/path/file.py:42:content
                    colon_pos = line.find(":")
                    if _IS_WINDOWS and colon_pos == 1:
                        # Drive letter - find next colon
                        colon_pos = line.find(":", 2)
                    if colon_pos > 0:
                        file_part = line[:colon_pos]
                        rest = line[colon_pos:]
                        try:
                            rel = os.path.relpath(file_part, ws_str)
                            rel_lines.append(rel + rest)
                            continue
                        except ValueError:
                            pass
                rel_lines.append(line)
        except Exception:
            rel_lines = lines

        if not rel_lines:
            return ToolResult(
                content=f"No matches found for pattern: {pattern!r}",
                details={"match_count": 0},
            )

        # Count matches (lines without leading -- separator)
        match_lines = [l for l in rel_lines if not l.startswith("--")]
        # Truncate if over limit
        truncated = False
        if len(match_lines) > max_results:
            rel_lines = rel_lines[:max_results + rel_lines[:max_results].count("--")]
            truncated = True

        content = "\n".join(rel_lines)
        summary = f"\n\n[{len(match_lines)} match(es)"
        if truncated:
            summary += f" (truncated to {max_results})"
        summary += "]"

        return ToolResult(
            content=content + summary,
            details={"match_count": len(match_lines), "truncated": truncated, "backend": "rg"},
        )

    def _run_python(
        self,
        pattern: str,
        root: Path,
        glob_filter: str,
        case_insensitive: bool,
        fixed_strings: bool,
        before: int,
        after: int,
        max_results: int,
    ) -> ToolResult:
        lines, total = _python_grep(
            root, pattern, glob_filter or None,
            case_insensitive, fixed_strings,
            before, after, max_results,
        )

        if not lines:
            return ToolResult(
                content=f"No matches found for pattern: {pattern!r}",
                details={"match_count": 0},
            )

        truncated = total >= max_results
        content = "\n".join(lines)
        summary = f"\n\n[{total} match(es)"
        if truncated:
            summary += f" (truncated to {max_results})"
        summary += "]"

        return ToolResult(
            content=content + summary,
            details={"match_count": total, "truncated": truncated, "backend": "python"},
        )


def create_grep_tool(cwd: str, shared_cwd: object | None = None) -> GrepTool:
    """Factory: create a grep tool configured for a working directory."""
    return GrepTool(cwd, shared_cwd=shared_cwd)
