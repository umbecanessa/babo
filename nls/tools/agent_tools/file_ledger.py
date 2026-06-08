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
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)

_MAX_DIFF_CHARS = 6_000   # cap per ledger entry to avoid bloat
_SKIP_DIFF_BYTES = 200_000  # skip diffing files larger than this

# Populated per-wave via set_wave_ownership(shared_paths=...); no hardcoded layout.
_SHARED_PATHS: frozenset[str] = frozenset()

# Scratch paths delegates may create outside their assigned scope (not teammates').
_SCRATCH_DIR_PREFIXES: tuple[str, ...] = (
    "tmp/",
    "temp/",
    "scratch/",
    ".tmp/",
    ".cache/",
)


def is_scratch_path(path: str) -> bool:
    """True for temp/scratch files delegates may write outside assigned scope."""
    norm = normalize_ledger_path(path)
    if not norm:
        return False
    base = norm.rsplit("/", 1)[-1].lower()
    if base.startswith("tmp") or base.startswith(".tmp"):
        return True
    if base.endswith((".tmp", ".temp", ".scratch")):
        return True
    for prefix in _SCRATCH_DIR_PREFIXES:
        p = prefix.rstrip("/")
        if norm == p or norm.startswith(prefix):
            return True
    return False


def normalize_ledger_path(path_str: str) -> str:
    """Normalize agent-supplied paths for ledger keys and ownership checks."""
    if not path_str:
        return ""
    p = path_str.strip().strip('"').strip("'")
    # JSON blob mistake: {"path": "foo.py"
    if p.startswith("{") and "path" in p:
        m = re.search(r'["\']path["\']\s*:\s*["\']([^"\']+)', p)
        if m:
            p = m.group(1)
    # Hybrid: packages/foo/C:\Users\... → take absolute tail after drive letter
    if re.search(r"[/\\][A-Za-z]:\\", p) or re.search(r"^[A-Za-z]:\\", p):
        m = re.search(r"([A-Za-z]:\\.*)$", p.replace("/", "\\"))
        if m:
            p = m.group(1)
        else:
            parts = re.split(r"[/\\](?=[A-Za-z]:\\)", p.replace("/", "\\"))
            if parts:
                p = parts[-1]
    p = p.replace("\\", "/")
    if p.startswith("workspace/"):
        p = p[len("workspace/"):]
    return p


def strip_redundant_project_prefix(path_str: str, cwd: str) -> str:
    """Drop leading project_dir when CWD is already inside the project folder."""
    p = normalize_ledger_path(path_str)
    if not p or not cwd:
        return p
    from pathlib import Path

    p = strip_path_through_cwd_segment(p, cwd)
    parts = Path(p).parts
    if len(parts) < 2:
        return p
    cwd_path = Path(cwd)
    if parts[0] == cwd_path.name:
        return Path(*parts[1:]).as_posix()
    cwd_norm = str(cwd_path).replace("\\", "/").rstrip("/")
    if cwd_norm.endswith("/" + parts[0]):
        return Path(*parts[1:]).as_posix()
    return p


def strip_path_through_cwd_segment(path_str: str, cwd: str) -> str:
    """When CWD is backend/, drop .../backend/ prefix from WM-stored paths."""
    p = normalize_ledger_path(path_str)
    if not p or not cwd:
        return p
    from pathlib import Path

    cwd_name = Path(cwd).name
    parts = Path(p).parts
    if cwd_name in parts:
        idx = parts.index(cwd_name)
        return Path(*parts[idx + 1:]).as_posix() if idx + 1 < len(parts) else p
    return p


_MUST_READ_SCAFFOLD_SUFFIX = (
    "\nIf bash/npm/pnpm scaffolded this file, read() it once, then write/edit."
)


def append_must_read_scaffold_hint(message: str) -> str:
    if not message or "MUST READ FIRST" not in message:
        return message
    if _MUST_READ_SCAFFOLD_SUFFIX.strip() in message:
        return message
    return message + _MUST_READ_SCAFFOLD_SUFFIX


@dataclass
class FileIndexEntry:
    """Derived provenance for one file path."""

    path: str
    creator_delegate: int | None = None
    creator_wave: int | None = None
    creator_role: str = "agent"
    last_delegate: int | None = None
    last_wave: int | None = None
    last_role: str = "agent"
    edit_count: int = 0
    last_ts: str = ""


