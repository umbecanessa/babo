"""File System endpoints for the NLS IDE.

Provides REST access to the server filesystem so the Angular
frontend can browse, read, write, and search files without
requiring the Electron shell.

Reuses the existing tool executors from ``nls.engine.tools_builtin``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from nls.engine.tools_builtin import (
    FileEditTool,
    FileReadTool,
    FileSearchTool,
    FileTreeTool,
    FileWriteTool,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fs", tags=["filesystem"])

# Singleton tool instances (stateless, safe to share)
_file_read = FileReadTool()
_file_write = FileWriteTool()
_file_edit = FileEditTool()
_file_tree = FileTreeTool()
_file_search = FileSearchTool()


# ── Request models ────────────────────────────────────────────────


class FileWriteRequest(BaseModel):
    path: str
    content: str
    append: bool = False


class FileEditRequest(BaseModel):
    path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class MkdirRequest(BaseModel):
    path: str
    recursive: bool = True


class FileWriteBytesRequest(BaseModel):
    path: str
    content_base64: str


class UnlinkRequest(BaseModel):
    path: str


class RenameRequest(BaseModel):
    old_path: str
    new_path: str


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/tree")
async def get_tree(
    path: str = Query(..., description="Directory path"),
    depth: int = Query(default=3, ge=1, le=10, description="Max recursion depth"),
    glob: str = Query(default="", description="Glob filter"),
):
    """List directory structure."""
    result = _file_tree.execute({"path": path, "depth": depth, "glob": glob})
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return {
        "text": result.text,
        "metadata": result.metadata,
    }


@router.get("/read")
async def read_file(
    path: str = Query(..., description="Absolute file path"),
    offset: int = Query(default=0, ge=0, description="Start line (1-based)"),
    limit: int = Query(default=0, ge=0, description="Max lines to read"),
):
    """Read a file with optional line range."""
    result = _file_read.execute({"path": path, "offset": offset, "limit": limit})
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return {
        "content": result.text,
        "metadata": result.metadata,
    }


@router.post("/write")
async def write_file(body: FileWriteRequest):
    """Write or create a file."""
    result = _file_write.execute({
        "path": body.path,
        "content": body.content,
        "append": body.append,
    })
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return {
        "message": result.text,
        "metadata": result.metadata,
    }


@router.post("/mkdir")
async def mkdir(body: MkdirRequest):
    """Create a directory."""
    from pathlib import Path as P

    if ".." in body.path.replace("\\", "/").split("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    dir_path = P(body.path)
    try:
        dir_path.mkdir(parents=body.recursive, exist_ok=True)
    except FileExistsError:
        if not dir_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Path exists and is not a directory: {body.path}")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"message": "Directory created", "path": str(dir_path)}


@router.post("/write-bytes")
async def write_file_bytes(body: FileWriteBytesRequest):
    """Write binary file content from base64."""
    import base64
    from pathlib import Path as P

    if ".." in body.path.replace("\\", "/").split("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    file_path = P(body.path)
    try:
        data = base64.b64decode(body.content_base64)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "message": "File written",
        "metadata": {"path": str(file_path), "size": len(data), "append": False},
    }


@router.post("/unlink")
async def unlink_path(body: UnlinkRequest):
    """Delete a file or directory."""
    import shutil
    from pathlib import Path as P

    if ".." in body.path.replace("\\", "/").split("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    target = P(body.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {body.path}")

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"message": "Deleted", "path": str(target)}


@router.post("/rename")
async def rename_path(body: RenameRequest):
    """Rename or move a file or directory."""
    from pathlib import Path as P

    for raw in (body.old_path, body.new_path):
        if ".." in raw.replace("\\", "/").split("/"):
            raise HTTPException(status_code=400, detail="Invalid path")

    old_path = P(body.old_path)
    new_path = P(body.new_path)
    if not old_path.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {body.old_path}")

    try:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
    except FileExistsError:
        raise HTTPException(status_code=400, detail=f"Destination already exists: {body.new_path}")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"message": "Renamed", "path": str(new_path)}


@router.post("/edit")
async def edit_file(body: FileEditRequest):
    """Perform surgical text replacement in a file."""
    result = _file_edit.execute({
        "path": body.path,
        "old_string": body.old_string,
        "new_string": body.new_string,
        "replace_all": body.replace_all,
    })
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return {
        "message": result.text,
        "metadata": result.metadata,
    }


@router.get("/search")
async def search_files(
    pattern: str = Query(..., description="Search pattern (regex)"),
    path: str = Query(..., description="Directory to search in"),
    glob: str = Query(default="", description="File glob filter"),
    max_results: int = Query(default=50, ge=1, le=500),
):
    """Search file contents using ripgrep or Python fallback."""
    result = _file_search.execute({
        "pattern": pattern,
        "path": path,
        "glob": glob,
        "max_results": max_results,
    })
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return {
        "text": result.text,
        "metadata": result.metadata,
    }


@router.get("/readdir")
async def read_directory(
    path: str = Query(..., description="Directory path"),
):
    """Read immediate directory contents (for file explorer).

    Returns a flat list of entries with name, isDirectory, and size.
    Skips hidden entries, node_modules, __pycache__, .git.
    """
    from pathlib import Path as P

    dir_path = P(path)
    if not dir_path.exists() or not dir_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    entries = []
    try:
        for entry in sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name.startswith(".") or entry.name in ("node_modules", "__pycache__", ".git"):
                continue
            try:
                size = entry.stat().st_size if entry.is_file() else 0
            except OSError:
                size = 0
            entries.append({
                "name": entry.name,
                "path": str(entry).replace("\\", "/"),
                "isDirectory": entry.is_dir(),
                "size": size,
            })
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    return {"entries": entries, "path": path}
