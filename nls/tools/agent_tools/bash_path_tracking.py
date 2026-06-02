"""Infer filesystem paths touched by bash for FileStateCache bootstrap."""

from __future__ import annotations

import re
import time
from pathlib import Path

_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".turbo",
})

# Only scan mtimes for commands likely to scaffold many new files.
_SCAFFOLD_MTIME_SCAN = re.compile(
    r"npm\s+create|npx\s+create-|gh\s+repo\s+create|git\s+clone|"
    r"alembic\s+init|vite@|create-vite|create-react-app",
    re.IGNORECASE,
)


def resolve_path(raw: str, cwd: str) -> Path | None:
    raw = raw.strip().strip('"').strip("'")
    if not raw or raw in (".", "..", "|", "nul"):
        return None
    if raw.startswith(("http://", "https://")):
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = Path(cwd) / p
    try:
        return p.resolve()
    except OSError:
        return None


def extract_paths_from_command(command: str, cwd: str) -> list[Path]:
    """Best-effort parse of file/dir paths a bash command may create or touch."""
    paths: list[Path] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        p = resolve_path(raw, cwd)
        if p is None:
            return
        key = str(p)
        if key not in seen:
            seen.add(key)
            paths.append(p)

    for m in re.finditer(r"(?:^|[\s|])>\s*(['\"]?)([^\s'\"|;&]+)\1", command):
        add(m.group(2))
    for m in re.finditer(r"(?:^|[\s|])>>\s*(['\"]?)([^\s'\"|;&]+)\1", command):
        add(m.group(2))

    for m in re.finditer(
        r"(?:Out-File|Set-Content)\s+(?:-Path\s+)?(['\"]?)([^\s'\"|;&]+)\1",
        command,
        re.IGNORECASE,
    ):
        add(m.group(2))

    for m in re.finditer(r"\btouch\s+([^\s|;&]+)", command):
        add(m.group(1))

    m = re.search(
        r"(?:npm\s+create\s+\S+|npx\s+(?:create-[\w-]+|@[\w/-]+/create[\w-]*)(?:\s+\S+)*)"
        r"\s+([\w./-]+)(?:\s|$|--)",
        command,
        re.IGNORECASE,
    )
    if m:
        add(m.group(1))

    m = re.search(r"\bgh\s+repo\s+create\s+([\w.-]+)", command, re.IGNORECASE)
    if m:
        add(m.group(1))

    for m in re.finditer(r"New-Item[^|]*-Path\s+([^\s|;&]+)", command, re.IGNORECASE):
        add(m.group(1))

    for m in re.finditer(r"\b(?:mkdir|md)\s+(?:-p\s+)?([^\s|;&]+)", command):
        add(m.group(1))

    return paths


def scan_recently_modified(
    root: str,
    since: float,
    *,
    max_files: int = 120,
    max_depth: int = 6,
) -> list[Path]:
    """Return files under *root* with mtime >= *since* (bounded walk)."""
    root_path = Path(root)
    if not root_path.is_dir():
        return []
    found: list[Path] = []
    cutoff = since - 0.25

    def walk(dir_path: Path, depth: int) -> None:
        if len(found) >= max_files or depth > max_depth:
            return
        try:
            entries = list(dir_path.iterdir())
        except OSError:
            return
        for entry in entries:
            if len(found) >= max_files:
                return
            if entry.is_dir():
                if entry.name in _SKIP_DIRS:
                    continue
                walk(entry, depth + 1)
            elif entry.is_file():
                try:
                    if entry.stat().st_mtime >= cutoff:
                        found.append(entry.resolve())
                except OSError:
                    pass

    walk(root_path, 0)
    return found


def record_bash_paths(
    cache: object,
    command: str,
    cwd: str,
    *,
    started_at: float | None = None,
) -> int:
    """Record paths in FileStateCache after a successful bash command."""
    record = getattr(cache, "record", None)
    if record is None:
        return 0

    n = 0
    recorded: set[str] = set()

    def _record(path: Path) -> None:
        nonlocal n
        key = str(path)
        if key in recorded:
            return
        recorded.add(key)
        record(key)
        n += 1

    for p in extract_paths_from_command(command, cwd):
        if p.is_file():
            _record(p)
        elif p.is_dir():
            try:
                for child in p.rglob("*"):
                    if len(recorded) >= 80:
                        break
                    if child.is_file() and child.name not in _SKIP_DIRS:
                        if all(part not in _SKIP_DIRS for part in child.parts):
                            _record(child.resolve())
            except OSError:
                pass

    if started_at is not None and _SCAFFOLD_MTIME_SCAN.search(command):
        for p in scan_recently_modified(cwd, started_at):
            _record(p)

    return n
