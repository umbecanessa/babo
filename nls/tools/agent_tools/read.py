"""Read tool -- Read file contents with offset/limit and truncation.

Supports text files (with line-numbered output), images (returned as
base64), PDFs, Office documents (Word, Excel, PowerPoint), RTF,
audio (transcription via Whisper), and archives (listing).

Large files are automatically truncated with actionable hints
("use offset=N to continue").
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import logging
import mimetypes
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

from .base import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    ToolResult,
    format_size,
    truncate_head,
)

logger = logging.getLogger(__name__)


def _ensure_import(
    module_name: str,
    pip_name: str | None = None,
) -> ModuleType:
    """Import *module_name*, auto-installing via pip if missing.

    Uses ``sys.executable -m pip install`` so the package lands in the
    same venv the server is running in — not the system Python.
    """
    pip_name = pip_name or module_name
    try:
        return importlib.import_module(module_name)
    except ImportError:
        pass

    logger.info("Auto-installing missing package: %s (pip: %s)", module_name, pip_name)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pip_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except Exception as exc:
        raise ImportError(
            f"Failed to auto-install '{pip_name}': {exc}"
        ) from exc

    importlib.invalidate_caches()
    return importlib.import_module(module_name)

# ── Extension sets ───────────────────────────────────────────────
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
_PDF_EXTENSIONS = {".pdf"}
_WORD_EXTENSIONS = {".docx", ".doc"}
_EXCEL_EXTENSIONS = {".xlsx", ".xls"}
_PPTX_EXTENSIONS = {".pptx", ".ppt"}
_RTF_EXTENSIONS = {".rtf"}
_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".wma", ".webm"}

def _ext(path: Path) -> str:
    return path.suffix.lower()


from .write import _resolve_path  # shared dedup-aware resolver


class ReadTool:
    """Read file contents -- text with line numbers, or images as base64.

    Parameters
    ----------
    cwd : str
        Working directory for resolving relative paths.
    max_lines : int
        Maximum lines to return before truncation.
    max_bytes : int
        Maximum bytes to return before truncation.
    shared_cwd : SharedCWD | None
        Shared mutable CWD holder updated by bash tool.
        When provided, relative paths resolve against this (which
        tracks the agent's actual working directory).
    """

    def __init__(
        self,
        cwd: str,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        shared_cwd: object | None = None,
        file_state_cache: object | None = None,
        read_index: object | None = None,
        reader_label: str = "agent",
        loop_id: str = "",
    ) -> None:
        self._cwd = cwd
        self._workspace_root = cwd
        self._shared_cwd = shared_cwd
        self._max_lines = max_lines
        self._max_bytes = max_bytes
        self._file_state_cache = file_state_cache
        self._read_index = read_index
        self._reader_label = reader_label
        self._loop_id = loop_id

    @property
    def _effective_cwd(self) -> str:
        if self._shared_cwd is not None:
            return str(self._shared_cwd)
        return self._cwd

    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return (
            f"Read the contents of a file. Output is truncated to "
            f"{self._max_lines} lines or {format_size(self._max_bytes)} by default. "
            f"Use offset/limit for large files, or set max_chars to "
            f"override the truncation limit when you need the full content. "
            f"Supports text, PDF, Word (.docx), Excel (.xlsx), PowerPoint "
            f"(.pptx), RTF, audio (transcription), images, and archives. "
            f"Prefer this over bash for reading files — it's faster and "
            f"works across all platforms. Call multiple read() in parallel "
            f"to study several files at once."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (relative or absolute)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed, text files only)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (text files only)",
                },
                "max_chars": {
                    "type": "integer",
                    "description": (
                        "Override the default result truncation limit (text files only). "
                        "Use when you need the FULL content of a large "
                        "file (e.g. a PRD, spec, or config). "
                        "Example: max_chars=50000 for a ~50KB file."
                    ),
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "Bypass read cache and reload from disk. Use when you "
                        "suspect stale cached metadata or need a fresh read."
                    ),
                },
            },
            "required": ["path"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        path_str = params.get("path", "")
        if not path_str:
            return ToolResult(content="Error: 'path' is required.", is_error=True)

        from .tool_path_args import normalize_tool_path_arg

        path_str, path_err = normalize_tool_path_arg(
            path_str, cwd=self._effective_cwd, key="path",
        )
        if path_err:
            return ToolResult(content=path_err, is_error=True)

        path = _resolve_path(path_str, self._effective_cwd)

        if not path.exists():
            # Fallback: if CWD has changed (agent cd'd into a subdir),
            # try resolving against the original workspace root.
            if (
                not Path(path_str).is_absolute()
                and self._effective_cwd != self._workspace_root
            ):
                ws_path = _resolve_path(path_str, self._workspace_root)
                if ws_path.exists():
                    path = ws_path
            # Also try dropping a redundant first component
            if not path.exists():
                parts = Path(path_str).parts
                if len(parts) > 1:
                    stripped = str(Path(*parts[1:]))
                    alt = _resolve_path(stripped, self._effective_cwd)
                    if alt.exists():
                        path = alt
                    else:
                        alt2 = _resolve_path(stripped, self._workspace_root)
                        if alt2.exists():
                            path = alt2

            # Fallback: check uploads/ directory for timestamp-prefixed files.
            # Uploaded files are stored as uploads/<timestamp>_<original_name>.
            if not path.exists():
                fname = Path(path_str).name
                for _uploads_root in (self._effective_cwd, self._workspace_root):
                    uploads_dir = Path(_uploads_root) / "uploads"
                    if uploads_dir.is_dir():
                        matches = [
                            f for f in uploads_dir.iterdir()
                            if f.is_file() and f.name.endswith(fname)
                        ]
                        if matches:
                            path = matches[0]
                            break

        if not path.exists():
            hint = (
                f"Tip: Use bash(command='ls') to see files in the current directory. "
                f"After cd, use paths relative to the NEW directory."
            )
            return ToolResult(
                content=(
                    f"Error: File not found: {path_str}\n"
                    f"CWD: {self._effective_cwd}\n"
                    f"Workspace root: {self._workspace_root}\n"
                    f"Resolved to: {path}\n"
                    f"{hint}"
                ),
                is_error=True,
            )

        if not path.is_file():
            return ToolResult(
                content=f"Error: Not a file: {path_str}",
                is_error=True,
            )

        if self._file_state_cache is not None:
            self._file_state_cache.record(str(path.resolve()))

        ext = _ext(path)

        if ext in _IMAGE_EXTENSIONS:
            return await self._read_image(path)
        if ext in _PDF_EXTENSIONS:
            return await self._read_pdf(path, params)
        if ext in _WORD_EXTENSIONS:
            return await self._read_docx(path)
        if ext in _EXCEL_EXTENSIONS:
            return await self._read_xlsx(path)
        if ext in _PPTX_EXTENSIONS:
            return await self._read_pptx(path)
        if ext in _RTF_EXTENSIONS:
            return await self._read_rtf(path)
        if ext in _ARCHIVE_EXTENSIONS:
            return await self._read_archive(path)
        if ext in _AUDIO_EXTENSIONS:
            return await self._read_audio(path)

        return await self._read_text(path, params)

    async def _read_image(self, path: Path) -> ToolResult:
        """Read an image file and return base64-encoded content."""
        try:
            data = path.read_bytes()
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            b64 = base64.b64encode(data).decode("ascii")
            size_str = format_size(len(data))
            return ToolResult(
                content=f"[Image: {path.name} ({mime}, {size_str})]",
                details={"image_base64": b64, "mime_type": mime},
            )
        except Exception as e:
            return ToolResult(content=f"Error reading image: {e}", is_error=True)

    async def _read_pdf(
        self, path: Path, params: dict[str, Any],
    ) -> ToolResult:
        """Extract text from a PDF file."""
        try:
            fitz = _ensure_import("pymupdf", "pymupdf")
        except ImportError as exc:
            return ToolResult(
                content=f"Error: Cannot read PDF '{path.name}': {exc}",
                is_error=True,
            )

        try:
            doc = fitz.open(str(path))
            pages: list[str] = []
            for i, page in enumerate(doc, 1):
                text = page.get_text().strip()
                if text:
                    pages.append(f"--- Page {i} ---\n{text}")
            doc.close()

            if not pages:
                return ToolResult(
                    content=(
                        f"PDF '{path.name}' has {len(doc)} pages but "
                        f"no extractable text. The file may be scanned/"
                        f"image-based -- try using the vision tool instead."
                    ),
                )

            full_text = "\n\n".join(pages)
            file_size = format_size(path.stat().st_size)

            truncated, was_truncated, trunc_details = truncate_head(
                full_text, self._max_lines, self._max_bytes,
            )

            header = f"[PDF: {path.name} ({file_size}, {len(pages)} pages)]\n\n"

            if was_truncated:
                truncated += (
                    f"\n\n[Content truncated. PDF has {len(pages)} pages total.]"
                )
                return ToolResult(
                    content=header + truncated,
                    details={"truncation": trunc_details, "pages": len(pages)},
                )

            return ToolResult(
                content=header + full_text,
                details={"pages": len(pages)},
            )
        except Exception as e:
            return ToolResult(
                content=f"Error reading PDF: {e}",
                is_error=True,
            )

    # ── Word (.docx) ──────────────────────────────────────────────

    async def _read_docx(self, path: Path) -> ToolResult:
        """Extract text from a Word document."""
        try:
            docx = _ensure_import("docx", "python-docx")
        except ImportError as exc:
            return ToolResult(
                content=f"Error: Cannot read Word file '{path.name}': {exc}",
                is_error=True,
            )

        try:
            doc = docx.Document(str(path))
            parts: list[str] = []

            for para in doc.paragraphs:
                text = para.text.strip()
                if text:
                    parts.append(text)

            for table in doc.tables:
                rows: list[str] = []
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    rows.append(" | ".join(cells))
                if rows:
                    parts.append("\n[Table]\n" + "\n".join(rows))

            if not parts:
                return ToolResult(
                    content=f"Word document '{path.name}' contains no extractable text.",
                )

            full_text = "\n\n".join(parts)
            file_size = format_size(path.stat().st_size)
            header = f"[Word: {path.name} ({file_size})]\n\n"

            truncated, was_truncated, trunc_details = truncate_head(
                full_text, self._max_lines, self._max_bytes,
            )
            if was_truncated:
                truncated += "\n\n[Content truncated.]"
                return ToolResult(
                    content=header + truncated,
                    details={"truncation": trunc_details},
                )
            return ToolResult(content=header + full_text)
        except Exception as e:
            return ToolResult(content=f"Error reading Word file: {e}", is_error=True)

    # ── Excel (.xlsx) ────────────────────────────────────────────

    async def _read_xlsx(self, path: Path) -> ToolResult:
        """Extract spreadsheet data as CSV-like text."""
        try:
            openpyxl = _ensure_import("openpyxl")
        except ImportError as exc:
            return ToolResult(
                content=f"Error: Cannot read Excel file '{path.name}': {exc}",
                is_error=True,
            )

        try:
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            sheet_names = list(wb.sheetnames)
            parts: list[str] = []

            for sheet_name in sheet_names:
                ws = wb[sheet_name]
                rows: list[str] = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) if c is not None else "" for c in row]
                    rows.append(",".join(cells))
                if rows:
                    parts.append(f"--- Sheet: {sheet_name} ---\n" + "\n".join(rows))

            wb.close()

            if not parts:
                return ToolResult(
                    content=f"Excel file '{path.name}' contains no data.",
                )

            full_text = "\n\n".join(parts)
            file_size = format_size(path.stat().st_size)
            header = (
                f"[Excel: {path.name} ({file_size}, "
                f"{len(sheet_names)} sheet(s))]\n\n"
            )

            truncated, was_truncated, trunc_details = truncate_head(
                full_text, self._max_lines, self._max_bytes,
            )
            if was_truncated:
                truncated += "\n\n[Content truncated. Large spreadsheet.]"
                return ToolResult(
                    content=header + truncated,
                    details={"truncation": trunc_details},
                )
            return ToolResult(content=header + full_text)
        except Exception as e:
            return ToolResult(content=f"Error reading Excel file: {e}", is_error=True)

    # ── PowerPoint (.pptx) ───────────────────────────────────────

    async def _read_pptx(self, path: Path) -> ToolResult:
        """Extract slide text from a PowerPoint presentation."""
        try:
            pptx = _ensure_import("pptx", "python-pptx")
        except ImportError as exc:
            return ToolResult(
                content=f"Error: Cannot read PowerPoint file '{path.name}': {exc}",
                is_error=True,
            )

        try:
            prs = pptx.Presentation(str(path))
            parts: list[str] = []

            for i, slide in enumerate(prs.slides, 1):
                texts: list[str] = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            text = para.text.strip()
                            if text:
                                texts.append(text)
                    if shape.has_table:
                        for row in shape.table.rows:
                            cells = [c.text.strip() for c in row.cells]
                            texts.append(" | ".join(cells))
                if texts:
                    parts.append(f"--- Slide {i} ---\n" + "\n".join(texts))

            if not parts:
                return ToolResult(
                    content=f"PowerPoint '{path.name}' contains no extractable text.",
                )

            full_text = "\n\n".join(parts)
            file_size = format_size(path.stat().st_size)
            slide_count = len(prs.slides)
            header = (
                f"[PowerPoint: {path.name} ({file_size}, "
                f"{slide_count} slides)]\n\n"
            )

            truncated, was_truncated, trunc_details = truncate_head(
                full_text, self._max_lines, self._max_bytes,
            )
            if was_truncated:
                truncated += f"\n\n[Content truncated. {slide_count} slides total.]"
                return ToolResult(
                    content=header + truncated,
                    details={"truncation": trunc_details},
                )
            return ToolResult(content=header + full_text)
        except Exception as e:
            return ToolResult(
                content=f"Error reading PowerPoint file: {e}", is_error=True,
            )

    # ── RTF ──────────────────────────────────────────────────────

    async def _read_rtf(self, path: Path) -> ToolResult:
        """Extract plain text from an RTF file."""
        try:
            striprtf_mod = _ensure_import("striprtf.striprtf", "striprtf")
        except ImportError as exc:
            return ToolResult(
                content=f"Error: Cannot read RTF file '{path.name}': {exc}",
                is_error=True,
            )

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            full_text = striprtf_mod.rtf_to_text(raw).strip()

            if not full_text:
                return ToolResult(
                    content=f"RTF file '{path.name}' contains no extractable text.",
                )

            file_size = format_size(path.stat().st_size)
            header = f"[RTF: {path.name} ({file_size})]\n\n"

            truncated, was_truncated, trunc_details = truncate_head(
                full_text, self._max_lines, self._max_bytes,
            )
            if was_truncated:
                truncated += "\n\n[Content truncated.]"
                return ToolResult(
                    content=header + truncated,
                    details={"truncation": trunc_details},
                )
            return ToolResult(content=header + full_text)
        except Exception as e:
            return ToolResult(content=f"Error reading RTF file: {e}", is_error=True)

    # ── Archives (.zip, .tar.gz, etc.) ───────────────────────────

    async def _read_archive(self, path: Path) -> ToolResult:
        """List the contents of an archive file."""
        file_size = format_size(path.stat().st_size)
        name_lower = path.name.lower()

        try:
            if zipfile.is_zipfile(str(path)):
                return self._list_zip(path, file_size)

            if (name_lower.endswith((".tar", ".tar.gz", ".tgz",
                                     ".tar.bz2", ".tar.xz"))):
                return self._list_tar(path, file_size)

            return ToolResult(
                content=(
                    f"Archive '{path.name}' ({file_size}): "
                    f"format not supported for listing. "
                    f"Use bash to extract: e.g. bash(command='7z l \"{path.name}\"')"
                ),
            )
        except Exception as e:
            return ToolResult(
                content=f"Error reading archive: {e}", is_error=True,
            )

    def _list_zip(self, path: Path, file_size: str) -> ToolResult:
        with zipfile.ZipFile(str(path), "r") as zf:
            entries: list[str] = []
            total_size = 0
            for info in zf.infolist():
                sz = info.file_size
                total_size += sz
                entries.append(f"  {format_size(sz):>8s}  {info.filename}")
            header = (
                f"[ZIP: {path.name} ({file_size}, "
                f"{len(entries)} files, "
                f"{format_size(total_size)} uncompressed)]\n\n"
            )
            listing = "\n".join(entries) if entries else "(empty archive)"
            return ToolResult(content=header + listing)

    def _list_tar(self, path: Path, file_size: str) -> ToolResult:
        with tarfile.open(str(path), "r:*") as tf:
            entries: list[str] = []
            total_size = 0
            for member in tf.getmembers():
                if member.isfile():
                    total_size += member.size
                    entries.append(
                        f"  {format_size(member.size):>8s}  {member.name}"
                    )
            header = (
                f"[TAR: {path.name} ({file_size}, "
                f"{len(entries)} files, "
                f"{format_size(total_size)} uncompressed)]\n\n"
            )
            listing = "\n".join(entries) if entries else "(empty archive)"
            return ToolResult(content=header + listing)

    # ── Audio (transcription via Whisper) ────────────────────────

    async def _read_audio(self, path: Path) -> ToolResult:
        """Transcribe an audio file using Whisper."""
        file_size = format_size(path.stat().st_size)

        # Try OpenAI Whisper first, then faster-whisper
        model, backend = None, ""
        try:
            import torch
            import whisper as openai_whisper
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = openai_whisper.load_model("small", device=device)
            backend = "openai-whisper"
        except Exception:
            pass

        if model is None:
            try:
                from faster_whisper import WhisperModel
                try:
                    import ctranslate2
                    cuda_ok = ctranslate2.get_cuda_device_count() > 0
                except Exception:
                    cuda_ok = False
                if cuda_ok:
                    model = WhisperModel("small", device="cuda", compute_type="float16")
                else:
                    model = WhisperModel("small", device="cpu", compute_type="int8")
                backend = "faster-whisper"
            except Exception:
                pass

        if model is None:
            return ToolResult(
                content=(
                    f"Cannot transcribe audio '{path.name}' ({file_size}). "
                    f"No Whisper model available. Install openai-whisper or "
                    f"faster-whisper."
                ),
                is_error=True,
            )

        try:
            if backend == "openai-whisper":
                result = model.transcribe(str(path), language=None)
                text = result["text"].strip()
                language = result.get("language", "unknown")
                duration = 0.0
                segs = result.get("segments", [])
                if segs:
                    duration = segs[-1].get("end", 0.0)
            else:
                segments, info = model.transcribe(
                    str(path), beam_size=5, language=None, vad_filter=True,
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
                language = info.language
                duration = info.duration

            if not text:
                return ToolResult(
                    content=(
                        f"Audio '{path.name}' ({file_size}, "
                        f"{duration:.1f}s): no speech detected."
                    ),
                )

            header = (
                f"[Audio: {path.name} ({file_size}, {duration:.1f}s, "
                f"lang={language})]\n\n"
            )

            truncated, was_truncated, trunc_details = truncate_head(
                text, self._max_lines, self._max_bytes,
            )
            if was_truncated:
                truncated += "\n\n[Transcript truncated.]"
                return ToolResult(
                    content=header + truncated,
                    details={"truncation": trunc_details},
                )
            return ToolResult(content=header + text)
        except Exception as e:
            return ToolResult(
                content=f"Error transcribing audio: {e}", is_error=True,
            )

    # ── Plain text (fallback) ────────────────────────────────────

    async def _read_text(
        self, path: Path, params: dict[str, Any],
    ) -> ToolResult:
        """Read a text file with offset/limit and truncation."""
        force = bool(params.get("force"))
        offset = params.get("offset")
        start = 0
        if offset is not None:
            start = max(0, int(offset) - 1)
        limit = params.get("limit")
        _max_chars = params.get("max_chars")
        if _max_chars is not None:
            _max_chars = min(max(1000, int(_max_chars)), 100_000)

        version = None
        if self._read_index is not None:
            from .read_index import tier1_eligible

            version = self._read_index.content_version(path)
            if version is not None and not force and _max_chars is None:
                mtime, size = version
                from .file_ledger import normalize_ledger_path

                norm = normalize_ledger_path(str(path))
                # Tier 2: serve slice from content cache when continuing
                if start > 0 or limit is not None:
                    cached_slice = self._read_index.get_cached_slice(
                        norm,
                        mtime=mtime,
                        size=size,
                        offset=start + 1,
                        limit=int(limit) if limit is not None else None,
                    )
                    if cached_slice is not None:
                        from nls.tools.agent_tools import bump_read_cache_hit
                        bump_read_cache_hit()
                        return ToolResult(
                            content=(
                                f"[CACHE SLICE — {norm}]\n{cached_slice}\n\n"
                                f"[From content cache. Use read(path, force=true) "
                                f"to reload from disk.]"
                            ),
                            details={"read_cache": "tier2_slice", "cache_key": self._read_index.make_cache_key(norm, mtime, size)},
                        )
                # Tier 1: short response when same version already read
                if tier1_eligible(size):
                    prior = self._read_index.lookup(
                        norm,
                        mtime=mtime,
                        size=size,
                        offset=start + 1,
                        limit=int(limit) if limit is not None else None,
                        max_chars=_max_chars,
                    )
                    if prior is not None:
                        preview = None
                        line_count = prior.lines
                        try:
                            raw_peek = path.read_text(encoding="utf-8", errors="replace")
                            line_count = len(raw_peek.split("\n"))
                            preview_lines = raw_peek.split("\n")[:40]
                            preview = [
                                f"{i+1:6d}|{ln}" for i, ln in enumerate(preview_lines)
                            ]
                        except Exception:
                            pass
                        from nls.tools.agent_tools import bump_read_cache_hit
                        bump_read_cache_hit()
                        return ToolResult(
                            content=self._read_index.format_cache_hit(
                                prior,
                                current_lines=line_count,
                                preview_lines=preview,
                            ),
                            details={
                                "read_cache": "tier1_hit",
                                "cache_key": prior.cache_key,
                            },
                        )

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        all_lines = raw.split("\n")
        total_lines = len(all_lines)

        if offset is not None and start >= total_lines:
            return ToolResult(
                content=f"Error: Offset {offset} is beyond end of file ({total_lines} lines total).",
                is_error=True,
            )

        if limit is not None:
            end = min(start + int(limit), total_lines)
        else:
            end = total_lines

        selected = all_lines[start:end]
        start_display = start + 1  # 1-indexed for display

        # Add line numbers
        numbered = []
        for i, line in enumerate(selected, start=start_display):
            numbered.append(f"{i:6d}|{line}")
        content = "\n".join(numbered)

        _eff_max_lines = self._max_lines
        _eff_max_bytes = self._max_bytes
        if _max_chars is not None:
            _eff_max_bytes = _max_chars
            _eff_max_lines = max(self._max_lines, _max_chars // 40)

        truncated_content, was_truncated, trunc_details = truncate_head(
            content, _eff_max_lines, _eff_max_bytes,
        )

        _details: dict[str, Any] = {}
        if _max_chars is not None:
            _details["requested_max_chars"] = _max_chars

        if was_truncated:
            output_lines = trunc_details.get("output_lines", len(selected))
            end_line = start_display + output_lines - 1
            next_offset = end_line + 1
            truncated_content += (
                f"\n\n[Showing lines {start_display}-{end_line} of {total_lines}. "
                f"Use offset={next_offset} to continue.]"
            )
            _details["truncation"] = trunc_details
            self._record_read_index(path, raw, total_lines, start + 1, limit, _max_chars)
            return ToolResult(
                content=truncated_content,
                details=_details,
            )

        if limit is not None and end < total_lines:
            remaining = total_lines - end
            next_offset = end + 1
            content += (
                f"\n\n[{remaining} more lines. Use offset={next_offset} to continue.]"
            )

        self._record_read_index(path, raw, total_lines, start + 1, limit, _max_chars)
        return ToolResult(content=content, details=_details)

    def _record_read_index(
        self,
        path: Path,
        raw: str,
        total_lines: int,
        offset: int,
        limit: int | None,
        max_chars: int | None,
    ) -> None:
        if self._read_index is None:
            return
        version = self._read_index.content_version(path)
        if version is None:
            return
        mtime, size = version
        from .file_ledger import normalize_ledger_path

        norm = normalize_ledger_path(str(path))
        try:
            entry = self._read_index.record_read(
                norm,
                mtime=mtime,
                size=size,
                lines=total_lines,
                reader=self._reader_label,
                loop_id=self._loop_id,
                offset=offset,
                limit=int(limit) if limit is not None else None,
                max_chars=max_chars,
                full_text=raw if size >= 8_192 else None,
            )
            logger.debug(
                "ReadIndex recorded %s cache_key=%s reader=%s",
                norm, entry.cache_key, self._reader_label,
            )
        except Exception:
            logger.debug("ReadIndex record failed for %s", path, exc_info=True)


def create_read_tool(
    cwd: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    shared_cwd: object | None = None,
    file_state_cache: object | None = None,
    read_index: object | None = None,
    reader_label: str = "agent",
    loop_id: str = "",
) -> ReadTool:
    """Factory: create a read tool configured for a working directory."""
    return ReadTool(
        cwd, max_lines, max_bytes, shared_cwd=shared_cwd,
        file_state_cache=file_state_cache,
        read_index=read_index,
        reader_label=reader_label,
        loop_id=loop_id,
    )
