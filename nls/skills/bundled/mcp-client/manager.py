"""MCPClientManager -- connection lifecycle for MCP servers.

Manages multiple concurrent MCP connections per agent.  Each connection
maintains a ``ClientSession`` with its own transport (stdio subprocess
or streamable-HTTP) and a set of ``MCPToolProxy`` instances.

The manager is a long-lived singleton per agent, surviving across
messages.  Connections persist until explicitly disconnected or the
agent shuts down.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from .proxy import MCPToolProxy

logger = logging.getLogger(__name__)


@dataclass
class MCPConnection:
    """State for one connected MCP server."""

    name: str
    session: Any  # mcp.ClientSession
    exit_stack: AsyncExitStack
    proxies: list[MCPToolProxy] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    instructions: str = ""
    connected_at: float = field(default_factory=time.time)
    config: dict[str, Any] = field(default_factory=dict)


class MCPClientManager:
    """Manages all MCP connections for one agent.

    Parameters
    ----------
    agent_id : str
        Owning agent identifier (for logging).
    """

    def __init__(self, agent_id: str = "") -> None:
        self._agent_id = agent_id
        self._connections: dict[str, MCPConnection] = {}

    # -- Connection lifecycle -------------------------------------------------

    async def connect(
        self,
        name: str,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> MCPConnection:
        """Connect to an MCP server and discover its tools + resources.

        Supports two transport modes:
        - **stdio** (subprocess): provide ``command`` and ``args``
        - **streamable HTTP**: provide ``url``

        Returns the ``MCPConnection`` with proxies and instructions.
        Raises ``RuntimeError`` on failure.
        """
        if name in self._connections:
            raise RuntimeError(f"Server '{name}' is already connected.")

        from mcp import ClientSession

        exit_stack = AsyncExitStack()
        try:
            if url:
                read_stream, write_stream = await self._open_http(
                    exit_stack, url, headers,
                )
            elif command:
                read_stream, write_stream = await self._open_stdio(
                    exit_stack, command, args or [], env,
                )
            else:
                raise RuntimeError(
                    "Either 'url' (HTTP) or 'command' (stdio) is required."
                )

            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            init_result = await session.initialize()
            logger.info(
                "MCP %s: connected (protocol=%s, server=%s)",
                name,
                getattr(init_result, "protocol_version", "?"),
                getattr(
                    getattr(init_result, "server_info", None), "name", "?"
                ),
            )

            proxies = await self._discover_tools(session, name)
            resources = await self._discover_resources(session)
            instructions = await self._fetch_instructions(session, resources)

            config: dict[str, Any] = {"name": name}
            if url:
                config["url"] = url
                if headers:
                    config["headers"] = headers
            else:
                config["command"] = command
                if args:
                    config["args"] = args
                if env:
                    config["env"] = env

            conn = MCPConnection(
                name=name,
                session=session,
                exit_stack=exit_stack,
                proxies=proxies,
                resources=resources,
                instructions=instructions,
                config=config,
            )
            self._connections[name] = conn
            logger.info(
                "MCP %s: %d tools, %d resources discovered",
                name, len(proxies), len(resources),
            )
            return conn

        except Exception:
            await exit_stack.aclose()
            raise

    async def disconnect(self, name: str) -> None:
        """Disconnect from a server and clean up."""
        conn = self._connections.pop(name, None)
        if conn is None:
            return
        try:
            await conn.exit_stack.aclose()
        except Exception as exc:
            logger.debug("MCP %s: cleanup error: %s", name, exc)
        logger.info("MCP %s: disconnected", name)

    async def disconnect_all(self) -> None:
        """Disconnect all servers (shutdown)."""
        names = list(self._connections.keys())
        for name in names:
            await self.disconnect(name)

    # -- Queries --------------------------------------------------------------

    def list_servers(self) -> list[dict[str, Any]]:
        """Return status of all connected servers."""
        result = []
        for name, conn in self._connections.items():
            result.append({
                "name": name,
                "tools": len(conn.proxies),
                "resources": len(conn.resources),
                "connected_since": conn.connected_at,
                "has_instructions": bool(conn.instructions),
            })
        return result

    def list_tools(self, server_name: str) -> list[dict[str, str]]:
        """Return tool info for one server."""
        conn = self._connections.get(server_name)
        if not conn:
            return []
        return [
            {"name": p.name, "description": p.description}
            for p in conn.proxies
        ]

    def get_all_proxies(self) -> list[MCPToolProxy]:
        """All live MCPToolProxy instances across servers."""
        proxies: list[MCPToolProxy] = []
        for conn in self._connections.values():
            proxies.extend(conn.proxies)
        return proxies

    def get_proxies_for(self, server_name: str) -> list[MCPToolProxy]:
        """Proxies for one server."""
        conn = self._connections.get(server_name)
        return list(conn.proxies) if conn else []

    def get_server_instructions(self, server_name: str) -> str:
        """Return server INSTRUCTIONS text (or empty)."""
        conn = self._connections.get(server_name)
        return conn.instructions if conn else ""

    def get_connection(self, name: str) -> MCPConnection | None:
        return self._connections.get(name)

    def is_connected(self, name: str) -> bool:
        return name in self._connections

    @property
    def connected_names(self) -> list[str]:
        return list(self._connections.keys())

    # -- Resource reading -----------------------------------------------------

    async def read_resource(self, server_name: str, uri: str) -> str:
        """Read a single MCP resource by URI."""
        conn = self._connections.get(server_name)
        if not conn:
            raise RuntimeError(f"Server '{server_name}' is not connected.")
        try:
            result = await conn.session.read_resource(uri)
            contents = getattr(result, "contents", None)
            if not contents:
                return "(empty resource)"
            parts = []
            for item in contents:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif hasattr(item, "blob"):
                    parts.append(f"[binary blob, {len(item.blob)} bytes]")
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        except Exception as exc:
            raise RuntimeError(f"Failed to read resource '{uri}': {exc}") from exc

    # -- Probing (reachability check) -----------------------------------------

    @staticmethod
    async def probe_url(url: str, timeout: float = 2.0) -> bool:
        """Check if an HTTP MCP server is reachable."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
                return resp.status_code < 500
        except Exception:
            return False

    # -- Transport helpers ----------------------------------------------------

    async def _open_stdio(
        self,
        stack: AsyncExitStack,
        command: str,
        args: list[str],
        env: dict[str, str] | None,
    ) -> tuple[Any, Any]:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )
        transport = await stack.enter_async_context(stdio_client(params))
        return transport[0], transport[1]

    async def _open_http(
        self,
        stack: AsyncExitStack,
        url: str,
        headers: dict[str, str] | None,
    ) -> tuple[Any, Any]:
        try:
            from mcp.client.streamable_http import streamablehttp_client
            kwargs: dict[str, Any] = {"url": url}
            if headers:
                import httpx
                kwargs["http_client"] = httpx.AsyncClient(headers=headers)
            transport = await stack.enter_async_context(
                streamablehttp_client(**kwargs)
            )
            # May return 2-tuple or 3-tuple depending on SDK version
            return transport[0], transport[1]
        except (ImportError, AttributeError):
            from mcp.client.sse import sse_client
            kwargs_sse: dict[str, Any] = {"url": url}
            if headers:
                kwargs_sse["headers"] = headers
            transport = await stack.enter_async_context(
                sse_client(**kwargs_sse)
            )
            return transport[0], transport[1]

    # -- Discovery helpers ----------------------------------------------------

    async def _discover_tools(
        self, session: Any, server_name: str,
    ) -> list[MCPToolProxy]:
        try:
            result = await session.list_tools()
            tools = getattr(result, "tools", []) or []
        except Exception as exc:
            logger.warning("MCP %s: list_tools failed: %s", server_name, exc)
            return []

        return [
            MCPToolProxy(session=session, server_name=server_name, mcp_tool=t)
            for t in tools
        ]

    async def _discover_resources(self, session: Any) -> list[dict[str, Any]]:
        try:
            result = await session.list_resources()
            resources = getattr(result, "resources", []) or []
            return [
                {
                    "uri": getattr(r, "uri", str(r)),
                    "name": getattr(r, "name", ""),
                    "description": getattr(r, "description", ""),
                }
                for r in resources
            ]
        except Exception:
            return []

    async def _fetch_instructions(
        self, session: Any, resources: list[dict[str, Any]],
    ) -> str:
        """Fetch server prompts / INSTRUCTIONS if available.

        Tries prompts API first (standard MCP approach), then falls
        back to reading an ``instructions`` resource if one exists.
        """
        # Try prompts API (standard MCP server instructions)
        try:
            prompts_result = await session.list_prompts()
            prompts = getattr(prompts_result, "prompts", []) or []
            if prompts:
                first = prompts[0]
                prompt_result = await session.get_prompt(
                    getattr(first, "name", ""),
                )
                messages = getattr(prompt_result, "messages", []) or []
                parts = []
                for msg in messages:
                    content = getattr(msg, "content", None)
                    if hasattr(content, "text"):
                        parts.append(content.text)
                if parts:
                    return "\n".join(parts)
        except Exception:
            pass

        # Fallback: look for instruction-like resources
        _instruction_keywords = {"instruction", "readme", "guide", "help"}
        for res in resources:
            res_name = res.get("name", "").lower()
            if any(kw in res_name for kw in _instruction_keywords):
                try:
                    result = await session.read_resource(res["uri"])
                    contents = getattr(result, "contents", []) or []
                    for item in contents:
                        if hasattr(item, "text") and item.text:
                            return item.text
                except Exception:
                    pass

        return ""
