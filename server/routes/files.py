"""File upload / download endpoints for agent workspaces.

Upload:
    POST /agents/{agent_id}/files/upload
        Body: multipart/form-data with one or more "files" fields
        Returns: JSON array of uploaded file metadata

Download:
    GET /agents/{agent_id}/files/download?path=<relative-path>
        Returns: binary file with correct Content-Type
"""

from __future__ import annotations

import logging
import mimetypes
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["files"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB per file
MAX_TOTAL_SIZE = 200 * 1024 * 1024  # 200 MB per request


def _get_workspace(request: Request, agent_id: str) -> Path:
    """Resolve the workspace directory for an agent."""
    agent_manager = request.app.state.agent_manager
    agents_dir: Path = agent_manager.agents_dir
    workspace = agents_dir / agent_id / "workspace"
    if not workspace.exists():
        raise HTTPException(404, f"Agent '{agent_id}' not found or has no workspace")
    return workspace


def _safe_resolve(workspace: Path, relative_path: str) -> Path:
    """Resolve *relative_path* inside *workspace*, rejecting traversal."""
    resolved = (workspace / relative_path).resolve()
    if not str(resolved).startswith(str(workspace.resolve())):
        raise HTTPException(400, "Path traversal is not allowed")
    return resolved


@router.post("/agents/{agent_id}/files/upload")
async def upload_files(
    request: Request,
    agent_id: str,
    files: list[UploadFile] = File(..., description="One or more files to upload"),
) -> list[dict[str, Any]]:
    """Upload files into the agent's workspace/uploads directory."""
    workspace = _get_workspace(request, agent_id)
    uploads_dir = workspace / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    total_bytes = 0

    for f in files:
        data = await f.read()
        size = len(data)
        total_bytes += size

        if size > MAX_FILE_SIZE:
            raise HTTPException(
                413,
                f"File '{f.filename}' exceeds maximum size of "
                f"{MAX_FILE_SIZE // (1024 * 1024)} MB",
            )
        if total_bytes > MAX_TOTAL_SIZE:
            raise HTTPException(
                413,
                f"Total upload size exceeds {MAX_TOTAL_SIZE // (1024 * 1024)} MB",
            )
        if size == 0:
            continue

        original = f.filename or "unnamed"
        safe_name = original.replace("/", "_").replace("\\", "_")
        ts = int(time.time() * 1000)
        dest_name = f"{ts}_{safe_name}"
        dest = uploads_dir / dest_name
        dest.write_bytes(data)

        mime = f.content_type or mimetypes.guess_type(original)[0] or "application/octet-stream"

        rel_path = f"uploads/{dest_name}"
        results.append({
            "name": original,
            "path": rel_path,
            "size": size,
            "mime_type": mime,
        })
        logger.info(
            "Agent %s: uploaded %s (%d bytes, %s)",
            agent_id, rel_path, size, mime,
        )

    return results


@router.get("/agents/{agent_id}/files/download")
async def download_file(
    request: Request,
    agent_id: str,
    path: str,
) -> FileResponse:
    """Download a file from the agent's workspace."""
    workspace = _get_workspace(request, agent_id)
    resolved = _safe_resolve(workspace, path)

    if not resolved.is_file():
        raise HTTPException(404, f"File not found: {path}")

    mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"

    return FileResponse(
        path=str(resolved),
        media_type=mime,
        filename=resolved.name,
        headers={"Content-Disposition": f'attachment; filename="{resolved.name}"'},
    )
