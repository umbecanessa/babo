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
            f"channels, and non-secret settings ({channels}). Squad leads: "
            "action=squad_readiness + channel_id to see which squad bots are in a "
            "Discord channel; target_agent_id for member detail."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "squad_readiness"],
                    "description": (
                        "list: one-line status for every channel skill. "
                        "get: detailed config for one channel. "
                        "squad_readiness: lead-only — which squad bots can access channel_id."
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
                "target_agent_id": {
                    "type": "string",
                    "description": (
                        "Squad lead only: inspect another squad member's channel config "
                        "(defaults to this agent). Not used for squad_readiness."
                    ),
                },
                "channel_id": {
                    "type": "string",
                    "description": (
                        "Required for squad_readiness — Discord text channel snowflake."
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
        target_agent_id = str(params.get("target_agent_id") or "").strip() or self._agent_id

        if target_agent_id != self._agent_id:
            try:
                from nls.runtime.skill_config_service import assert_squad_lead_may_configure

                assert_squad_lead_may_configure(self._agent_id, target_agent_id)
            except (PermissionError, ValueError) as exc:
                return ToolResult(content=f"Error: {exc}", is_error=True)
            except Exception as exc:
                return ToolResult(content=f"Error: {exc}", is_error=True)

        inspect_dir = self._agent_dir if target_agent_id == self._agent_id else None
        data_root = resolve_data_root(target_agent_id, inspect_dir)
        if data_root is None:
            return ToolResult(
                content="Error: could not resolve data directory for channel inspection.",
                is_error=True,
            )

        inspect_id = target_agent_id

        try:
            if action == "squad_readiness":
                channel_id = str(params.get("channel_id") or "").strip()
                if not channel_id:
                    return ToolResult(
                        content="Error: channel_id is required for squad_readiness.",
                        is_error=True,
                    )
                from nls.runtime.discord_squad_readiness import audit_squad_discord_channel

                _, report = await audit_squad_discord_channel(
                    self._agent_id, channel_id,
                )
                return ToolResult(content=report)
            if action == "list":
                return ToolResult(content=inspect_all_channels(data_root, inspect_id))
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
                        inspect_id,
                        channel,
                        active_only=active_only,
                    ),
                )
            return ToolResult(
                content=f"Unknown action '{action}'. Use list, get, or squad_readiness.",
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
