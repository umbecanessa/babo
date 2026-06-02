"""MCPToolProxy -- wraps a single MCP server tool as an NLS AgentTool.

Each proxy delegates ``execute()`` to the MCP session's ``call_tool()``
method, translating between the MCP ``CallToolResult`` and the NLS
``ToolResult`` format.  Tool schemas map 1:1 because both use JSON
Schema for parameter definitions.

Proxies are namespaced as ``{server}__{tool}`` to avoid collisions
when multiple MCP servers are connected simultaneously.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)


class MCPToolProxy:
    """Wraps one MCP tool as an NLS ``AgentTool``.

    Parameters
    ----------
    session : mcp.ClientSession
        The live MCP client session that owns this tool.
    server_name : str
        Short identifier for the server (e.g. ``"unity"``).
    mcp_tool : mcp.types.Tool
        The tool descriptor returned by ``session.list_tools()``.
    """

    def __init__(
        self,
        session: Any,
        server_name: str,
        mcp_tool: Any,
    ) -> None:
        self._session = session
        self._server_name = server_name
        self._mcp_tool = mcp_tool

    # -- AgentTool protocol ---------------------------------------------------

    @property
    def name(self) -> str:
        return f"{self._server_name}__{self._mcp_tool.name}"

    @property
    def description(self) -> str:
        desc = self._mcp_tool.description or self._mcp_tool.name
        return f"[{self._server_name}] {desc}"

    @property
    def parameters(self) -> dict[str, Any]:
        schema = self._mcp_tool.inputSchema
        if isinstance(schema, dict):
            return schema
        if hasattr(schema, "model_dump"):
            return schema.model_dump(exclude_none=True)
        return {"type": "object", "properties": {}}

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        try:
            result = await self._session.call_tool(
                self._mcp_tool.name,
                arguments=params or None,
            )
        except Exception as exc:
            logger.warning(
                "MCP call_tool failed (%s/%s): %s",
                self._server_name, self._mcp_tool.name, exc,
            )
            return ToolResult(
                content=f"MCP tool error ({self._server_name}/{self._mcp_tool.name}): {exc}",
                is_error=True,
            )

        content = _extract_content(result)
        is_error = getattr(result, "isError", False) or False
        return ToolResult(content=content, is_error=is_error)

    # -- Helpers --------------------------------------------------------------

    @property
    def mcp_tool_name(self) -> str:
        """The original (un-namespaced) MCP tool name."""
        return self._mcp_tool.name

    @property
    def server_name(self) -> str:
        return self._server_name

    def __repr__(self) -> str:
        return f"<MCPToolProxy {self.name}>"


def _extract_content(result: Any) -> str:
    """Convert MCP ``CallToolResult.content`` blocks to plain text."""
    content_list = getattr(result, "content", None)
    if not content_list:
        return "(no output)"

    parts: list[str] = []
    for block in content_list:
        if hasattr(block, "text"):
            parts.append(block.text)
        elif hasattr(block, "data"):
            parts.append(f"[binary: {getattr(block, 'mimeType', 'unknown')}]")
        else:
            parts.append(str(block))
    return "\n".join(parts) or "(no output)"
