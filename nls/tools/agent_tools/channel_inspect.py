"""channel_inspect — on-demand channel configuration and scope for this agent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nls.runtime.channel_inspect import (
    inspect_all_channels,
    inspect_channel,
    known_channels,
    resolve_data_root,
)

from .base import ToolResult

logger = logging.getLogger(__name__)


class ChannelInspectTool:
    """Inspect pre-shipped channel integrations (Discord, Slack, Telegram, etc.)."""

    def __init__(self, agent_id: str, agent_dir: Path | None = None) -> None:
        self._agent_id = agent_id
        self._agent_dir = agent_dir

    @property
    def name(self) -> str:
        return "channel_inspect"

    @property
    def description(self) -> str:
        channels = ", ".join(known_channels())
        return (
            "Inspect this agent's channel integrations — connection status, scoped "
            f"channels, and non-secret settings ({channels}). Use before asking the "
            "owner for bot tokens or channel names. Rings show summary; this tool "
            "returns full detail."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get"],
                    "description": (
                        "list: one-line status for every channel skill. "
                        "get: detailed config for one channel."
                    ),
                },
                "channel": {
                    "type": "string",
                    "description": (
                        "Required for get — discord, slack, telegram, whatsapp, or email."
                    ),
                },
                "active_only": {
                    "type": "boolean",
                    "description": (
                        "For get on discord/slack: only show channels with "
                        "effective_enabled=true (actively listening)."
                    ),
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: Any | None = None,
    ) -> ToolResult:
        action = str(params.get("action") or "").strip().lower()
        data_root = resolve_data_root(self._agent_id, self._agent_dir)
        if data_root is None:
            return ToolResult(
                content="Error: could not resolve data directory for channel inspection.",
                is_error=True,
            )

        try:
            if action == "list":
                return ToolResult(content=inspect_all_channels(data_root, self._agent_id))
            if action == "get":
                channel = str(params.get("channel") or "").strip().lower()
                if not channel:
                    return ToolResult(
                        content="Error: channel is required for action=get.",
                        is_error=True,
                    )
                active_only = bool(params.get("active_only"))
                return ToolResult(
                    content=inspect_channel(
                        data_root,
                        self._agent_id,
                        channel,
                        active_only=active_only,
                    ),
                )
            return ToolResult(
                content=f"Unknown action '{action}'. Use list or get.",
                is_error=True,
            )
        except Exception as exc:
            logger.exception("channel_inspect failed (action=%s)", action)
            return ToolResult(content=f"Error: {exc}", is_error=True)


def create_channel_inspect_tool(
    agent_id: str,
    agent_dir: Path | None = None,
) -> ChannelInspectTool:
    return ChannelInspectTool(agent_id=agent_id, agent_dir=agent_dir)
