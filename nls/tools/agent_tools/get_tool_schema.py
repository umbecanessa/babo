"""get_tool_schema meta-tool — lazy schema loading for the agentic loop.

Instead of passing every tool's full JSON Schema on every vLLM call,
the agentic loop passes only a compact tool directory (name + one-liner)
in the system prompt plus this meta-tool.  The model calls
``get_tool_schema("web_search")`` to fetch the full parameter schema
before using a tool for the first time.

This saves ~1000-1500 tokens per agentic iteration at the cost of one
extra round-trip per *new* tool (amortised — tools stay "unlocked" for
the rest of the loop).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .base import AgentTool, ToolResult, tool_to_openai_schema


class GetToolSchemaTool:
    """Return the full OpenAI function-calling schema for a named tool.

    The agentic loop injects this as the only "real" tool schema so the
    model can discover parameters on demand rather than receiving every
    schema upfront.
    """

    def __init__(self, tool_map: dict[str, AgentTool]) -> None:
        self._tool_map = tool_map

    @property
    def name(self) -> str:
        return "get_tool_schema"

    @property
    def description(self) -> str:
        return (
            "Get the full parameter schema for a tool before calling it. "
            "You MUST call this before using any tool for the first time "
            "in this task. Returns the tool's name, description, and "
            "JSON Schema parameters."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the tool to retrieve the schema for.",
                },
            },
            "required": ["tool_name"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        requested = params.get("tool_name", "")
        tool = self._tool_map.get(requested)
        if not tool:
            available = ", ".join(sorted(self._tool_map.keys()))
            return ToolResult(
                content=(
                    f"Unknown tool: '{requested}'. "
                    f"Available tools: {available}"
                ),
                is_error=True,
            )
        schema = tool_to_openai_schema(tool)
        return ToolResult(content=json.dumps(schema["function"], indent=2))
