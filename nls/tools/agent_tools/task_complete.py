"""task_complete tool -- explicit signal that the agent's work is done.

Instead of relying on the evaluator's heuristics to detect completion
from text-only responses, the agent calls this tool when it has
genuinely finished.  The loop exits cleanly with ``tool_requested_stop``.

This removes ambiguity: a long text response is never auto-interpreted
as "done" — the agent must deliberately choose to end.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)


class TaskCompleteTool:
    """Agent calls this to explicitly signal task completion."""

    @property
    def name(self) -> str:
        return "task_complete"

    @property
    def description(self) -> str:
        return (
            "Call this tool ONLY when you have fully completed the user's "
            "request and have nothing left to do. This ends your turn. "
            "Do NOT call this if there are pending plan steps, queued waves, "
            "or running teams — finish those first. "
            "Pass a short summary of what was accomplished."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "Brief summary of what was accomplished "
                        "(shown to the user as the final message)."
                    ),
                },
            },
            "required": ["summary"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        summary = (params.get("summary") or "").strip()
        if not summary:
            return ToolResult(
                content="Error: 'summary' is required — describe what you accomplished.",
                is_error=True,
            )

        logger.info("[task_complete] Agent signaled completion: %s", summary[:200])

        return ToolResult(
            content=summary,
            stop_loop=True,
            details={"type": "task_complete", "summary": summary},
        )


def create_task_complete_tool() -> TaskCompleteTool:
    return TaskCompleteTool()
