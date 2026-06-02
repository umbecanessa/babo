"""MCPManageTool -- the agent-facing tool for MCP server management.

Actions: ``search``, ``connect``, ``disconnect``, ``list_servers``,
``list_tools``, ``read_resource``.

On ``connect``, new ``MCPToolProxy`` instances are appended to the
shared ``tools`` list so the agentic loop sees them on the next
iteration.  On ``disconnect``, they are removed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)


class MCPManageTool:
    """Agent tool for managing MCP server connections.

    Parameters
    ----------
    manager : MCPClientManager
        The per-agent manager instance.
    tools_ref : list
        A *mutable reference* to the agent's live tools list so we
        can inject/remove MCPToolProxy instances dynamically.
    config_path : Path
        Path to this skill's config.json (for saving server configs).
    skills_dir : Path
        Root skills directory (for scanning ``mcp_servers.json`` files).
    """

    def __init__(
        self,
        manager: Any,
        tools_ref: list[Any],
        config_path: Path,
        skills_dir: Path,
    ) -> None:
        from .manager import MCPClientManager
        self._manager: MCPClientManager = manager
        self._tools_ref = tools_ref
        self._config_path = config_path
        self._skills_dir = skills_dir

    @property
    def name(self) -> str:
        return "mcp_manage"

    @property
    def description(self) -> str:
        return (
            "Manage MCP extensions -- search the registry, connect/disconnect "
            "servers, list available tools, read resources. Connected MCP "
            "servers inject their tools as first-class capabilities."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "search", "connect", "disconnect",
                        "list_servers", "list_tools", "read_resource",
                    ],
                    "description": "The action to perform.",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Search query (for 'search'). Searches saved "
                        "configs, probes local servers, and queries the "
                        "PulseMCP registry."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Server name for connect/disconnect/list_tools/"
                        "read_resource."
                    ),
                },
                "command": {
                    "type": "string",
                    "description": "Binary to launch (stdio transport).",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Arguments for the command.",
                },
                "env": {
                    "type": "object",
                    "description": "Environment variables for the server.",
                },
                "url": {
                    "type": "string",
                    "description": (
                        "HTTP URL for application-hosted MCP servers "
                        "(e.g. http://localhost:8080/mcp)."
                    ),
                },
                "uri": {
                    "type": "string",
                    "description": "Resource URI (for 'read_resource').",
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "")
        try:
            if action == "search":
                return await self._search(params.get("query", ""))
            elif action == "connect":
                return await self._connect(params)
            elif action == "disconnect":
                return await self._disconnect(params.get("name", ""))
            elif action == "list_servers":
                return self._list_servers()
            elif action == "list_tools":
                return self._list_tools(params.get("name", ""))
            elif action == "read_resource":
                return await self._read_resource(
                    params.get("name", ""), params.get("uri", ""),
                )
            else:
                return ToolResult(
                    content=(
                        f"Unknown action '{action}'. "
                        f"Use: search, connect, disconnect, list_servers, "
                        f"list_tools, read_resource"
                    ),
                    is_error=True,
                )
        except Exception as exc:
            return ToolResult(content=f"mcp_manage error: {exc}", is_error=True)

    # -- Actions --------------------------------------------------------------

    async def _search(self, query: str) -> ToolResult:
        if not query:
            return ToolResult(
                content="'query' is required for search.", is_error=True,
            )

        from .discovery import load_saved_servers
        from .registry import MCPServerInfo, search_pulsemcp

        results: list[dict[str, Any]] = []

        saved = load_saved_servers(self._config_path)
        q_lower = query.lower()
        for sname, cfg in saved.items():
            if q_lower in sname.lower() or q_lower in cfg.description.lower():
                reachable: bool | None = None
                if cfg.url:
                    reachable = await self._manager.probe_url(cfg.url)
                info = MCPServerInfo(
                    name=sname,
                    description=cfg.description or f"Previously connected ({cfg.source})",
                    source="saved",
                    reachable=reachable,
                    url=cfg.url,
                    command=cfg.command,
                    args=cfg.args or None,
                )
                results.append(info.to_dict())

        try:
            registry_results = await search_pulsemcp(query, limit=8)
            for r in registry_results:
                results.append(r.to_dict())
        except Exception as exc:
            logger.debug("Registry search failed: %s", exc)

        if not results:
            return ToolResult(
                content=f"No MCP extensions found for '{query}'.",
            )

        lines = [f"MCP extension search results for '{query}':\n"]
        for i, r in enumerate(results, 1):
            src = r.get("source", "")
            badge = {"saved": "[saved]", "local": "[local]", "registry": "[registry]"}.get(src, "")
            reach = ""
            if r.get("reachable") is True:
                reach = " (reachable)"
            elif r.get("reachable") is False:
                reach = " (not reachable)"
            stars = f" ({r['stars']}★)" if r.get("stars") else ""
            lines.append(
                f"{i}. **{r['name']}** {badge}{reach}{stars}\n"
                f"   {r.get('description', '')}"
            )
            if r.get("url"):
                lines.append(f"   URL: {r['url']}")
            if r.get("command"):
                cmd = r["command"]
                args = " ".join(r.get("args", []))
                lines.append(f"   Command: {cmd} {args}".rstrip())
            if r.get("install_command"):
                lines.append(f"   Install: {r['install_command']}")

        lines.append(
            "\nTo connect, use: mcp_manage(action='connect', name='...', "
            "url='...' or command='...')"
        )
        return ToolResult(content="\n".join(lines))

    async def _connect(self, params: dict[str, Any]) -> ToolResult:
        name = params.get("name", "")
        if not name:
            return ToolResult(content="'name' is required.", is_error=True)

        if self._manager.is_connected(name):
            tools = self._manager.list_tools(name)
            return ToolResult(
                content=(
                    f"Server '{name}' is already connected with "
                    f"{len(tools)} tools."
                ),
            )

        url = params.get("url")
        command = params.get("command")

        if not url and not command:
            from .discovery import load_saved_servers
            saved = load_saved_servers(self._config_path)
            cfg = saved.get(name)
            if cfg:
                url = cfg.url
                command = cfg.command
                params.setdefault("args", cfg.args)
                params.setdefault("env", cfg.env)
                params.setdefault("headers", cfg.headers)

        if not url and not command:
            return ToolResult(
                content=(
                    f"Cannot connect to '{name}': provide either 'url' "
                    f"(for HTTP servers) or 'command' (for stdio servers)."
                ),
                is_error=True,
            )

        conn = await self._manager.connect(
            name,
            command=command,
            args=params.get("args"),
            env=params.get("env"),
            url=url,
            headers=params.get("headers"),
        )

        for proxy in conn.proxies:
            self._tools_ref.append(proxy)

        from .discovery import save_server_config
        save_server_config(self._config_path, name, conn.config)

        lines = [f"Connected to '{name}' -- {len(conn.proxies)} tools available:\n"]
        for p in conn.proxies[:20]:
            lines.append(f"  - {p.name}: {p.description}")
        if len(conn.proxies) > 20:
            lines.append(f"  ... and {len(conn.proxies) - 20} more")

        if conn.instructions:
            lines.append(f"\n--- Server instructions ---\n{conn.instructions}")

        if conn.resources:
            lines.append(f"\nResources ({len(conn.resources)}):")
            for r in conn.resources[:10]:
                lines.append(f"  - {r.get('name', r.get('uri', '?'))}: {r.get('description', '')}")

        return ToolResult(content="\n".join(lines))

    async def _disconnect(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(content="'name' is required.", is_error=True)

        if not self._manager.is_connected(name):
            return ToolResult(content=f"Server '{name}' is not connected.")

        proxies = self._manager.get_proxies_for(name)
        proxy_names = {p.name for p in proxies}
        self._tools_ref[:] = [
            t for t in self._tools_ref
            if getattr(t, "name", "") not in proxy_names
        ]

        await self._manager.disconnect(name)
        return ToolResult(content=f"Disconnected from '{name}'.")

    def _list_servers(self) -> ToolResult:
        servers = self._manager.list_servers()
        if not servers:
            return ToolResult(content="No MCP servers connected.")

        lines = ["Connected MCP servers:\n"]
        for s in servers:
            lines.append(
                f"- **{s['name']}**: {s['tools']} tools, "
                f"{s['resources']} resources"
            )
        return ToolResult(content="\n".join(lines))

    def _list_tools(self, server_name: str) -> ToolResult:
        if not server_name:
            all_proxies = self._manager.get_all_proxies()
            if not all_proxies:
                return ToolResult(content="No MCP tools available.")
            lines = ["All MCP tools:\n"]
            for p in all_proxies:
                lines.append(f"- {p.name}: {p.description}")
            return ToolResult(content="\n".join(lines))

        tools = self._manager.list_tools(server_name)
        if not tools:
            return ToolResult(
                content=f"No tools for '{server_name}' (not connected?).",
            )
        lines = [f"Tools for '{server_name}':\n"]
        for t in tools:
            lines.append(f"- {t['name']}: {t['description']}")
        return ToolResult(content="\n".join(lines))

    async def _read_resource(self, server_name: str, uri: str) -> ToolResult:
        if not server_name or not uri:
            return ToolResult(
                content="'name' and 'uri' are required for read_resource.",
                is_error=True,
            )
        content = await self._manager.read_resource(server_name, uri)
        return ToolResult(content=content)
