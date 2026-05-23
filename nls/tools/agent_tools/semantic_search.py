"""Semantic search tool -- Query the workspace codebase by meaning.

Gives the agent the ability to find relevant code/files using natural language
queries rather than exact text matches.

Two backends, selected automatically:
  Local (preferred):
      Loads nomic-ai/nomic-embed-code in-process via sentence-transformers.
      ~137M params, runs on CPU, ~5ms per chunk.  Lazy-loaded on first use
      (same pattern as the Whisper transcription tool).

  Runtime embed fallback (when sentence-transformers not installed locally):
      Proxies embedding requests to the GPU Worker at {runtime_url}/embed.
      The GPU Worker hosts the same nomic-embed-code model with CUDA.

The CodebaseIndex is stored at workspace/.nls_index/ and rebuilt automatically
when files change.

Typical usage by the agent:
    semantic_search(query="where is authentication handled")
    semantic_search(query="how does the agentic loop timeout work", max_results=5)
    semantic_search(query="database connection setup", glob="*.py")
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)

_EMBED_MODEL_ID = "nomic-ai/nomic-embed-code"
_INDEX_BUILD_TIMEOUT_S = 60  # If index takes longer, return partial result
_MAX_RESULTS = 20

# ---------------------------------------------------------------------------
# Lazy singleton — local embed model (mirrors transcribe.py)
# ---------------------------------------------------------------------------

_embed_model: Any = None
_embed_lock = threading.Lock()
_embed_backend: str = ""


def _get_local_embed_model() -> tuple[Any, str]:
    """Lazy-load nomic-embed-code via sentence-transformers on first use."""
    global _embed_model, _embed_backend

    if _embed_model is not None:
        return _embed_model, _embed_backend

    with _embed_lock:
        if _embed_model is not None:
            return _embed_model, _embed_backend

        t0 = time.perf_counter()
        try:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading %s (local)...", _EMBED_MODEL_ID)
            model = SentenceTransformer(
                _EMBED_MODEL_ID,
                trust_remote_code=True,
            )
            _embed_model = model
            _embed_backend = "local"
            elapsed = time.perf_counter() - t0
            logger.info("%s loaded in %.1fs", _EMBED_MODEL_ID, elapsed)
            return _embed_model, _embed_backend

        except ImportError as exc:
            raise ImportError(
                f"sentence-transformers not installed: {exc}. "
                "Run: pip install sentence-transformers"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load {_EMBED_MODEL_ID}: {exc}"
            ) from exc


def _local_embed_fn(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using the local model."""
    model, _ = _get_local_embed_model()
    vectors = model.encode(texts, normalize_embeddings=True)
    return vectors.tolist()


# ---------------------------------------------------------------------------
# Runtime embed proxy (optional remote embedding service)
# ---------------------------------------------------------------------------


async def _proxy_embed(
    texts: list[str],
    runtime_url: str,
    secret: str = "",
) -> list[list[float]]:
    """Forward embedding requests to the GPU Worker at {runtime_url}/embed."""
    import httpx

    url = f"{runtime_url.rstrip('/')}/embed"
    headers: dict[str, str] = {}
    if secret:
        headers["X-GPU-Worker-Secret"] = secret

    payload = {"texts": texts, "batch_size": 32}

    logger.info("Proxying embed (%d texts) to GPU Worker at %s", len(texts), url)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        raise RuntimeError(
            f"GPU Worker /embed failed: {resp.status_code} {resp.text[:200]}"
        )

    data = resp.json()
    embeddings = data.get("embeddings")
    if not embeddings:
        raise RuntimeError(f"GPU Worker /embed returned no embeddings: {data}")

    return embeddings


# ---------------------------------------------------------------------------
# SemanticSearchTool
# ---------------------------------------------------------------------------


