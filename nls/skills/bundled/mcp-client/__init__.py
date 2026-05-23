"""MCP Client -- Bundled NLS skill for generic MCP server integration.

Provides:
  - An ``mcp_manage`` agent tool for searching, connecting, and managing
    MCP servers (20,000+ available via PulseMCP registry)
  - Dynamic tool injection: connected MCP server tools appear as
    first-class AgentTools in the agentic loop
  - REST API for the frontend Tools page (unified search, status,
    connect/disconnect)
  - Auto-reconnect across sessions via saved server configs
  - MCP resource reading and server instruction injection

Architecture:
  - MCPClientManager (per-agent): manages connections and their lifecycles
  - MCPToolProxy: wraps individual MCP tools as AgentTools
  - MCPManageTool: the agent-facing gateway tool
  - PulseMCP registry client: searches the global MCP ecosystem
  - Discovery: scans installed skills for mcp_servers.json
"""

from __future__ import annotations

import logging
from typing import Any

from nls.skills import SkillMeta, ConfigField

logger = logging.getLogger(__name__)

meta = SkillMeta(
    name="mcp-client",
    version="0.1",
    description=(
        "Generic MCP client -- connect to any MCP server and gain its "
        "tools as first-class agent capabilities. Search 20,000+ "
        "extensions via PulseMCP."
    ),
    config_schema=[
        ConfigField(
            key="auto_reconnect",
            type="boolean",
            description="Auto-reconnect saved servers on startup",
            default=True,
            scope="agent",
            category="general",
        ),
    ],
)


_shared_manager: Any | None = None


def _get_shared_manager() -> Any:
    """Return the single shared MCPClientManager instance."""
    global _shared_manager
    if _shared_manager is None:
        from .manager import MCPClientManager
        _shared_manager = MCPClientManager(agent_id="shared")
    return _shared_manager


class MCPSkillAdapter:
    """Shared state for the MCP client skill."""

    def __init__(self, app: Any, ctx: Any) -> None:
        self._app = app
        self._ctx = ctx
        self.config_path = ctx.data_dir / "config.json"
        self.skills_dir = ctx._skills_dir

    @property
    def manager(self) -> Any:
        return _get_shared_manager()


def _create_manage_tool(
    adapter: MCPSkillAdapter,
    agent_id: str,
    tools_ref: list[Any] | None = None,
) -> Any:
    """Create an MCPManageTool bound to the given agent."""
    from .manage_tool import MCPManageTool

    manager = _get_shared_manager()

    return MCPManageTool(
        manager=manager,
        tools_ref=tools_ref or [],
        config_path=adapter.config_path,
        skills_dir=adapter.skills_dir,
    )


def register(app: Any, ctx: Any) -> None:
    from .routes import router

    adapter = MCPSkillAdapter(app=app, ctx=ctx)
    app.state.mcp_client_adapter = adapter
    ctx.adapter = adapter

    ctx.include_router(router, prefix="/skills/mcp-client")

    def tool_factory(agent_id: str) -> Any:
        return _create_manage_tool(adapter, agent_id)

    ctx.register_tool_factory(tool_factory)
    ctx.on_startup(_make_startup(adapter))
    ctx.on_shutdown(_make_shutdown())


def _make_startup(adapter: MCPSkillAdapter):
    async def _startup() -> None:
        """Auto-connect saved servers and scan for mcp_servers.json."""
        from .discovery import load_saved_servers, scan_mcp_configs

        config = adapter._ctx.load_config(defaults={"auto_reconnect": True})
        if not config.get("auto_reconnect", True):
            return

        all_configs = {}
        try:
            all_configs.update(scan_mcp_configs(adapter.skills_dir))
        except Exception as exc:
            logger.debug("MCP discovery scan failed: %s", exc)

        try:
            all_configs.update(load_saved_servers(adapter.config_path))
        except Exception as exc:
            logger.debug("MCP saved servers load failed: %s", exc)

        if not all_configs:
            return

        manager = adapter.manager
        for name, cfg in all_configs.items():
            if manager.is_connected(name):
                continue
            try:
                if cfg.url:
                    reachable = await manager.probe_url(cfg.url)
                    if not reachable:
                        logger.info(
                            "MCP %s: not reachable at %s, skipping",
                            name, cfg.url,
                        )
                        continue

                await manager.connect(
                    name,
                    command=cfg.command,
                    args=cfg.args or None,
                    env=cfg.env or None,
                    url=cfg.url,
                    headers=cfg.headers or None,
                )
                logger.info("MCP %s: auto-connected on startup", name)
            except Exception as exc:
                logger.info("MCP %s: auto-connect failed: %s", name, exc)

    return _startup


def _make_shutdown():
    async def _shutdown() -> None:
        """Disconnect all MCP servers."""
        global _shared_manager
        if _shared_manager is not None:
            try:
                await _shared_manager.disconnect_all()
            except Exception:
                pass
            _shared_manager = None

    return _shutdown


def get_manager() -> Any:
    """Get the shared MCPClientManager instance."""
    return _get_shared_manager()
