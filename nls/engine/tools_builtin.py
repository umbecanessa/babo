"""Built-in filesystem and autonomic tools for the IDE and config tool registry."""

from __future__ import annotations

import fnmatch
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SKIP_DIR_NAMES = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})


@dataclass
class BuiltinToolResult:
    success: bool
    text: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _resolve_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _format_lines(lines: list[str], start_line: int = 1) -> str:
    width = max(4, len(str(start_line + len(lines) - 1)))
    return "\n".join(
        f"{i:>{width}}|{line.rstrip()}"
        for i, line in enumerate(lines, start=start_line)
    )


class FileReadTool:
    def execute(self, params: dict[str, Any]) -> BuiltinToolResult:
        path = _resolve_path(str(params.get("path", "")))
        offset = int(params.get("offset") or 0)
        limit = int(params.get("limit") or 0)

        if not path.exists():
            return BuiltinToolResult(success=False, error=f"File not found: {path}")
        if not path.is_file():
            return BuiltinToolResult(success=False, error=f"Not a file: {path}")

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return BuiltinToolResult(success=False, error=str(exc))

        lines = text.splitlines()
        start = max(0, offset - 1) if offset > 0 else 0
        end = len(lines) if limit <= 0 else min(len(lines), start + limit)
        selected = lines[start:end]
        numbered = _format_lines(selected, start_line=start + 1)
        return BuiltinToolResult(
            success=True,
            text=numbered,
            metadata={"path": str(path), "lines": len(selected), "total_lines": len(lines)},
        )


class FileWriteTool:
    def execute(self, params: dict[str, Any]) -> BuiltinToolResult:
        path = _resolve_path(str(params.get("path", "")))
        content = str(params.get("content", ""))
        append = bool(params.get("append"))

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(content)
            else:
                path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return BuiltinToolResult(success=False, error=str(exc))

        action = "appended to" if append else "wrote"
        return BuiltinToolResult(
            success=True,
            text=f"Successfully {action} {path}",
            metadata={"path": str(path), "bytes": len(content.encode("utf-8"))},
        )


class FileEditTool:
    def execute(self, params: dict[str, Any]) -> BuiltinToolResult:
        path = _resolve_path(str(params.get("path", "")))
        old_string = str(params.get("old_string", ""))
        new_string = str(params.get("new_string", ""))
        replace_all = bool(params.get("replace_all"))

        if not path.exists():
            return BuiltinToolResult(success=False, error=f"File not found: {path}")
        if not path.is_file():
            return BuiltinToolResult(success=False, error=f"Not a file: {path}")

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return BuiltinToolResult(success=False, error=str(exc))

        if old_string not in text:
            return BuiltinToolResult(
                success=False,
                error=f"old_string not found in {path}",
            )

        count = text.count(old_string) if replace_all else 1
        updated = text.replace(old_string, new_string, count if replace_all else 1)

        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return BuiltinToolResult(success=False, error=str(exc))

        return BuiltinToolResult(
            success=True,
            text=f"Replaced {count} occurrence(s) in {path}",
            metadata={"path": str(path), "replacements": count},
        )


class FileTreeTool:
    def execute(self, params: dict[str, Any]) -> BuiltinToolResult:
        root = _resolve_path(str(params.get("path", "")))
        depth = int(params.get("depth") or 2)
        glob_pat = str(params.get("glob") or "").strip()

        if not root.exists():
            return BuiltinToolResult(success=False, error=f"Path not found: {root}")
        if not root.is_dir():
            return BuiltinToolResult(success=False, error=f"Not a directory: {root}")

        lines: list[str] = []

        def _walk(dir_path: Path, level: int, indent: str) -> None:
            if level > depth:
                return
            try:
                entries = sorted(
                    dir_path.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
            except OSError as exc:
                lines.append(f"{indent}[permission denied: {exc}]")
                return

            visible = [
                e for e in entries
                if not e.name.startswith(".") and e.name not in _SKIP_DIR_NAMES
                and not (glob_pat and e.is_file() and not fnmatch.fnmatch(e.name, glob_pat))
            ]

            for entry in visible:
                if entry.is_dir():
                    lines.append(f"{indent}{entry.name}/ <dir>")
                    _walk(entry, level + 1, indent + "  ")
                else:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"{indent}{entry.name} ({size} bytes)")

        _walk(root, 1, "")
        body = "\n".join(lines) if lines else "(empty directory)"
        return BuiltinToolResult(
            success=True,
            text=body,
            metadata={"path": str(root), "entries": len(lines)},
        )


class FileSearchTool:
    def execute(self, params: dict[str, Any]) -> BuiltinToolResult:
        pattern = str(params.get("pattern", ""))
        root = _resolve_path(str(params.get("path", "")))
        glob_pat = str(params.get("glob") or "").strip()
        max_results = int(params.get("max_results") or 50)

        if not pattern:
            return BuiltinToolResult(success=False, error="pattern is required")
        if not root.exists():
            return BuiltinToolResult(success=False, error=f"Path not found: {root}")

        rg_cmd = ["rg", "--no-heading", "--line-number", "--max-count", str(max_results)]
        if glob_pat:
            rg_cmd.extend(["--glob", glob_pat])
        rg_cmd.extend([pattern, str(root)])

        try:
            proc = subprocess.run(
                rg_cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode in (0, 1):
                text = proc.stdout.strip() or "No matches found."
                return BuiltinToolResult(
                    success=True,
                    text=text,
                    metadata={"matches": text.count("\n") + (1 if text and "No matches" not in text else 0)},
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return BuiltinToolResult(success=False, error=f"Invalid regex: {exc}")

        hits: list[str] = []
        files = [root] if root.is_file() else root.rglob("*")
        for file_path in files:
            if len(hits) >= max_results:
                break
            if not file_path.is_file():
                continue
            if glob_pat and not fnmatch.fnmatch(file_path.name, glob_pat):
                continue
            if file_path.name.startswith("."):
                continue
            try:
                for line_no, line in enumerate(
                    file_path.read_text(encoding="utf-8", errors="ignore").splitlines(),
                    start=1,
                ):
                    if regex.search(line):
                        hits.append(f"{file_path}:{line_no}:{line}")
                        if len(hits) >= max_results:
                            break
            except OSError:
                continue

        text = "\n".join(hits) if hits else "No matches found."
        return BuiltinToolResult(
            success=True,
            text=text,
            metadata={"matches": len(hits)},
        )


class RequestSleepTool:
    """Agent-initiated sleep request (registered directly by factory)."""

    def __init__(self, ans: Any = None) -> None:
        self._ans = ans

    @property
    def name(self) -> str:
        return "request_sleep"

    def execute(self, params: dict[str, Any] | None = None) -> BuiltinToolResult:
        reason = ""
        if params:
            reason = str(params.get("reason", ""))
        if self._ans is not None and hasattr(self._ans, "request_sleep"):
            try:
                self._ans.request_sleep(reason=reason)
            except Exception as exc:
                return BuiltinToolResult(success=False, error=str(exc))
        return BuiltinToolResult(
            success=True,
            text="Sleep request acknowledged.",
            metadata={"reason": reason},
        )
