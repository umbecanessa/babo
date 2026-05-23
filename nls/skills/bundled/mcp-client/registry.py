"""PulseMCP registry client -- search the global MCP ecosystem.

PulseMCP (``api.pulsemcp.com/v0beta``) indexes 8,600+ MCP servers
with a free, no-auth REST API.  This module provides a thin async
wrapper that normalises results into a common ``MCPServerInfo`` format
shared by the agent tool and the frontend REST API.

Results are cached in-memory with a 10-minute TTL to reduce API calls
and improve frontend responsiveness.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.pulsemcp.com/v0beta"

_CACHE_TTL = 600  # 10 minutes
_cache: dict[str, tuple[float, list["MCPServerInfo"]]] = {}


@dataclass
class MCPServerInfo:
    """Normalised descriptor for an MCP server from any source."""

    name: str
    description: str = ""
    github_url: str = ""
    stars: int = 0
    install_command: str = ""
    transport_type: str = ""  # "stdio" | "http" | ""
    source: str = "registry"  # "registry" | "saved" | "local"
    reachable: bool | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "source": self.source,
        }
        if self.github_url:
            d["github_url"] = self.github_url
        if self.stars:
            d["stars"] = self.stars
        if self.install_command:
            d["install_command"] = self.install_command
        if self.transport_type:
            d["transport_type"] = self.transport_type
        if self.reachable is not None:
            d["reachable"] = self.reachable
        if self.url:
            d["url"] = self.url
        if self.command:
            d["command"] = self.command
        if self.args:
            d["args"] = self.args
        return d


async def search_pulsemcp(
    query: str,
    limit: int = 10,
) -> list[MCPServerInfo]:
    """Search PulseMCP for MCP servers matching *query*.

    Returns cached results if available and fresh (10-min TTL).
    Returns an empty list (not an error) if the API is unreachable.
    """
    cache_key = f"{query.lower().strip()}:{limit}"
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return list(cached[1])

    try:
        import httpx
    except ImportError:
        logger.debug("httpx not installed -- PulseMCP search unavailable")
        return _stale_or_empty(cache_key)

    url = f"{_BASE_URL}/servers"
    params = {"q": query, "limit": limit}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code >= 400:
                logger.warning(
                    "PulseMCP search failed: HTTP %d", resp.status_code,
                )
                return _stale_or_empty(cache_key)
            data = resp.json()
    except Exception as exc:
        logger.debug("PulseMCP search error: %s", exc)
        return _stale_or_empty(cache_key)

    results = _parse_results(data, limit)
    _cache[cache_key] = (time.time(), results)
    return results


def _stale_or_empty(cache_key: str) -> list[MCPServerInfo]:
    """Return stale cached results on failure, or empty list."""
    cached = _cache.get(cache_key)
    if cached:
        return list(cached[1])
    return []


def _parse_results(data: Any, limit: int) -> list[MCPServerInfo]:
    servers = data if isinstance(data, list) else data.get("servers", [])
    results: list[MCPServerInfo] = []
    for entry in servers[:limit]:
        name = (
            entry.get("name")
            or entry.get("display_name")
            or entry.get("slug", "unknown")
        )
        desc = entry.get("description") or entry.get("summary", "")
        github = entry.get("github_url") or entry.get("url", "")
        stars = entry.get("github_stars") or entry.get("stars", 0)

        install_cmd = ""
        install = entry.get("install") or entry.get("installation", {})
        if isinstance(install, dict):
            install_cmd = install.get("npm", "") or install.get("pip", "")
        elif isinstance(install, str):
            install_cmd = install

        transport = ""
        if "stdio" in str(entry.get("transport", "")):
            transport = "stdio"
        elif "http" in str(entry.get("transport", "")).lower():
            transport = "http"

        results.append(MCPServerInfo(
            name=name,
            description=desc[:200],
            github_url=github,
            stars=int(stars) if stars else 0,
            install_command=install_cmd,
            transport_type=transport,
            source="registry",
        ))

    return results