@dataclass
class FileLedgerIndex:
    """In-memory index rebuilt from ledger JSONL."""

    entries: dict[str, FileIndexEntry] = field(default_factory=dict)

    def apply_entry(self, entry: dict[str, Any]) -> None:
        """Incrementally update index from one ledger record."""
        raw_path = entry.get("path", "")
        path = normalize_ledger_path(raw_path)
        if not path or path.startswith("{"):
            return
        author = entry.get("author") or {}
        role = author.get("role", "agent")
        delegate = author.get("delegate_index")
        wave = author.get("wave")
        ts = entry.get("ts", "")
        rec = self.entries.get(path)
        if rec is None:
            rec = FileIndexEntry(path=path)
            rec.creator_role = role
            rec.creator_delegate = delegate if role == "delegate" else None
            rec.creator_wave = wave
            self.entries[path] = rec
        rec.edit_count += 1
        rec.last_role = role
        rec.last_delegate = delegate if role == "delegate" else None
        rec.last_wave = wave
        rec.last_ts = ts

    def rebuild(self, history: list[dict[str, Any]]) -> None:
        self.entries.clear()
        for entry in history:
            self.apply_entry(entry)


# ---------------------------------------------------------------------------
# FileLedger
# ---------------------------------------------------------------------------


class FileLedger:
    """Append-only JSONL ledger recording file changes with unified diffs."""

    def __init__(self, ledger_path: Path) -> None:
        self._path = ledger_path
        self._lock = threading.Lock()
        self._index = FileLedgerIndex()
        self._read_index: Any | None = None
        self._wave_registry: dict[int, dict[int, list[str]]] = {}
        self._active_wave: int | None = None
        self._shared_paths: set[str] = set(_SHARED_PATHS)
        self._released_delegates: dict[int, set[int]] = {}
        self._project_dir_prefix: str = ""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.refresh_index()

    def refresh_index(self) -> None:
        """Rebuild the in-memory index from the JSONL file."""
        with self._lock:
            self._index.rebuild(self.history(n=10_000))

    def set_wave_ownership(
        self,
        wave: int,
        delegate_paths: dict[int, list[str]],
        *,
        shared_paths: list[str] | None = None,
        project_dir: str | None = None,
    ) -> None:
        """Register path patterns per delegate for a parallel wave."""
        with self._lock:
            self._wave_registry[wave] = {
                k: [normalize_ledger_path(p) for p in v]
                for k, v in delegate_paths.items()
            }
            self._active_wave = wave
            self._released_delegates.pop(wave, None)
            if project_dir is not None:
                self._project_dir_prefix = normalize_ledger_path(project_dir)
            if shared_paths is not None:
                self._shared_paths = {
                    normalize_ledger_path(p) for p in shared_paths
                }

    def clear_active_wave(self, wave: int | None = None) -> None:
        with self._lock:
            if wave is None:
                self._active_wave = None
            elif self._active_wave == wave:
                self._active_wave = None
            if wave is not None:
                self._released_delegates.pop(wave, None)

    def release_delegate_ownership(self, wave: int, delegate: int) -> None:
        """Drop exclusive path scope when a delegate finishes their task."""
        with self._lock:
            self._released_delegates.setdefault(wave, set()).add(delegate)
            wave_reg = self._wave_registry.get(wave)
            if wave_reg is not None:
                wave_reg.pop(delegate, None)

    def grant_delegate_paths(
        self,
        wave: int,
        delegate: int,
        paths: list[str],
    ) -> list[str]:
        """Append path patterns to a delegate's wave scope (orchestrator grant)."""
        granted: list[str] = []
        with self._lock:
            wave_reg = self._wave_registry.setdefault(wave, {})
            existing = wave_reg.setdefault(delegate, [])
            seen = {normalize_ledger_path(p) for p in existing}
            for raw in paths:
                norm = normalize_ledger_path(raw)
                if not norm or norm in seen:
                    continue
                existing.append(norm)
                seen.add(norm)
                granted.append(norm)
        return granted

    def delegate_covers_paths(
        self,
        wave: int,
        delegate: int,
        paths: list[str],
    ) -> bool:
        """True when every requested path is already in the delegate's wave scope."""
        if not paths:
            return False
        with self._lock:
            patterns = self._wave_registry.get(wave, {}).get(delegate, [])
            if not patterns:
                return False
            for raw in paths:
                norm = normalize_ledger_path(raw)
                if not norm:
                    return False
                if not any(self._path_matches_pattern(norm, p) for p in patterns):
                    return False
        return True

    def set_delegate_paths(
        self,
        wave: int,
        delegate: int,
        paths: list[str],
    ) -> None:
        """Replace a delegate's wave path list (e.g. after plan owned_paths update)."""
        with self._lock:
            wave_reg = self._wave_registry.setdefault(wave, {})
            wave_reg[delegate] = [
                norm
                for p in paths
                if (norm := normalize_ledger_path(p))
            ]

    def get_index_entry(self, path: str) -> FileIndexEntry | None:
        norm = normalize_ledger_path(path)
        return self._index.entries.get(norm)

    def _scope_relative_path(self, path: str) -> str:
        """Path relative to project_dir when delegate CWD is inside it."""
        norm = normalize_ledger_path(path)
        pd = self._project_dir_prefix
        if not norm or not pd:
            return norm
        if norm == pd:
            return ""
        prefix = f"{pd}/"
        if norm.startswith(prefix):
            return norm[len(prefix):]
        return norm

    def _path_matches_pattern(self, path: str, pattern: str) -> bool:
        norm = self._scope_relative_path(path)
        pat = normalize_ledger_path(pattern)
        if not norm or not pat:
            return False
        if norm == pat:
            return True
        if pat.endswith("/"):
            return norm.startswith(pat)
        return norm.startswith(pat.rstrip("/") + "/")

    def _delegate_owns_path(
        self,
        delegate: int,
        path: str,
        wave: int | None,
    ) -> bool:
        if wave is None:
            return True
        owners = self._wave_registry.get(wave, {})
        if delegate not in owners:
            return False
        patterns = owners.get(delegate, [])
        if not patterns:
            return False
        return any(self._path_matches_pattern(path, p) for p in patterns)

    def _teammate_scope_owner(
        self,
        delegate: int,
        path: str,
        wave: int | None,
    ) -> int | None:
        """Delegate # that owns this path pattern, if not the current author."""
        if wave is None:
            return None
        for other, patterns in self._wave_registry.get(wave, {}).items():
            if other == delegate:
                continue
            if any(self._path_matches_pattern(path, p) for p in patterns):
                return other
        return None

    def check_mutation_allowed(
        self,
        path: str,
        author: dict[str, Any],
        *,
        file_exists: bool,
    ) -> str | None:
        """Return an error message if this write/edit should be blocked."""
        norm = normalize_ledger_path(path)
        if not norm or norm.startswith("{"):
            return (
                f"Invalid path: {path!r}. Use a relative path like "
                f"'packages/server/foo.py', not JSON or absolute Windows paths."
            )

        scope_norm = self._scope_relative_path(norm)

        role = author.get("role", "agent")
        delegate = author.get("delegate_index")
        wave = author.get("wave")

        if role == "orchestrator":
            return None

        _in_my_scope = (
            role == "delegate"
            and delegate is not None
            and wave is not None
            and self._delegate_owns_path(delegate, scope_norm, wave)
        )

        idx = self.get_index_entry(norm)

        if norm in self._shared_paths or scope_norm in self._shared_paths:
            if role == "delegate" and not _in_my_scope:
                # Shared integration files are locked once they exist on disk.
                # Creating them during wave-0 scaffold (file missing) is allowed.
                if not file_exists and idx is None:
                    return None
                return (
                    f"FILE LOCKED: {norm} is a shared integration file. "
                    f"Do not edit it unless it is listed in your assigned "
                    f"owned_paths — implement in your module paths otherwise. "
                    f"Need access? escalate(reason='file_access', paths=['{norm}'], "
                    f"message='why you need this file')."
                )

        _released = self._released_delegates.get(wave or -1, set())
        if (
            role == "delegate"
            and delegate is not None
            and wave is not None
            and self._active_wave == wave
            and idx is not None
            and idx.creator_delegate is not None
            and idx.creator_delegate != delegate
            and idx.creator_wave == wave
            and idx.creator_delegate not in _released
            and file_exists
            and not _in_my_scope
        ):
            return (
                f"FILE OWNED BY TEAMMATE: {norm} was created by delegate "
                f"#{idx.creator_delegate} in wave {wave} and is outside your "
                f"assigned scope. If both steps share this path, add it to "
                f"owned_paths on both plan steps."
            )

        if role == "delegate" and delegate is not None and wave is not None:
            if delegate in _released:
                return (
                    f"DELEGATE COMPLETE: delegate #{delegate} finished wave {wave} — "
                    f"do not edit files. Hand off to the orchestrator or teammates."
                )
            idx = self.get_index_entry(norm)
            if (
                idx is not None
                and idx.creator_delegate is not None
                and idx.creator_delegate in _released
                and idx.creator_delegate != delegate
            ):
                return None
            if self._delegate_owns_path(delegate, scope_norm, wave):
                return None
            teammate = self._teammate_scope_owner(delegate, scope_norm, wave)
            if teammate is not None:
                return (
                    f"PATH IN TEAMMATE'S ASSIGNMENT: {norm} is inside delegate "
                    f"#{teammate}'s exclusive wave-{wave} scope. If both delegates "
                    f"need this file, add the path to owned_paths on both steps."
                )
            if is_scratch_path(scope_norm):
                return None
            if idx is None:
                # Unclaimed on the ledger — first write() wins even when bash/npx
                # created the file on disk before the delegate used write().
                return None
            if idx is not None and idx.creator_delegate == delegate:
                return None
            owners = self._wave_registry.get(wave, {})
            allowed = owners.get(delegate, [])
            hint = ", ".join(allowed[:4]) if allowed else "see [FILE OWNERSHIP]"
            return (
                f"PATH NOT IN YOUR ASSIGNMENT: {norm} is outside your "
                f"wave-{wave} file scope. Your paths: {hint}. "
                f"Scratch files (tmp_*.json, temp/) are OK outside teammate "
                f"directories. Need this file? escalate(reason='file_access', "
                f"paths=['{norm}'], message='why you need it')."
            )

        return None

    def format_path_context(self, path: str, author: dict[str, Any]) -> str:
        """Short provenance blurb for SubCryptex / tool hints."""
        norm = normalize_ledger_path(path)
        lines = [f"[FILE CONTEXT: {norm}]"]
        idx = self.get_index_entry(norm)
        if idx and idx.creator_delegate is not None:
            lines.append(
                f"  Created by delegate #{idx.creator_delegate} "
                f"(wave {idx.creator_wave}); last edit #{idx.last_delegate} "
                f"({idx.edit_count} change(s))."
            )
        elif idx:
            lines.append(f"  {idx.edit_count} recorded change(s); last: {idx.last_role}.")
        else:
            lines.append("  No prior ledger history — new file is OK.")
        delegate = author.get("delegate_index")
        wave = author.get("wave")
        if delegate is not None and wave is not None:
            allowed = self._wave_registry.get(wave, {}).get(delegate, [])
            if allowed:
                lines.append(f"  Your wave-{wave} scope: {', '.join(allowed[:5])}")
        return "\n".join(lines)

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

        norm_path = normalize_ledger_path(file_path)
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": norm_path or file_path,
            "action": action,
            "author": author,
            "stats": {"added": added, "removed": removed},
            "diff": diff_str,
        }
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            with self._lock:
                self._index.apply_entry(entry)
            self._invalidate_read_index(norm_path or file_path)
        except Exception:
            logger.debug("FileLedger.record write failed for %s", file_path, exc_info=True)

    def set_read_index(self, read_index: Any | None) -> None:
        """Optional shared read cache — invalidated on writes."""
        self._read_index = read_index

    def _invalidate_read_index(self, path: str) -> None:
        ri = getattr(self, "_read_index", None)
        if ri is not None and path:
            try:
                ri.invalidate_path(path)
            except Exception:
                logger.debug("ReadIndex invalidation failed for %s", path, exc_info=True)

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

    def __init__(
        self,
        ledger: FileLedger,
        file_state_cache: object | None = None,
        cwd: str = "",
        shared_cwd: object | None = None,
    ) -> None:
        self._ledger = ledger
        self._file_state_cache = file_state_cache
        self._cwd = cwd
        self._shared_cwd = shared_cwd

    @property
    def _effective_cwd(self) -> str:
        if self._shared_cwd is not None:
            return str(self._shared_cwd)
        return self._cwd

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

        if path and self._file_state_cache is not None:
            from .file_ledger import normalize_ledger_path
            from .write import _resolve_path

            norm = normalize_ledger_path(path) or path
            resolved = _resolve_path(norm, self._effective_cwd)
            if resolved.exists():
                self._file_state_cache.record(str(resolved.resolve()))

        return ToolResult(
            content=self._ledger.format_summary(path, detail, n),
            details={"ledger_path": str(self._ledger._path)},
        )
