"""Eyes tool -- agent control over the VisualCortex.

Lets the agent:
  - focus on the browser, desktop, a specific window, or a monitor
  - turn the VC on/off to save resources
  - take an immediate on-demand snapshot (look)
  - ask a targeted question about what's on screen (look with question)
  - look back at recent visual history (look_back)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

_ACTIONS = {"focus", "on", "off", "look", "look_back"}


class EyesTool(AgentTool):
    """Controls the agent's visual perception (VisualCortex)."""

    def __init__(self, visual_cortex: Any) -> None:
        self._vc = visual_cortex

    @property
    def name(self) -> str:
        return "eyes"

    @property
    def description(self) -> str:
        return (
            "Control your visual perception. Use this to see what's on screen.\n\n"
            "ACTIONS:\n"
            "- focus: Point your eyes at a target. Params: target ('browser', "
            "'desktop', 'window:<title>', 'monitor:0', 'monitor:1', 'off'). "
            "Focus on 'browser' during web tasks. Focus on 'window:WhatsApp' "
            "to watch a specific app. Use 'off' when vision is not needed.\n"
            "- on: Resume automatic visual capture (if you turned it off).\n"
            "- off: Pause all automatic visual capture. Use when vision is "
            "not needed to save resources.\n"
            "- look: One-shot immediate capture and description. Does NOT "
            "change your persistent focus. Params: target (optional, same as "
            "focus targets), question (optional — ask something specific about "
            "what you see, e.g. 'Is the form submitted?').\n"
            "- look_back: Review recent visual history. Params: minutes "
            "(default 5). Returns a timeline of what was on screen."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": sorted(_ACTIONS),
                    "description": "Which eye action to perform.",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "Focus target: 'browser', 'desktop', 'window:<title substring>', "
                        "'monitor:0', 'monitor:1', 'off'. Required for 'focus', optional for 'look'."
                    ),
                },
                "question": {
                    "type": "string",
                    "description": "For 'look': ask a specific question about what you see.",
                },
                "minutes": {
                    "type": "number",
                    "description": "For 'look_back': how many minutes of history to review (default 5).",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        if self._vc is None:
            return ToolResult(content="Visual cortex is not available.", is_error=True)

        action = params.get("action", "").strip()
        if action not in _ACTIONS:
            return ToolResult(
                content=f"Unknown action '{action}'. Valid: {', '.join(sorted(_ACTIONS))}",
                is_error=True,
            )

        if action == "focus":
            target = params.get("target", "").strip()
            if not target:
                return ToolResult(content="'focus' requires a 'target' parameter.", is_error=True)
            self._vc.set_focus(target)
            return ToolResult(content=f"Visual focus set to: {target}")

        if action == "on":
            self._vc.set_enabled(True)
            return ToolResult(content="Visual perception resumed.")

        if action == "off":
            self._vc.set_enabled(False)
            return ToolResult(content="Visual perception paused.")

        if action == "look":
            target = params.get("target") or None
            question = params.get("question") or ""
            try:
                result = await self._vc.look_now(target=target, question=question)
            except Exception as exc:
                logger.warning("eyes look failed: %s", exc)
                return ToolResult(content=f"Look failed: {exc}", is_error=True)
            if not result:
                return ToolResult(content="Nothing captured (no visual source available).")
            return ToolResult(content=result)

        if action == "look_back":
            minutes = float(params.get("minutes", 5))
            try:
                result = self._vc.get_history_summary(minutes=int(minutes))
            except Exception as exc:
                logger.warning("eyes look_back failed: %s", exc)
                return ToolResult(content=f"look_back failed: {exc}", is_error=True)
            if not result:
                return ToolResult(content=f"No visual history in the last {minutes:.0f} minutes.")
            return ToolResult(content=result)

        return ToolResult(content=f"Action '{action}' not implemented.", is_error=True)


def create_eyes_tool(visual_cortex: Any) -> EyesTool:
    """Factory for the eyes tool."""
    return EyesTool(visual_cortex)
