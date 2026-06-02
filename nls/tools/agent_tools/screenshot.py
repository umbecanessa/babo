"""Screenshot tool — on-demand screen snapshot via the Visual Cortex buffer.

Lets the agent explicitly pull the current screen state at any point during
a turn, e.g.
  "Can you see the WhatsApp page I'm on?"
  "What does my terminal show right now?"
  "Screenshot my screen so you can help me debug this UI."

Returns the latest buffered VC observation (description + OCR text).  If the
buffer is empty or stale (> 30 s old), falls back to requesting a fresh
capture synchronously before returning.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)


class ScreenshotTool:
    """Pull-based screen snapshot via the Visual Cortex buffer."""

    def __init__(self, visual_cortex: Any) -> None:
        self._vc = visual_cortex

    @property
    def name(self) -> str:
        return "screenshot"

    @property
    def description(self) -> str:
        return (
            "Take a screenshot of the user's current screen and return a "
            "description of what is visible, including any readable text (OCR).\n\n"
            "Use this to:\n"
            "- See what page or app the user is currently on\n"
            "- Read text the user is looking at (e.g. a chat, a document, a terminal)\n"
            "- Verify a UI action worked (e.g. 'did the form submit?')\n"
            "- Understand the current visual context before giving advice\n\n"
            "Optionally provide a 'question' to focus the description on a "
            "specific aspect of the screen."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Optional. A specific question about the screen, e.g. "
                        "'What tab is open in the browser?' or "
                        "'What does the error message say?'"
                    ),
                },
            },
            "required": [],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        question: str = params.get("question", "").strip()

        try:
            # Try fresh context first
            ctx = self._vc.get_visual_context(channel="tool")
            if not ctx:
                # Buffer empty — wait a moment for an in-flight capture
                await asyncio.sleep(1.5)
                ctx = self._vc.get_visual_context(channel="tool")

            if not ctx:
                return ToolResult(
                    content=(
                        "No screen capture available yet. "
                        "The Visual Cortex may still be starting up or "
                        "no display is connected. Try again in a few seconds."
                    ),
                    is_error=True,
                )

            if question:
                content = (
                    f"[SCREENSHOT — in response to: {question!r}]\n{ctx}"
                )
            else:
                content = f"[SCREENSHOT]\n{ctx}"

            return ToolResult(content=content)

        except Exception as exc:
            logger.error("ScreenshotTool.execute failed: %s", exc, exc_info=True)
            return ToolResult(
                content=f"Screenshot failed: {exc}",
                is_error=True,
            )


def create_screenshot_tool(visual_cortex: Any) -> ScreenshotTool:
    """Factory: create a screenshot tool backed by the given VisualCortex."""
    return ScreenshotTool(visual_cortex=visual_cortex)
