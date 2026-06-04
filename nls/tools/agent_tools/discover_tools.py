"""discover_tools — meta-tool that lets the model find deferred tools on demand.

When the initial tool set is trimmed to core tools (for context-window
savings), the model can call ``discover_tools(query="email")`` to search
all registered tool names and descriptions.  Matching tool schemas are
returned so the model can use them in subsequent calls.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .base import ToolResult


class DiscoverToolsTool:
    """Search the full tool registry by name or description keyword."""

    def __init__(self, registry: dict[str, Any] | None = None) -> None:
        self._registry: dict[str, Any] = registry or {}

    def set_registry(self, registry: dict[str, Any]) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "discover_tools"

    @property
    def description(self) -> str:
        return (
            "Search for additional tools not in your current tool set. "
            "Pass a keyword (e.g. 'email', 'browser', 'team') and get "
            "matching tool schemas you can then call."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword to search tool names and descriptions",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        query = (params.get("query") or "").lower().strip()
        if not query:
            names = sorted(self._registry.keys())
            return ToolResult(
                content=f"All available tools ({len(names)}): {', '.join(names)}"
            )

        entries: list[tuple[int, str, str]] = []
        for name, tool in self._registry.items():
            desc = getattr(tool, "description", "") or ""
            name_l = name.lower()
            desc_l = desc.lower()
            if query not in name_l and query not in desc_l:
                continue
            if name_l.startswith(query):
                rank = 0
            elif query in name_l:
                rank = 1
            else:
                rank = 2
            entries.append((rank, name, desc))

        entries.sort(key=lambda item: (item[0], item[1]))
        matches = [
            f"- **{name}**: {desc[:120]}"
            for _, name, desc in entries
        ]

        if not matches:
            return ToolResult(
                content=f"No tools matching '{query}'. "
                f"Available: {', '.join(sorted(self._registry.keys()))}"
            )

        return ToolResult(
            content=f"Found {len(matches)} tool(s) matching '{query}':\n"
            + "\n".join(matches)
            + "\n\nThese tools are now available for use."
        )


def create_discover_tools_tool(
    registry: dict[str, Any] | None = None,
) -> DiscoverToolsTool:
    return DiscoverToolsTool(registry=registry)
