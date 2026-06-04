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

import json
import logging
import mimetypes
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["files"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB per file
MAX_TOTAL_SIZE = 200 * 1024 * 1024  # 200 MB per request


def _folder_attachment(
    folder_name: str,
    rel_path: str,
    dest: Path,
) -> dict[str, Any]:
    total_size = 0
    file_count = 0
    for item in dest.rglob("*"):
        if item.is_file():
            file_count += 1
            total_size += item.stat().st_size
    return {
        "name": f"{folder_name}/",
        "path": rel_path,
        "size": total_size,
        "mime_type": "application/x-directory",
        "is_folder": True,
        "file_count": file_count,
    }


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


@router.post("/agents/{agent_id}/files/upload-folder")
async def upload_folder(
    request: Request,
    agent_id: str,
    folder_name: str = Form(...),
    relative_paths: str = Form(...),
    files: list[UploadFile] = File(..., description="Files from a dropped folder"),
) -> list[dict[str, Any]]:
    """Upload a folder tree into uploads/{timestamp}_{folder_name}/."""
    if not files:
        raise HTTPException(400, "Folder is empty")

    try:
        paths = json.loads(relative_paths)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "relative_paths must be a JSON array") from exc

    if not isinstance(paths, list) or len(paths) != len(files):
        raise HTTPException(400, "relative_paths must match the number of files")

    workspace = _get_workspace(request, agent_id)
    uploads_dir = workspace / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    safe_folder = (folder_name or "folder").replace("/", "_").replace("\\", "_").strip() or "folder"
    ts = int(time.time() * 1000)
    base_name = f"{ts}_{safe_folder}"
    base_dir = uploads_dir / base_name
    base_dir.mkdir(parents=True, exist_ok=True)

    total_bytes = 0
    for f, rel in zip(files, paths):
        data = await f.read()
        size = len(data)
        total_bytes += size
        if size > MAX_FILE_SIZE:
            raise HTTPException(
                413,
                f"File '{rel}' exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB",
            )
        if total_bytes > MAX_TOTAL_SIZE:
            raise HTTPException(
                413,
                f"Total upload size exceeds {MAX_TOTAL_SIZE // (1024 * 1024)} MB",
            )
        if size == 0:
            continue

        rel_str = str(rel or f.filename or "unnamed").replace("\\", "/").lstrip("/")
        safe_rel = Path(*[p for p in rel_str.split("/") if p and p not in (".", "..")])
        dest = (base_dir / safe_rel).resolve()
        if not str(dest).startswith(str(base_dir.resolve())):
            raise HTTPException(400, "Invalid relative path in folder upload")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    rel_path = f"uploads/{base_name}"
    result = _folder_attachment(safe_folder, rel_path, base_dir)
    logger.info(
        "Agent %s: uploaded folder %s (%d files, %d bytes)",
        agent_id,
        rel_path,
        result["file_count"],
        result["size"],
    )
    return [result]


@router.post("/agents/{agent_id}/files/import-folder")
async def import_folder(
    request: Request,
    agent_id: str,
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    """Copy a local folder into the agent workspace (desktop drag-drop)."""
    source = str(body.get("source_path") or "").strip()
    if not source:
        raise HTTPException(400, "source_path is required")

    src = Path(source)
    if not src.is_dir():
        raise HTTPException(400, f"Not a directory: {source}")

    workspace = _get_workspace(request, agent_id)
    uploads_dir = workspace / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    folder_name = src.name.replace("/", "_").replace("\\", "_") or "folder"
    ts = int(time.time() * 1000)
    base_name = f"{ts}_{folder_name}"
    dest = uploads_dir / base_name

    total_bytes = 0
    for item in src.rglob("*"):
        if item.is_file():
            total_bytes += item.stat().st_size
            if total_bytes > MAX_TOTAL_SIZE:
                raise HTTPException(
                    413,
                    f"Folder exceeds maximum size of {MAX_TOTAL_SIZE // (1024 * 1024)} MB",
                )

    shutil.copytree(src, dest)
    rel_path = f"uploads/{base_name}"
    result = _folder_attachment(folder_name, rel_path, dest)
    logger.info(
        "Agent %s: imported folder %s from %s (%d files, %d bytes)",
        agent_id,
        rel_path,
        source,
        result["file_count"],
        result["size"],
    )
    return [result]


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
