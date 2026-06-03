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
import time
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)

_STALE_SECONDS = 30.0
_BUFFER_CHANNELS = ("user", "agent", None)


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

    def _buffered_context(self) -> str:
        """Read VC buffer — user desktop first, then agent/browser, then any."""
        for channel in _BUFFER_CHANNELS:
            ctx = self._vc.get_visual_context(channel=channel)
            if ctx:
                return ctx
        return ""

    def _latest_event_age(self) -> float | None:
        buf = getattr(self._vc, "buffer", None)
        latest = getattr(buf, "latest", None)
        if latest is None:
            return None
        ts = getattr(latest, "timestamp", None)
        if ts is None:
            return None
        return max(0.0, time.time() - float(ts))

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        question: str = params.get("question", "").strip()

        try:
            ctx = self._buffered_context()
            age = self._latest_event_age()
            stale = age is None or age > _STALE_SECONDS

            if (not ctx or stale) and hasattr(self._vc, "look_now"):
                try:
                    fresh = await self._vc.look_now(question=question)
                    if fresh:
                        ctx = fresh
                except Exception as exc:
                    logger.debug("Screenshot look_now fallback failed: %s", exc)

            if not ctx:
                await asyncio.sleep(1.5)
                ctx = self._buffered_context()

            if not ctx:
                buf = getattr(getattr(self._vc, "buffer", None), "__len__", lambda: 0)()
                logger.warning(
                    "ScreenshotTool: empty buffer after retry (buffer_len=%s)",
                    buf() if callable(buf) else 0,
                )
                return ToolResult(
                    content=(
                        "No screen capture available yet. "
                        "The Visual Cortex may still be starting up or "
                        "no display is connected. Try again in a few seconds, "
                        "or use eyes(action='look') for a one-shot capture."
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
