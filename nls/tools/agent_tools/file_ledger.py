"""File change ledger — lightweight per-agent provenance tracking.

Every successful write() or edit() call appends a JSONL entry that records:
  - timestamp, file path, action type
  - who made the change (orchestrator / delegate #N wave W)
  - line-level diff (+added / -removed) computed via difflib

The ledger lives at:  {agent_dir}/file_ledger.jsonl

Both the orchestrator and all sub-delegates share the same ledger file
because they operate on the same workspace directory.

The ``FileHistoryTool`` lets any agent query this history:
    file_history(path="src/auth.ts")           -- summary
    file_history(path="src/auth.ts", detail=True) -- full diffs
    file_history()                             -- recent changes across all files
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)

_MAX_DIFF_CHARS = 6_000   # cap per ledger entry to avoid bloat
_SKIP_DIFF_BYTES = 200_000  # skip diffing files larger than this


# ---------------------------------------------------------------------------
# FileLedger
# ---------------------------------------------------------------------------


class FileLedger:
    """Append-only JSONL ledger recording file changes with unified diffs."""

    def __init__(self, ledger_path: Path) -> None:
        self._path = ledger_path
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def record(
        self,
        file_path: str,
        before: str | None,
        after: str,
        action: str,
        author: dict[str, Any],
    ) -> None:
        """Compute a unified diff and append to the ledger.

        Parameters
        ----------
        file_path:  workspace-relative path (or absolute — kept as-is)
        before:     file content before the change (None for new files)
        after:      file content after the change
        action:     "write" or "edit"
        author:     dict with role, loop_id, iteration, etc.
        """
        _after_bytes = len(after.encode("utf-8", errors="replace"))
        _before_bytes = len((before or "").encode("utf-8", errors="replace"))
        if max(_after_bytes, _before_bytes) > _SKIP_DIFF_BYTES:
            diff_str = "(file too large to diff)"
            added = after.count("\n")
            removed = (before or "").count("\n")
        else:
            before_lines = (before or "").splitlines(keepends=True)
            after_lines = after.splitlines(keepends=True)
            raw_diff = list(difflib.unified_diff(
                before_lines, after_lines,
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                n=3,
            ))
            diff_str = "".join(raw_diff)
            if len(diff_str) > _MAX_DIFF_CHARS:
                diff_str = diff_str[:_MAX_DIFF_CHARS] + "\n...(diff truncated)"
            added = sum(
                1 for ln in raw_diff
                if ln.startswith("+") and not ln.startswith("+++")
            )
            removed = sum(
                1 for ln in raw_diff
                if ln.startswith("-") and not ln.startswith("---")
            )

        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": file_path,
            "action": action,
            "author": author,
            "stats": {"added": added, "removed": removed},
            "diff": diff_str,
        }
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("FileLedger.record write failed for %s", file_path, exc_info=True)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def history(
        self,
        path: str | None = None,
        n: int = 20,
    ) -> list[dict[str, Any]]:
        """Return up to *n* most recent ledger entries, optionally filtered."""
        entries: list[dict[str, Any]] = []
        if not self._path.exists():
            return entries
        try:
            with self._path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if path is None:
                        entries.append(entry)
                    else:
                        ep = entry.get("path", "")
                        # Match exactly, or as a path suffix (not bare substring)
                        # so "auth.ts" doesn't accidentally match "router.ts".
                        if (
                            ep == path
                            or ep.endswith("/" + path)
                            or ep.endswith("\\" + path)
                        ):
                            entries.append(entry)
        except Exception:
            logger.debug("FileLedger.history read failed", exc_info=True)
        return entries[-n:]

    def format_summary(
        self,
        path: str | None = None,
        detail: bool = False,
        n: int = 10,
    ) -> str:
        """Return a human-readable history string for injection into context."""
        entries = self.history(path, n)
        if not entries:
            if path:
                return f"No recorded changes for '{path}' yet."
            return "No file changes recorded yet."

        lines: list[str] = []
        scope = f"'{path}'" if path else "all files"
        lines.append(f"File history ({scope}) — {len(entries)} change(s):\n")

        for e in entries:
            ts = e.get("ts", "")[:19].replace("T", " ")
            fp = e.get("path", "?")
            action = e.get("action", "write")
            stats = e.get("stats", {})
            added = stats.get("added", 0)
            removed = stats.get("removed", 0)
            who = _format_author(e.get("author", {}))
            lines.append(
                f"  [{ts}] {action:5s}  {fp}  "
                f"+{added}/-{removed} lines  by {who}"
            )
            if detail and e.get("diff"):
                lines.append("")
                for dl in e["diff"].splitlines():
                    lines.append("    " + dl)
                lines.append("")

        if not detail:
            lines.append(
                "\nUse detail=True to see the full unified diff for each change."
            )
        return "\n".join(lines)


def _format_author(author: dict[str, Any]) -> str:
    role = author.get("role", "agent")
    wave = author.get("wave")
    delegate = author.get("delegate_index")
    iteration = author.get("iteration")

    if role == "delegate" and delegate is not None:
        s = f"delegate #{delegate}"
        if wave is not None:
            s += f" (wave {wave})"
    elif role == "orchestrator":
        s = "orchestrator"
    else:
        s = role

    if iteration is not None:
        s += f" iter {iteration}"
    return s


# ---------------------------------------------------------------------------
# FileHistoryTool
# ---------------------------------------------------------------------------


class FileHistoryTool:
    """Query the file change ledger — who wrote/edited what and when.

    Examples
    --------
    file_history(path="src/lib/auth.ts")
        → summary of all recorded changes to auth.ts

    file_history(path="src/lib/auth.ts", detail=True)
        → same, plus full unified diff for each change

    file_history()
        → recent changes across all files (default last 10)
    """

    def __init__(self, ledger: FileLedger) -> None:
        self._ledger = ledger

    @property
    def name(self) -> str:
        return "file_history"

    @property
    def description(self) -> str:
        return (
            "Show who created or modified a file and exactly what changed "
            "(lines added / removed). Useful for the orchestrator to review "
            "delegate work, and for delegates to understand what prior "
            "waves already wrote before starting on a file.\n"
            "Call with path= to inspect a specific file, or omit for a "
            "recent-changes summary across all files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path to inspect, e.g. 'src/lib/auth.ts'. "
                        "Omit to see recent changes across all files."
                    ),
                },
                "detail": {
                    "type": "boolean",
                    "description": (
                        "Include the full unified diff for each change. "
                        "Default: false (summary only)."
                    ),
                },
                "n": {
                    "type": "integer",
                    "description": "Max number of entries to return (default: 10).",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        path = params.get("path") or None
        detail = bool(params.get("detail", False))
        n = min(int(params.get("n", 10)), 50)
        return ToolResult(
            content=self._ledger.format_summary(path, detail, n),
            details={"ledger_path": str(self._ledger._path)},
        )
