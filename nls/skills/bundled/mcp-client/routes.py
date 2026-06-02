"""REST routes for the MCP Client skill -- frontend Tools page API.

Mounted at ``/skills/mcp-client/`` by the skill's ``register()`` function.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectRequest(BaseModel):
    name: str
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None


class DisconnectRequest(BaseModel):
    name: str


def _get_adapter(request: Request) -> Any:
    """Retrieve the MCPSkillAdapter from app state."""
    adapter = getattr(request.app.state, "mcp_client_adapter", None)
    if adapter is None:
        raise HTTPException(503, "MCP client skill not initialized")
    return adapter


@router.get("/search")
async def search_extensions(request: Request, q: str = "") -> dict[str, Any]:
    """Unified search: saved configs + PulseMCP registry."""
    if not q:
        raise HTTPException(400, "Query parameter 'q' is required")

    adapter = _get_adapter(request)
    from .registry import search_pulsemcp
    from .discovery import load_saved_servers

    results: list[dict[str, Any]] = []

    saved = load_saved_servers(adapter.config_path)
    q_lower = q.lower()
    browse_all = q.strip() == "*"
    for sname, cfg in saved.items():
        if browse_all or q_lower in sname.lower() or q_lower in cfg.description.lower():
            results.append({
                "name": sname,
                "description": cfg.description or "Previously connected",
                "source": "saved",
                "installed": adapter.manager.is_connected(sname),
                "url": cfg.url,
                "command": cfg.command,
            })

    registry_query = "popular MCP servers" if browse_all else q
    try:
        registry_hits = await search_pulsemcp(registry_query, limit=12)
        for r in registry_hits:
            results.append({
                **r.to_dict(),
                "installed": adapter.manager.is_connected(r.name),
            })
    except Exception:
        pass

    return {"query": q, "results": results}


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """Connected servers and their tool counts."""
    adapter = _get_adapter(request)
    return {"servers": adapter.manager.list_servers()}


@router.post("/connect")
async def connect_server(
    request: Request, body: ConnectRequest,
) -> dict[str, Any]:
    """Connect to an MCP server."""
    adapter = _get_adapter(request)

    url = body.url
    command = body.command
    args = body.args
    env = body.env
    headers = body.headers

    if not url and not command:
        from .discovery import load_saved_servers
        saved = load_saved_servers(adapter.config_path)
        cfg = saved.get(body.name)
        if cfg:
            url = url or cfg.url
            command = command or cfg.command
            args = args or cfg.args or None
            env = env or cfg.env or None
            headers = headers or cfg.headers or None

    try:
        conn = await adapter.manager.connect(
            body.name,
            command=command,
            args=args,
            env=env,
            url=url,
            headers=headers,
        )
        from .discovery import save_server_config
        save_server_config(adapter.config_path, body.name, conn.config)

        return {
            "name": body.name,
            "tools": len(conn.proxies),
            "resources": len(conn.resources),
            "connected": True,
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/disconnect")
async def disconnect_server(
    request: Request, body: DisconnectRequest,
) -> dict[str, Any]:
    """Disconnect from an MCP server."""
    adapter = _get_adapter(request)
    await adapter.manager.disconnect(body.name)
    return {"name": body.name, "connected": False}
