"""Agent-scoped read index + optional bounded content cache (Tier 1 / Tier 2).

Tier 1: metadata index (path + mtime + size) shared across orchestrator and
delegates.  Tier 2: optional on-disk content cache for large unchanged files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_ledger import normalize_ledger_path

logger = logging.getLogger(__name__)

_TIER1_MIN_BYTES = 2_048
_TIER2_MIN_BYTES = 8_192
_PREVIEW_LINES = 40
_MAX_CACHE_BYTES = 50 * 1024 * 1024
_MAX_CACHE_FILES = 20


@dataclass
class ReadIndexEntry:
    """One recorded read of a path at a content version."""

    path: str
    mtime: float
    size: int
    lines: int = 0
    reader: str = "agent"
    loop_id: str = ""
    cache_key: str = ""
    ts: str = ""
    offset: int = 1
    limit: int | None = None
    max_chars: int | None = None
    content_hash: str = ""

    @property
    def version_key(self) -> str:
        return f"{self.path}:{self.mtime:.6f}:{self.size}"

    @property
    def slice_key(self) -> str:
        lim = self.limit if self.limit is not None else 0
        mc = self.max_chars if self.max_chars is not None else 0
        return f"{self.version_key}:o{self.offset}:l{lim}:m{mc}"


class AgentReadIndex:
    """Shared read provenance + optional content cache for one agent."""

    def __init__(self, agent_dir: Path) -> None:
        self._agent_dir = agent_dir
        self._index_path = agent_dir / "read_index.jsonl"
        self._cache_dir = agent_dir / ".read_cache"
        self._cache_meta_path = self._cache_dir / "index.json"
        self._lock = threading.Lock()
        self._entries: dict[str, ReadIndexEntry] = {}
        self._cache_index: dict[str, dict[str, Any]] = {}
        self._writes_since_compact = 0
        try:
            self._agent_dir.mkdir(parents=True, exist_ok=True)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._load_index()
        self._load_cache_meta()

    # ------------------------------------------------------------------
    # Index persistence
    # ------------------------------------------------------------------

    def _load_index(self) -> None:
        if not self._index_path.exists():
            return
        try:
            with self._index_path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    entry = ReadIndexEntry(
                        path=d.get("path", ""),
                        mtime=float(d.get("mtime", 0)),
                        size=int(d.get("size", 0)),
                        lines=int(d.get("lines", 0)),
                        reader=d.get("reader", "agent"),
                        loop_id=d.get("loop_id", ""),
                        cache_key=d.get("cache_key", ""),
                        ts=d.get("ts", ""),
                        offset=int(d.get("offset", 1)),
                        limit=d.get("limit"),
                        max_chars=d.get("max_chars"),
                        content_hash=d.get("content_hash", ""),
                    )
                    if entry.path:
                        self._entries[entry.slice_key] = entry
        except Exception:
            logger.debug("AgentReadIndex load failed", exc_info=True)

    def _append_index(self, entry: ReadIndexEntry) -> None:
        try:
            with self._index_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "path": entry.path,
                            "mtime": entry.mtime,
                            "size": entry.size,
                            "lines": entry.lines,
                            "reader": entry.reader,
                            "loop_id": entry.loop_id,
                            "cache_key": entry.cache_key,
                            "ts": entry.ts,
                            "offset": entry.offset,
                            "limit": entry.limit,
                            "max_chars": entry.max_chars,
                            "content_hash": entry.content_hash,
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                )
        except Exception:
            logger.debug("AgentReadIndex append failed", exc_info=True)

    def _compact_index_file(self) -> None:
        """Rewrite JSONL to latest entry per slice_key (dedupe on disk)."""
        try:
            with self._lock:
                entries = list(self._entries.values())
            if not entries:
                return
            lines = []
            for entry in entries:
                lines.append(
                    json.dumps(
                        {
                            "path": entry.path,
                            "mtime": entry.mtime,
                            "size": entry.size,
                            "lines": entry.lines,
                            "reader": entry.reader,
                            "loop_id": entry.loop_id,
                            "cache_key": entry.cache_key,
                            "ts": entry.ts,
                            "offset": entry.offset,
                            "limit": entry.limit,
                            "max_chars": entry.max_chars,
                            "content_hash": entry.content_hash,
                        },
                        ensure_ascii=False,
                    ),
                )
            self._index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            logger.debug("AgentReadIndex compact failed", exc_info=True)

    def _load_cache_meta(self) -> None:
        if not self._cache_meta_path.exists():
            return
        try:
            data = json.loads(self._cache_meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._cache_index = data
        except Exception:
            logger.debug("Read cache meta load failed", exc_info=True)

    def _save_cache_meta(self) -> None:
        try:
            self._cache_meta_path.write_text(
                json.dumps(self._cache_index, indent=0),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("Read cache meta save failed", exc_info=True)

    # ------------------------------------------------------------------
    # Content version helpers
    # ------------------------------------------------------------------

    @staticmethod
    def content_version(path: Path) -> tuple[float, int] | None:
        try:
            st = path.stat()
            return st.st_mtime, st.st_size
        except OSError:
            return None

    @staticmethod
    def make_cache_key(path: str, mtime: float, size: int) -> str:
        raw = f"{path}:{mtime:.6f}:{size}"
        return "rc_" + hashlib.sha256(raw.encode()).hexdigest()[:12]

    @staticmethod
    def content_hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Lookup / record
    # ------------------------------------------------------------------

    def lookup(
        self,
        path: str,
        *,
        mtime: float,
        size: int,
        offset: int = 1,
        limit: int | None = None,
        max_chars: int | None = None,
    ) -> ReadIndexEntry | None:
        norm = normalize_ledger_path(path)
        slice_key = ReadIndexEntry(
            path=norm,
            mtime=mtime,
            size=size,
            offset=offset,
            limit=limit,
            max_chars=max_chars,
        ).slice_key
        with self._lock:
            return self._entries.get(slice_key)

    def find_any_version(self, path: str) -> ReadIndexEntry | None:
        """Latest entry for *path* regardless of slice (for cache-hit hints)."""
        norm = normalize_ledger_path(path)
        with self._lock:
            matches = [e for k, e in self._entries.items() if e.path == norm]
        if not matches:
            return None
        return max(matches, key=lambda e: e.ts or "")

    def list_entries(self) -> list[ReadIndexEntry]:
        """All indexed reads (latest slice per key)."""
        with self._lock:
            return list(self._entries.values())

    def record_read(
        self,
        path: str,
        *,
        mtime: float,
        size: int,
        lines: int,
        reader: str,
        loop_id: str = "",
        offset: int = 1,
        limit: int | None = None,
        max_chars: int | None = None,
        full_text: str | None = None,
    ) -> ReadIndexEntry:
        norm = normalize_ledger_path(path)
        cache_key = self.make_cache_key(norm, mtime, size)
        content_hash = ""
        if full_text is not None:
            content_hash = self.content_hash_bytes(
                full_text.encode("utf-8", errors="replace"),
            )
        entry = ReadIndexEntry(
            path=norm,
            mtime=mtime,
            size=size,
            lines=lines,
            reader=reader,
            loop_id=loop_id,
            cache_key=cache_key,
            ts=datetime.now(timezone.utc).isoformat(),
            offset=offset,
            limit=limit,
            max_chars=max_chars,
            content_hash=content_hash,
        )
        with self._lock:
            self._entries[entry.slice_key] = entry
        self._append_index(entry)
        self._writes_since_compact = getattr(self, "_writes_since_compact", 0) + 1
        if self._writes_since_compact >= 50:
            self._compact_index_file()
            self._writes_since_compact = 0

        if full_text is not None and size >= _TIER2_MIN_BYTES:
            self._store_content_cache(cache_key, norm, mtime, size, full_text)

        return entry

    def invalidate_path(self, path: str) -> int:
        """Drop index entries for *path* after a write/edit."""
        norm = normalize_ledger_path(path)
        removed = 0
        with self._lock:
            stale_keys = [k for k, e in self._entries.items() if e.path == norm]
            for k in stale_keys:
                self._entries.pop(k, None)
                removed += 1
            stale_cache = [
                ck for ck, meta in self._cache_index.items()
                if meta.get("path") == norm
            ]
            for ck in stale_cache:
                self._remove_cache_file(ck)
        if removed:
            logger.debug("ReadIndex invalidated %d entries for %s", removed, norm)
        return removed

    # ------------------------------------------------------------------
    # Tier 2 content cache
    # ------------------------------------------------------------------

    def _store_content_cache(
        self,
        cache_key: str,
        path: str,
        mtime: float,
        size: int,
        full_text: str,
    ) -> None:
        try:
            self._evict_cache_if_needed(len(full_text.encode("utf-8")))
            cache_file = self._cache_dir / f"{cache_key}.txt"
            cache_file.write_text(full_text, encoding="utf-8")
            with self._lock:
                self._cache_index[cache_key] = {
                    "path": path,
                    "mtime": mtime,
                    "size": size,
                    "bytes": cache_file.stat().st_size,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            self._save_cache_meta()
        except Exception:
            logger.debug("Content cache store failed for %s", path, exc_info=True)

    def _remove_cache_file(self, cache_key: str) -> None:
        with self._lock:
            meta = self._cache_index.pop(cache_key, None)
        if meta is None:
            return
        try:
            (self._cache_dir / f"{cache_key}.txt").unlink(missing_ok=True)
        except Exception:
            pass
        self._save_cache_meta()

    def _evict_cache_if_needed(self, incoming_bytes: int) -> None:
        with self._lock:
            total = sum(int(m.get("bytes", 0)) for m in self._cache_index.values())
            count = len(self._cache_index)
        while (
            count >= _MAX_CACHE_FILES
            or total + incoming_bytes > _MAX_CACHE_BYTES
        ) and self._cache_index:
            oldest_key = min(
                self._cache_index,
                key=lambda k: self._cache_index[k].get("ts", ""),
            )
            with self._lock:
                meta = self._cache_index.pop(oldest_key, None)
            if meta:
                total -= int(meta.get("bytes", 0))
                count -= 1
                try:
                    (self._cache_dir / f"{oldest_key}.txt").unlink(missing_ok=True)
                except Exception:
                    pass
        self._save_cache_meta()

    def get_cached_slice(
        self,
        path: str,
        *,
        mtime: float,
        size: int,
        offset: int = 1,
        limit: int | None = None,
    ) -> str | None:
        """Return numbered text slice from Tier 2 cache if valid."""
        norm = normalize_ledger_path(path)
        cache_key = self.make_cache_key(norm, mtime, size)
        with self._lock:
            meta = self._cache_index.get(cache_key)
        if meta is None:
            return None
        if abs(float(meta.get("mtime", 0)) - mtime) > 0.001:
            return None
        if int(meta.get("size", -1)) != size:
            return None
        cache_file = self._cache_dir / f"{cache_key}.txt"
        if not cache_file.exists():
            return None
        try:
            text = cache_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        lines = text.splitlines()
        start = max(0, offset - 1)
        end = len(lines) if limit is None else min(len(lines), start + limit)
        selected = lines[start:end]
        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i:6d}|{line}")
        return "\n".join(numbered)

    # ------------------------------------------------------------------
    # Cached read response (Tier 1)
    # ------------------------------------------------------------------

    def format_cache_hit(
        self,
        entry: ReadIndexEntry,
        *,
        current_lines: int,
        preview_lines: list[str] | None = None,
    ) -> str:
        """Short tool response when content version unchanged."""
        lines: list[str] = [
            f"[CACHED READ — content unchanged since {entry.reader} @ {entry.ts[:19]}]",
            f"{entry.path} — {current_lines} lines, {entry.size:,} bytes.",
            f"cache_key={entry.cache_key}",
        ]
        if entry.offset > 1 or entry.limit is not None:
            lines.append(
                f"Prior read used offset={entry.offset}"
                + (f", limit={entry.limit}" if entry.limit else "")
                + ".",
            )
        lines.append(
            "Use read(path, offset=N) to load a section, or read(path, force=true) "
            "to reload from disk.",
        )
        if preview_lines:
            lines.append("")
            lines.append(f"Preview (lines 1-{len(preview_lines)}):")
            lines.extend(preview_lines[:_PREVIEW_LINES])
        return "\n".join(lines)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "index_entries": len(self._entries),
                "cache_files": len(self._cache_index),
                "cache_bytes": sum(
                    int(m.get("bytes", 0)) for m in self._cache_index.values()
                ),
            }


def tier1_eligible(size: int) -> bool:
    return size >= _TIER1_MIN_BYTES
