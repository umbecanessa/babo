"""Drive tools -- search, list, read, upload files.

Read operations honour a ``drive_folders`` allowlist when configured.
Upload requires the ``drive.file`` scope (enabled via ``drive_access: read_write``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)


def _not_connected() -> ToolResult:
    return ToolResult(
        content="Error: Google account not connected. Use google_workspace_connect first.",
        is_error=True,
    )


def _folder_filter(folder_allowlist: list[str]) -> str:
    """Build a Drive query fragment restricting to allowed folders."""
    if not folder_allowlist:
        return ""
    parents = " or ".join(f"'{fid}' in parents" for fid in folder_allowlist)
    return f" and ({parents})"


class DriveSearchTool:
    """Search Google Drive files by name or content."""

    def __init__(self, adapter: Any, agent_id: str, folder_allowlist: list[str] | None = None) -> None:
        self._adapter = adapter
        self._agent_id = agent_id
        self._folder_allowlist = folder_allowlist or []

    @property
    def name(self) -> str:
        return "drive_search"

    @property
    def description(self) -> str:
        return (
            "Search the user's PERSONAL Google Drive files by name or "
            "content keywords. Only finds documents the user has stored "
            "in their own Drive. This is NOT a web search engine — it "
            "cannot find addresses, websites, business info, or any "
            "public internet content. Use web_search for that."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (file name, content keywords)",
                },
                "mime_type": {
                    "type": "string",
                    "description": "Filter by MIME type (e.g. 'application/vnd.google-apps.spreadsheet')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results (default 10, max 50)",
                },
            },
            "required": ["query"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        query = params.get("query", "")
        mime_type = params.get("mime_type", "")
        max_results = min(params.get("max_results", 10), 50)

        safe_query = query.replace("\\", "\\\\").replace("'", "\\'")
        drive_q = f"fullText contains '{safe_query}' and trashed = false"
        if mime_type:
            drive_q += f" and mimeType = '{mime_type}'"
        drive_q += _folder_filter(self._folder_allowlist)

        try:
            service = await asyncio.to_thread(flow.build_service, "drive", "v3")
            results = await asyncio.to_thread(
                lambda: service.files().list(
                    q=drive_q,
                    pageSize=max_results,
                    fields="files(id, name, mimeType, modifiedTime, size, parents)",
                ).execute()
            )
            files = results.get("files", [])
            if not files:
                return ToolResult(content=f"No files found for: {query}")

            lines: list[str] = []
            for f in files:
                entry = (
                    f"- {f['name']}\n"
                    f"  ID: {f['id']}\n"
                    f"  Type: {f.get('mimeType', '?')}\n"
                    f"  Modified: {f.get('modifiedTime', '?')}"
                )
                if f.get("size"):
                    entry += f"\n  Size: {int(f['size']):,} bytes"
                lines.append(entry)

            return ToolResult(
                content=f"Found {len(files)} file(s):\n\n" + "\n\n".join(lines),
                details={"count": len(files), "file_ids": [f["id"] for f in files]},
            )
        except Exception as exc:
            return ToolResult(content=f"Drive search failed: {exc}", is_error=True)


class DriveListTool:
    """List contents of a Google Drive folder."""

    def __init__(self, adapter: Any, agent_id: str, folder_allowlist: list[str] | None = None) -> None:
        self._adapter = adapter
        self._agent_id = agent_id
        self._folder_allowlist = folder_allowlist or []

    @property
    def name(self) -> str:
        return "drive_list"

    @property
    def description(self) -> str:
        return "List files in a Google Drive folder. Provide a folder_id or list root."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "folder_id": {
                    "type": "string",
                    "description": "Folder ID to list (default: 'root')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum files (default 20, max 100)",
                },
            },
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        folder_id = params.get("folder_id", "root")
        max_results = min(params.get("max_results", 20), 100)

        if self._folder_allowlist and folder_id not in self._folder_allowlist and folder_id != "root":
            return ToolResult(
                content=f"Access denied: folder {folder_id} is not in the allowlist.",
                is_error=True,
            )

        drive_q = f"'{folder_id}' in parents and trashed = false"

        try:
            service = await asyncio.to_thread(flow.build_service, "drive", "v3")
            results = await asyncio.to_thread(
                lambda: service.files().list(
                    q=drive_q,
                    pageSize=max_results,
                    fields="files(id, name, mimeType, modifiedTime, size)",
                    orderBy="folder,name",
                ).execute()
            )
            files = results.get("files", [])
            if not files:
                return ToolResult(content=f"Folder {folder_id} is empty.")

            lines: list[str] = []
            for f in files:
                is_folder = f.get("mimeType") == "application/vnd.google-apps.folder"
                icon = "[folder]" if is_folder else "[file]"
                entry = f"  {icon} {f['name']}  (ID: {f['id']})"
                if f.get("size"):
                    entry += f"  [{int(f['size']):,} bytes]"
                lines.append(entry)

            return ToolResult(
                content=f"Contents of {folder_id} ({len(files)} items):\n" + "\n".join(lines),
            )
        except Exception as exc:
            return ToolResult(content=f"Drive list failed: {exc}", is_error=True)


class DriveReadTool:
    """Read the content of a Google Drive file."""

    def __init__(self, adapter: Any, agent_id: str, folder_allowlist: list[str] | None = None) -> None:
        self._adapter = adapter
        self._agent_id = agent_id
        self._folder_allowlist = folder_allowlist or []

    @property
    def name(self) -> str:
        return "drive_read"

    @property
    def description(self) -> str:
        return (
            "Read the content of a Google Drive file. Google Docs are exported "
            "as plain text, Sheets as CSV, Slides as plain text. "
            "Binary files return metadata only."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "Drive file ID",
                },
            },
            "required": ["file_id"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        file_id = params.get("file_id", "")
        if not file_id:
            return ToolResult(content="Error: file_id is required", is_error=True)

        try:
            service = await asyncio.to_thread(flow.build_service, "drive", "v3")

            # Get file metadata
            meta = await asyncio.to_thread(
                lambda: service.files().get(
                    fileId=file_id, fields="id,name,mimeType,size,parents",
                ).execute()
            )

            # Check folder allowlist
            if self._folder_allowlist:
                parents = meta.get("parents", [])
                if parents and not any(p in self._folder_allowlist for p in parents):
                    return ToolResult(
                        content="Access denied: this file is outside the allowed folders.",
                        is_error=True,
                    )

            mime = meta.get("mimeType", "")
            name = meta.get("name", "unknown")

            _EXPORT_MAP = {
                "application/vnd.google-apps.document": ("text/plain", "txt"),
                "application/vnd.google-apps.spreadsheet": ("text/csv", "csv"),
                "application/vnd.google-apps.presentation": ("text/plain", "txt"),
            }

            if mime in _EXPORT_MAP:
                export_mime, ext = _EXPORT_MAP[mime]
                content_bytes = await asyncio.to_thread(
                    lambda: service.files().export(
                        fileId=file_id, mimeType=export_mime,
                    ).execute()
                )
                if isinstance(content_bytes, bytes):
                    text = content_bytes.decode("utf-8", errors="replace")
                else:
                    text = str(content_bytes)

                # Truncate very large files
                if len(text) > 50_000:
                    text = text[:50_000] + f"\n\n[... truncated, {len(text)} chars total]"

                return ToolResult(
                    content=f"**{name}** ({mime}):\n\n{text}",
                    details={"file_id": file_id, "format": ext},
                )

            # For non-Google files, try to download text content
            size = int(meta.get("size", 0))
            if size > 10_000_000:
                return ToolResult(
                    content=(
                        f"**{name}** ({mime}, {size:,} bytes)\n\n"
                        "File is too large to read inline. Use the file ID to reference it."
                    ),
                )

            if mime == "application/pdf":
                content_bytes = await asyncio.to_thread(
                    lambda: service.files().get_media(fileId=file_id).execute()
                )
                text = await asyncio.to_thread(_extract_pdf_text, content_bytes)
                if text:
                    if len(text) > 50_000:
                        text = text[:50_000] + f"\n\n[... truncated, full PDF has more content]"
                    return ToolResult(
                        content=f"**{name}** (PDF, {size:,} bytes):\n\n{text}",
                        details={"file_id": file_id, "format": "pdf"},
                    )
                return ToolResult(
                    content=(
                        f"**{name}** (PDF, {size:,} bytes)\n\n"
                        "Could not extract text from this PDF (may be image-based)."
                    ),
                )

            if mime.startswith("text/") or mime in (
                "application/json", "application/xml",
                "application/javascript", "application/csv",
            ):
                content_bytes = await asyncio.to_thread(
                    lambda: service.files().get_media(fileId=file_id).execute()
                )
                text = content_bytes.decode("utf-8", errors="replace") if isinstance(content_bytes, bytes) else str(content_bytes)
                if len(text) > 50_000:
                    text = text[:50_000] + f"\n\n[... truncated]"
                return ToolResult(content=f"**{name}** ({mime}):\n\n{text}")

            return ToolResult(
                content=(
                    f"**{name}**\n"
                    f"Type: {mime}\n"
                    f"Size: {size:,} bytes\n"
                    f"ID: {file_id}\n\n"
                    "This is a binary file. Content cannot be displayed as text."
                ),
            )
        except Exception as exc:
            return ToolResult(content=f"Drive read failed: {exc}", is_error=True)


class DriveUploadTool:
    """Upload a file from the agent's workspace to Google Drive."""

    def __init__(self, adapter: Any, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "drive_upload"

    @property
    def description(self) -> str:
        return (
            "Upload a file from the agent workspace to Google Drive. "
            "The file is uploaded to the specified folder (or root). "
            "Requires drive.file scope — contact admin if uploads fail with a scope error."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file in the agent workspace (e.g. 'reports/q1.pdf')",
                },
                "folder_id": {
                    "type": "string",
                    "description": "Target Drive folder ID (default: root)",
                },
                "name": {
                    "type": "string",
                    "description": "Override the file name on Drive (default: original filename)",
                },
            },
            "required": ["file_path"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        file_path = params.get("file_path", "")
        folder_id = params.get("folder_id", "root")
        override_name = params.get("name", "")

        if not file_path:
            return ToolResult(content="Error: file_path is required", is_error=True)

        try:
            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am is None:
                return ToolResult(content="Cannot resolve workspace", is_error=True)

            workspace = am.agents_dir / self._agent_id / "workspace"
            from pathlib import Path
            resolved = (workspace / file_path).resolve()
            if not str(resolved).startswith(str(workspace.resolve())):
                return ToolResult(content="Error: path traversal blocked", is_error=True)
            if not resolved.is_file():
                return ToolResult(content=f"File not found: {file_path}", is_error=True)

            import mimetypes as _mt
            mime = _mt.guess_type(str(resolved))[0] or "application/octet-stream"
            filename = override_name or resolved.name

            service = await asyncio.to_thread(flow.build_service, "drive", "v3")

            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(resolved), mimetype=mime, resumable=True)
            file_meta: dict[str, Any] = {"name": filename}
            if folder_id and folder_id != "root":
                file_meta["parents"] = [folder_id]

            uploaded = await asyncio.to_thread(
                lambda: service.files().create(
                    body=file_meta, media_body=media, fields="id,name,webViewLink",
                ).execute()
            )

            self._adapter.audit(
                self._agent_id, "drive_upload",
                file_id=uploaded.get("id", ""),
                name=filename,
                folder_id=folder_id,
            )

            link = uploaded.get("webViewLink", "")
            return ToolResult(
                content=(
                    f"Uploaded '{filename}' to Google Drive.\n"
                    f"File ID: {uploaded.get('id', '')}\n"
                    f"Link: {link}"
                ),
                details={"file_id": uploaded.get("id", ""), "link": link},
            )
        except Exception as exc:
            return ToolResult(content=f"Drive upload failed: {exc}", is_error=True)


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes using PyMuPDF (fitz).

    Returns empty string on failure so the caller can fall back to
    a metadata-only response.
    """
    try:
        import fitz  # PyMuPDF -- available via pymupdf
    except ImportError:
        logger.debug("pymupdf not installed; cannot extract PDF text")
        return ""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        pages: list[str] = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return "\n\n".join(pages).strip()
    except Exception as exc:
        logger.debug("PDF text extraction failed: %s", exc)
        return ""
