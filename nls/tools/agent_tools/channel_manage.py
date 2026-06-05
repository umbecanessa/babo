"""channel_manage — channel-agnostic admin (sync, scope, permissions)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nls.runtime.channel_manage import (
    channel_manage_actions,
    dispatch_channel_manage,
    list_manageable_channels,
)
from nls.runtime.channel_inspect import known_channels

from .base import ToolResult

logger = logging.getLogger(__name__)


class ChannelManageTool:
    """Admin operations for any channel skill that implements manage_channel."""

    def __init__(self, agent_id: str, agent_dir: Path | None = None) -> None:
        self._agent_id = agent_id
        self._agent_dir = agent_dir

    @property
    def name(self) -> str:
        return "channel_manage"

    @property
    def description(self) -> str:
        known = ", ".join(known_channels())
        manageable = ", ".join(list_manageable_channels()) or known
        return (
            "Channel admin for bundled and custom integrations — uses saved "
            "credentials server-side (NEVER bash/python/curl with tokens).\n"
            f"Channels: {manageable}. Actions vary by channel — common: sync, list, "
            "enable (scope/listen on channel_id), grant_bot_access (Discord workspace), "
            "squad_readiness (Discord multi-face). "
            "Multi-face squads: squad(action='invite_squad_bots', channel_id=...) after "
            "check_channel_readiness.\n"
            "Custom channel skills: implement adapter.manage_channel or register via "
            "ctx.register_channel_manage() in register()."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["channel", "action"],
            "properties": {
                "channel": {
                    "type": "string",
                    "description": (
                        "Channel key: discord, slack, telegram, whatsapp, email, "
                        "or custom skill channel id."
                    ),
                },
                "action": {
                    "type": "string",
                    "description": (
                        "Admin action — run channel_manage with channel only and "
                        "action='help' to list actions for that channel."
                    ),
                },
                "channel_id": {
                    "type": "string",
                    "description": "Workspace channel id (Discord snowflake, Slack channel id).",
                },
                "bot_user_id": {
                    "type": "string",
                    "description": "Target user/bot id for grant_bot_access (Discord).",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "For enable — default true.",
                },
                "require_mention": {
                    "type": "boolean",
                    "description": "For enable on workspace channels.",
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: Any | None = None,
    ) -> ToolResult:
        channel = str(params.get("channel") or "").strip().lower()
        action = str(params.get("action") or "").strip().lower()

        if action == "help":
            if not channel:
                lines = ["Manageable channels:"]
                for ch in list_manageable_channels() or list(known_channels()):
                    acts = channel_manage_actions(ch)
                    lines.append(f"  • {ch}: {', '.join(acts) or '(inspect only)'}")
                return ToolResult(content="\n".join(lines))
            acts = channel_manage_actions(channel)
            if not acts:
                return ToolResult(
                    content=(
                        f"No admin actions for '{channel}'. "
                        "Use channel_inspect(action='get', channel=...)."
                    ),
                    is_error=True,
                )
            return ToolResult(
                content=f"channel_manage channel={channel} actions: {', '.join(acts)}",
            )

        ok, msg = await dispatch_channel_manage(
            self._agent_id,
            channel,
            action,
            dict(params),
        )
        return ToolResult(content=msg, is_error=not ok)


def create_channel_manage_tool(
    agent_id: str,
    agent_dir: Path | None = None,
) -> ChannelManageTool:
    return ChannelManageTool(agent_id=agent_id, agent_dir=agent_dir)