class SemanticSearchTool:
    """Search the workspace codebase semantically using natural language.

    Parameters
    ----------
    cwd : str
        Working directory (workspace root for indexing).
    runtime_url : str
        URL of the GPU Worker service for remote embedding fallback.
    gpu_worker_secret : str
        Auth secret for the GPU Worker.
    shared_cwd : object | None
        Shared mutable CWD updated by the bash tool.
    """

    def __init__(
        self,
        cwd: str,
        runtime_url: str = "",
        gpu_worker_secret: str = "",
        shared_cwd: object | None = None,
    ) -> None:
        self._cwd = cwd
        self._workspace_root = cwd
        self._runtime_url = runtime_url
        self._secret = gpu_worker_secret or os.environ.get("NLS_GPU_WORKER_SECRET", "")
        self._shared_cwd = shared_cwd

        # Index lives at workspace/.nls_index/ — workspace-local, gitignore-able
        from nls.tools.codebase_index import CodebaseIndex
        self._index = CodebaseIndex(
            workspace=cwd,
            model_id=_EMBED_MODEL_ID,
        )

        # Background rebuild state
        self._build_thread: threading.Thread | None = None
        self._build_lock = threading.Lock()
        self._build_error: str = ""

    @property
    def _effective_cwd(self) -> str:
        if self._shared_cwd is not None:
            return str(self._shared_cwd)
        return self._cwd

    @property
    def name(self) -> str:
        return "semantic_search"

    @property
    def description(self) -> str:
        return (
            "Search the workspace codebase by meaning using natural language. "
            "Finds relevant code, functions, and files even when you don't know "
            "the exact names or keywords. "
            "Use for questions like 'where is authentication handled', "
            "'how does the retry logic work', 'find database connection setup'. "
            "Complement with grep() for exact-match searches."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language description of the code you're looking for",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Maximum number of results to return (default: 10, max: {_MAX_RESULTS})",
                },
                "glob": {
                    "type": "string",
                    "description": "Glob pattern to restrict results to matching files, e.g. '*.py', '**/*.ts'",
                },
                "rebuild": {
                    "type": "boolean",
                    "description": "Force a full index rebuild before searching (default: false)",
                },
            },
            "required": ["query"],
        }

    def _get_embed_fn(self):
        """Return a synchronous embed function — local or raises ImportError."""
        return _local_embed_fn

    def _ensure_index(
        self,
        force_rebuild: bool = False,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> str | None:
        """Ensure the index is loaded and fresh. Returns an error string or None."""
        with self._build_lock:
            # Already loaded and not stale
            if self._index.is_loaded and not force_rebuild and not self._index.is_stale():
                return None

            # Resolve embed function
            embed_fn = None
            embed_error = None
            try:
                embed_fn = self._get_embed_fn()
            except ImportError as e:
                embed_error = str(e)

            if embed_fn is None and not self._runtime_url:
                return (
                    f"Cannot build index: {embed_error}. "
                    "Install sentence-transformers or configure runtime_url."
                )

            if embed_fn is None:
                # We need a sync wrapper around the async proxy
                # This will be handled in execute() via asyncio
                return "_need_async_embed"

            # Build synchronously (we're in a thread or blocking is OK)
            try:
                t0 = time.perf_counter()
                self._index.load_or_build(embed_fn, force_rebuild=force_rebuild)
                elapsed = time.perf_counter() - t0
                logger.info(
                    "SemanticSearch: index ready (%d chunks, %.1fs)",
                    self._index.chunk_count, elapsed,
                )
                return None
            except Exception as e:
                return f"Index build failed: {e}"

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        query = params.get("query", "").strip()
        if not query:
            return ToolResult(content="Error: 'query' is required.", is_error=True)

        max_results = min(int(params.get("max_results", 10)), _MAX_RESULTS)
        glob_filter = params.get("glob", "") or None
        force_rebuild = bool(params.get("rebuild", False))

        loop = asyncio.get_running_loop()

        # ── Step 1: ensure the index is built ─────────────────────────────
        # Try local first (in executor to avoid blocking event loop)
        ensure_result = await loop.run_in_executor(
            None,
            lambda: self._ensure_index(force_rebuild=force_rebuild),
        )

        if ensure_result == "_need_async_embed":
            # sentence-transformers not installed — use runtime embed proxy
            ensure_result = await self._ensure_index_via_proxy(
                loop, force_rebuild=force_rebuild,
            )

        if ensure_result is not None:
            return ToolResult(
                content=f"Error: {ensure_result}",
                is_error=True,
            )

        if self._index.chunk_count == 0:
            return ToolResult(
                content=(
                    "Index is empty — no text files found in the workspace. "
                    f"Workspace: {self._workspace_root}"
                ),
            )

        # ── Step 2: embed the query ────────────────────────────────────────
        try:
            embed_fn_available = True
            try:
                _local_embed_fn(["test"])
            except ImportError:
                embed_fn_available = False

            if embed_fn_available:
                query_vec = await loop.run_in_executor(
                    None, lambda: _local_embed_fn([query])[0],
                )
            else:
                vecs = await _proxy_embed([query], self._runtime_url, self._secret)
                query_vec = vecs[0]

        except Exception as e:
            return ToolResult(
                content=f"Error embedding query: {e}",
                is_error=True,
            )

        # ── Step 3: search ────────────────────────────────────────────────
        try:
            results = await loop.run_in_executor(
                None,
                lambda: self._index.search(
                    query_vec, top_k=max_results, glob_filter=glob_filter,
                ),
            )
        except Exception as e:
            return ToolResult(
                content=f"Error during search: {e}",
                is_error=True,
            )

        if not results:
            msg = f"No results found for: {query!r}"
            if glob_filter:
                msg += f" (filtered to: {glob_filter})"
            return ToolResult(content=msg, details={"result_count": 0})

        # ── Step 4: format output ─────────────────────────────────────────
        lines: list[str] = [
            f"Semantic search results for: {query!r}",
            f"({len(results)} result(s), index: {self._index.chunk_count} chunks)\n",
        ]
        for i, r in enumerate(results, 1):
            score_pct = f"{r.score * 100:.0f}%"
            header = f"[{i}] {r.file}:{r.start_line}-{r.end_line}  (score: {score_pct})"
            lines.append(header)
            # Show up to 20 lines of each chunk
            snippet_lines = r.text.splitlines()[:20]
            snippet = "\n".join(snippet_lines)
            if len(r.text.splitlines()) > 20:
                snippet += f"\n... ({len(r.text.splitlines()) - 20} more lines)"
            lines.append(snippet)
            lines.append("")

        return ToolResult(
            content="\n".join(lines),
            details={
                "result_count": len(results),
                "index_chunks": self._index.chunk_count,
                "query": query,
            },
        )

    async def _ensure_index_via_proxy(
        self,
        loop: asyncio.AbstractEventLoop,
        force_rebuild: bool = False,
    ) -> str | None:
        """Build the index using embeddings from the runtime proxy."""
        from nls.tools.codebase_index import _collect_chunks

        with self._build_lock:
            if self._index.is_loaded and not force_rebuild and not self._index.is_stale():
                return None

            # Try loading from disk first (may already be built with proxy)
            if not force_rebuild and self._index._load_from_disk():
                if not self._index.is_stale():
                    return None

            try:
                ws = Path(self._workspace_root)
                chunks = await loop.run_in_executor(None, lambda: _collect_chunks(ws))
                if not chunks:
                    return None

                texts = [c.text for c in chunks]
                batch_size = 32
                all_vecs: list[list[float]] = []

                for i in range(0, len(texts), batch_size):
                    batch = texts[i : i + batch_size]
                    vecs = await _proxy_embed(batch, self._runtime_url, self._secret)
                    all_vecs.extend(vecs)

                import numpy as np
                emb = np.array(all_vecs, dtype=np.float32)
                norms = np.linalg.norm(emb, axis=1, keepdims=True)
                norms = np.where(norms == 0, 1.0, norms)
                emb = emb / norms

                with self._index._lock:
                    self._index._chunks = chunks
                    self._index._embeddings = emb
                    from nls.tools.codebase_index import _build_manifest
                    self._index._manifest = _build_manifest(ws, chunks, _EMBED_MODEL_ID)
                    self._index._loaded = True
                    self._index._save_to_disk()

                logger.info(
                    "SemanticSearch: proxy index built, %d chunks", len(chunks),
                )
                return None

            except Exception as e:
                return f"Proxy index build failed: {e}"


def create_semantic_search_tool(
    cwd: str,
    runtime_url: str = "",
    gpu_worker_secret: str = "",
    shared_cwd: object | None = None,
) -> SemanticSearchTool:
    """Factory: create a semantic_search tool for a working directory."""
    return SemanticSearchTool(
        cwd=cwd,
        runtime_url=runtime_url,
        gpu_worker_secret=gpu_worker_secret,
        shared_cwd=shared_cwd,
    )
