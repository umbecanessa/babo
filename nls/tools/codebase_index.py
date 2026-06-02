"""Codebase Index -- Workspace file chunker and numpy cosine similarity search.

Builds an in-memory (and on-disk) vector index of workspace source files for
semantic search.  No FAISS dependency required -- numpy cosine similarity is
fast enough for codebases up to ~50k chunks (~10k files).

On-disk layout (workspace/.nls_index/):
    embeddings.npy   -- float32 array [N, D]
    chunks.json      -- list of chunk metadata dicts
    manifest.json    -- workspace hash, model id, mtimes for staleness check

Usage::

    from nls.tools.codebase_index import CodebaseIndex

    index = CodebaseIndex(workspace="/path/to/project")

    def embed_fn(texts: list[str]) -> list[list[float]]:
        ...  # call local model or remote endpoint

    if index.is_stale():
        index.build(embed_fn)

    results = index.search(embed_fn(["where is auth handled"])[0], top_k=10)
    for r in results:
        print(r.file, r.start_line, r.score, r.text[:80])
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INDEX_DIR_NAME = ".nls_index"
_EMBEDDINGS_FILE = "embeddings.npy"
_CHUNKS_FILE = "chunks.json"
_MANIFEST_FILE = "manifest.json"

_MAX_CHUNK_CHARS = 1500
_CHUNK_OVERLAP_CHARS = 150
_MAX_FILE_SIZE_BYTES = 512 * 1024  # 512 KB — skip very large files

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".nls_index",
    "dist", "build", ".next", ".nuxt", "coverage", ".turbo",
    "release", "release-mac", "win-unpacked",
})

_TEXT_EXTENSIONS = frozenset({
    # Python
    ".py", ".pyi",
    # JavaScript / TypeScript
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    # Web
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    # Config / data
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
    # Shell
    ".sh", ".bash", ".zsh", ".fish", ".ps1",
    # Docs
    ".md", ".mdx", ".rst", ".txt",
    # Other code
    ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".rb", ".php", ".swift", ".kt", ".scala", ".lua", ".r",
    ".sql", ".graphql", ".proto",
    # Config
    ".xml", ".gradle", ".cmake",
    # Dockerfile / makefile
    "", ".dockerfile",
})

# Regex patterns for function/class boundaries per language
_BOUNDARY_PATTERNS: list[re.Pattern[str]] = [
    # Python: def / class / async def (top-level or indented)
    re.compile(r"^(?:class|def|async\s+def)\s+\w", re.MULTILINE),
    # JS/TS: function/class/arrow fn assignments
    re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\s+\w", re.MULTILINE),
    re.compile(r"^(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\(", re.MULTILINE),
    # Go
    re.compile(r"^func\s+", re.MULTILINE),
    # Rust
    re.compile(r"^(?:pub\s+)?(?:fn|struct|enum|impl|trait)\s+\w", re.MULTILINE),
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ChunkResult:
    """A single search result from the codebase index."""
    file: str
    start_line: int
    end_line: int
    text: str
    score: float


@dataclass
class _Chunk:
    file: str
    start_line: int
    end_line: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "_Chunk":
        return cls(
            file=d["file"],
            start_line=d["start_line"],
            end_line=d["end_line"],
            text=d["text"],
        )


# ---------------------------------------------------------------------------
# File chunking
# ---------------------------------------------------------------------------


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.startswith(".")


def _is_text_file(path: Path) -> bool:
    ext = path.suffix.lower()
    name = path.name.lower()
    # Explicit allowlist
    if ext in _TEXT_EXTENSIONS:
        return True
    # Common config files with no extension
    if name in ("dockerfile", "makefile", "gemfile", "rakefile",
                "procfile", "vagrantfile", "justfile"):
        return True
    return False


def _chunk_text_by_boundary(text: str, rel_path: str) -> list[_Chunk]:
    """Chunk a text file by function/class boundaries with sliding-window fallback."""
    lines = text.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return []

    # Find all boundary line indices (0-based)
    boundaries: list[int] = [0]
    for pat in _BOUNDARY_PATTERNS:
        for m in pat.finditer(text):
            line_no = text.count("\n", 0, m.start())
            boundaries.append(line_no)

    # Deduplicate and sort
    boundaries = sorted(set(boundaries))

    if len(boundaries) <= 1:
        # No boundaries found — fall back to sliding window
        return _chunk_text_sliding(text, rel_path)

    chunks: list[_Chunk] = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else total_lines
        chunk_lines = lines[start:end]
        chunk_text = "\n".join(chunk_lines).strip()

        if not chunk_text:
            continue

        # If the chunk is very large, split it further with sliding window
        if len(chunk_text) > _MAX_CHUNK_CHARS * 2:
            sub = _chunk_text_sliding(
                chunk_text, rel_path, base_line=start,
            )
            chunks.extend(sub)
        else:
            chunks.append(_Chunk(
                file=rel_path,
                start_line=start + 1,
                end_line=min(end, total_lines),
                text=chunk_text[:_MAX_CHUNK_CHARS * 2],
            ))

    return chunks


def _chunk_text_sliding(
    text: str,
    rel_path: str,
    base_line: int = 0,
) -> list[_Chunk]:
    """Sliding-window chunker: ~1500 chars with 150-char overlap."""
    chunks: list[_Chunk] = []
    start_char = 0
    lines_before = text[:0].count("\n")

    while start_char < len(text):
        end_char = min(start_char + _MAX_CHUNK_CHARS, len(text))
        chunk_text = text[start_char:end_char].strip()

        if chunk_text:
            start_line = base_line + text[:start_char].count("\n") + 1
            end_line = base_line + text[:end_char].count("\n") + 1
            chunks.append(_Chunk(
                file=rel_path,
                start_line=start_line,
                end_line=end_line,
                text=chunk_text,
            ))

        if end_char >= len(text):
            break
        start_char = end_char - _CHUNK_OVERLAP_CHARS

    return chunks


def _collect_chunks(workspace: Path) -> list[_Chunk]:
    """Walk the workspace and collect all text chunks."""
    chunks: list[_Chunk] = []
    ws_str = str(workspace.resolve())

    for root, dirs, files in os.walk(workspace):
        # Prune skip dirs in-place
        dirs[:] = [d for d in dirs if not _should_skip_dir(d)]

        for fname in sorted(files):
            fpath = Path(root) / fname
            if not _is_text_file(fpath):
                continue

            try:
                size = fpath.stat().st_size
            except OSError:
                continue

            if size == 0 or size > _MAX_FILE_SIZE_BYTES:
                continue

            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Relative path from workspace root, forward slashes
            try:
                rel = str(fpath.resolve().relative_to(ws_str)).replace("\\", "/")
            except ValueError:
                rel = str(fpath).replace("\\", "/")

            file_chunks = _chunk_text_by_boundary(text, rel)
            chunks.extend(file_chunks)

    return chunks


# ---------------------------------------------------------------------------
# Manifest / staleness
# ---------------------------------------------------------------------------


def _build_manifest(workspace: Path, chunks: list[_Chunk], model_id: str) -> dict[str, Any]:
    """Build a manifest mapping each indexed file to its mtime."""
    file_mtimes: dict[str, float] = {}
    for chunk in chunks:
        if chunk.file not in file_mtimes:
            fpath = workspace / chunk.file
            try:
                file_mtimes[chunk.file] = fpath.stat().st_mtime
            except OSError:
                file_mtimes[chunk.file] = 0.0

    return {
        "model_id": model_id,
        "chunk_count": len(chunks),
        "file_count": len(file_mtimes),
        "file_mtimes": file_mtimes,
    }


def _check_stale(workspace: Path, manifest: dict[str, Any]) -> bool:
    """Return True if any indexed file has changed since the manifest was written."""
    file_mtimes: dict[str, float] = manifest.get("file_mtimes", {})

    # Check for modified or deleted files
    for rel_path, old_mtime in file_mtimes.items():
        fpath = workspace / rel_path
        try:
            current_mtime = fpath.stat().st_mtime
            if abs(current_mtime - old_mtime) > 0.01:
                return True
        except OSError:
            return True  # File deleted

    # Check for new text files (sample a few dirs — full walk is too slow)
    # We do a shallow check: if the total file count in root differs, rebuild
    try:
        root_files = sum(
            1 for f in workspace.iterdir()
            if f.is_file() and _is_text_file(f)
        )
        manifest_root_count = sum(
            1 for p in file_mtimes if "/" not in p
        )
        if root_files != manifest_root_count:
            return True
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# CodebaseIndex
# ---------------------------------------------------------------------------


class CodebaseIndex:
    """Numpy-backed semantic index for a workspace directory.

    Parameters
    ----------
    workspace : str
        Root directory to index.
    index_dir : str | None
        Directory to persist the index (default: workspace/.nls_index/).
    model_id : str
        Embedding model identifier stored in the manifest for invalidation.
    """

    def __init__(
        self,
        workspace: str,
        index_dir: str | None = None,
        model_id: str = "nomic-ai/nomic-embed-code",
    ) -> None:
        self._workspace = Path(workspace).resolve()
        self._index_dir = (
            Path(index_dir) if index_dir
            else self._workspace / _INDEX_DIR_NAME
        )
        self._model_id = model_id

        # In-memory state (loaded from disk or built fresh)
        self._chunks: list[_Chunk] = []
        self._embeddings: Any = None  # numpy array [N, D] or None
        self._manifest: dict[str, Any] = {}
        self._loaded = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_from_disk(self) -> bool:
        """Try to load index from disk. Returns True on success."""
        try:
            import numpy as np
        except ImportError:
            return False

        emb_path = self._index_dir / _EMBEDDINGS_FILE
        chunks_path = self._index_dir / _CHUNKS_FILE
        manifest_path = self._index_dir / _MANIFEST_FILE

        if not (emb_path.exists() and chunks_path.exists() and manifest_path.exists()):
            return False

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("model_id") != self._model_id:
                logger.info("Index model_id mismatch — will rebuild")
                return False

            chunks_raw = json.loads(chunks_path.read_text(encoding="utf-8"))
            embeddings = np.load(str(emb_path))

            if len(chunks_raw) != embeddings.shape[0]:
                logger.warning("Index chunk/embedding count mismatch — will rebuild")
                return False

            self._chunks = [_Chunk.from_dict(c) for c in chunks_raw]
            self._embeddings = embeddings
            self._manifest = manifest
            self._loaded = True
            logger.info(
                "CodebaseIndex: loaded %d chunks from disk (%s)",
                len(self._chunks), self._index_dir,
            )
            return True
        except Exception as e:
            logger.warning("CodebaseIndex: failed to load from disk: %s", e)
            return False

    def _save_to_disk(self) -> None:
        """Persist the current in-memory index to disk."""
        try:
            import numpy as np
        except ImportError:
            return

        try:
            self._index_dir.mkdir(parents=True, exist_ok=True)

            np.save(str(self._index_dir / _EMBEDDINGS_FILE), self._embeddings)

            chunks_path = self._index_dir / _CHUNKS_FILE
            chunks_path.write_text(
                json.dumps([c.to_dict() for c in self._chunks], ensure_ascii=False),
                encoding="utf-8",
            )

            manifest_path = self._index_dir / _MANIFEST_FILE
            manifest_path.write_text(
                json.dumps(self._manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            logger.info(
                "CodebaseIndex: saved %d chunks to %s",
                len(self._chunks), self._index_dir,
            )
        except Exception as e:
            logger.warning("CodebaseIndex: failed to save to disk: %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_stale(self) -> bool:
        """Return True if the on-disk index is missing or outdated."""
        manifest_path = self._index_dir / _MANIFEST_FILE
        if not manifest_path.exists():
            return True

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("model_id") != self._model_id:
                return True
            return _check_stale(self._workspace, manifest)
        except Exception:
            return True

    def build(
        self,
        embed_fn: Callable[[list[str]], list[list[float]]],
        batch_size: int = 32,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Build the index by chunking workspace files and embedding them.

        Parameters
        ----------
        embed_fn : callable
            Function that takes a list of text strings and returns a list of
            embedding vectors (list of lists of floats).
        batch_size : int
            Number of chunks to embed per batch.
        on_progress : callable | None
            Optional ``(done, total) -> None`` callback for progress reporting.
        """
        try:
            import numpy as np
        except ImportError as e:
            raise RuntimeError(
                "numpy is required for CodebaseIndex. Run: pip install numpy"
            ) from e

        with self._lock:
            logger.info(
                "CodebaseIndex: collecting chunks from %s ...", self._workspace,
            )
            chunks = _collect_chunks(self._workspace)
            if not chunks:
                logger.warning("CodebaseIndex: no text files found in %s", self._workspace)
                self._chunks = []
                self._embeddings = np.zeros((0, 1), dtype=np.float32)
                self._manifest = _build_manifest(self._workspace, [], self._model_id)
                self._loaded = True
                self._save_to_disk()
                return

            logger.info("CodebaseIndex: embedding %d chunks ...", len(chunks))
            all_embeddings: list[list[float]] = []

            for i in range(0, len(chunks), batch_size):
                batch = chunks[i : i + batch_size]
                texts = [c.text for c in batch]
                try:
                    vecs = embed_fn(texts)
                    all_embeddings.extend(vecs)
                except Exception as e:
                    logger.error(
                        "CodebaseIndex: embed_fn failed on batch %d-%d: %s",
                        i, i + len(batch), e,
                    )
                    # Fill with zeros so indices stay aligned
                    dim = len(all_embeddings[0]) if all_embeddings else 768
                    all_embeddings.extend([[0.0] * dim] * len(batch))

                if on_progress:
                    on_progress(min(i + batch_size, len(chunks)), len(chunks))

            embeddings_np = np.array(all_embeddings, dtype=np.float32)

            # L2-normalise for cosine similarity via dot product
            norms = np.linalg.norm(embeddings_np, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            embeddings_np = embeddings_np / norms

            self._chunks = chunks
            self._embeddings = embeddings_np
            self._manifest = _build_manifest(self._workspace, chunks, self._model_id)
            self._loaded = True

            self._save_to_disk()
            logger.info(
                "CodebaseIndex: built index with %d chunks, shape %s",
                len(chunks), embeddings_np.shape,
            )

    def load_or_build(
        self,
        embed_fn: Callable[[list[str]], list[list[float]]],
        force_rebuild: bool = False,
    ) -> None:
        """Load from disk if fresh, otherwise build from scratch."""
        with self._lock:
            if not force_rebuild and self._load_from_disk():
                if not self.is_stale():
                    return
                logger.info("CodebaseIndex: index is stale — rebuilding")
            self.build(embed_fn)

    def search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        glob_filter: str | None = None,
    ) -> list[ChunkResult]:
        """Search the index for the most similar chunks.

        Parameters
        ----------
        query_vec : list[float]
            Pre-normalised query embedding vector.
        top_k : int
            Maximum number of results to return.
        glob_filter : str | None
            Optional glob pattern to restrict results to matching file paths.
        """
        try:
            import numpy as np
        except ImportError:
            return []

        with self._lock:
            if not self._loaded or self._embeddings is None or len(self._chunks) == 0:
                return []

            q = np.array(query_vec, dtype=np.float32)
            norm = np.linalg.norm(q)
            if norm > 0:
                q = q / norm

            # Cosine similarity = dot product (embeddings are already normalised)
            scores = self._embeddings @ q  # shape [N]

            # Apply glob filter if requested
            if glob_filter:
                import fnmatch
                mask = np.array([
                    fnmatch.fnmatch(c.file, glob_filter) or
                    fnmatch.fnmatch(c.file, f"**/{glob_filter}")
                    for c in self._chunks
                ], dtype=bool)
                scores = np.where(mask, scores, -1.0)

            top_indices = np.argsort(scores)[::-1][:top_k]

            results: list[ChunkResult] = []
            for idx in top_indices:
                score = float(scores[idx])
                if score < 0:
                    continue
                chunk = self._chunks[int(idx)]
                results.append(ChunkResult(
                    file=chunk.file,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    text=chunk.text,
                    score=score,
                ))

            return results

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def is_loaded(self) -> bool:
        return self._loaded
