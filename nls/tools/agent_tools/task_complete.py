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
            "Call ONLY when the user's entire request is finished — all plan "
            "steps done, no running teams/delegates, nothing queued. "
            "This ends your turn permanently for this task.\n"
            "Do NOT use this while a wave or delegates are still running — "
            "use await_delegates(summary='...') to exit monitoring and let "
            "background work continue. "
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
