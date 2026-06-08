"""channel_remote — read/delete/send via platform APIs (saved credentials)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nls.runtime.channel_inspect import known_channels
from nls.runtime.channel_remote import (
    ambient_vs_remote_guidance,
    channel_remote_actions,
    dispatch_channel_remote,
    list_channel_remote_channels,
)

from .base import ToolResult

logger = logging.getLogger(__name__)


class ChannelRemoteTool:
    """Platform message I/O for bundled channel integrations."""

    def __init__(self, agent_id: str, agent_dir: Path | None = None) -> None:
        self._agent_id = agent_id
        self._agent_dir = agent_dir

    @property
    def name(self) -> str:
        return "channel_remote"

    @property
    def description(self) -> str:
        known = ", ".join(known_channels())
        remote = ", ".join(list_channel_remote_channels()) or known
        return (
            "Platform message I/O using saved credentials (NEVER bash/curl with tokens).\n"
            f"Channels: {remote}. Actions vary: read (Discord/Slack backfill only), "
            "delete, send (text and/or file_path/file_paths).\n"
            f"{ambient_vs_remote_guidance()}\n"
            "Use channel_inspect(action='get', channel=...) for scoped channel IDs."
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
                        "Channel key: discord, slack, telegram, whatsapp, "
                        "or custom skill channel id."
                    ),
                },
                "action": {
                    "type": "string",
                    "description": (
                        "read | delete | send | help — run help per channel "
                        "for supported actions."
                    ),
                },
                "channel_id": {
                    "type": "string",
                    "description": (
                        "Target channel/chat id (Discord snowflake, Slack channel id, "
                        "Telegram chat_id, WhatsApp jid or phone)."
                    ),
                },
                "message_id": {
                    "type": "string",
                    "description": (
                        "Message id to delete (Discord snowflake, Slack ts, Telegram id)."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Message body for send (optional when attaching files).",
                },
                "file_path": {
                    "type": "string",
                    "description": "Single workspace file to attach on send.",
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Multiple workspace files to attach on send.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages for read (default 50).",
                },
                "before": {
                    "type": "string",
                    "description": (
                        "Pagination cursor — Discord message id or Slack next_cursor."
                    ),
                },
                "reply_to_message_id": {
                    "type": "string",
                    "description": "Optional reply target for send.",
                },
                "thread_ts": {
                    "type": "string",
                    "description": "Slack thread timestamp for threaded send.",
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
                lines = ["Remote message channels:"]
                for ch in list_channel_remote_channels() or list(known_channels()):
                    acts = channel_remote_actions(ch)
                    lines.append(f"  • {ch}: {', '.join(acts) or '(none yet)'}")
                return ToolResult(content="\n".join(lines))
            acts = channel_remote_actions(channel)
            if not acts:
                return ToolResult(
                    content=(
                        f"No remote message actions for '{channel}'. "
                        f"{ambient_vs_remote_guidance()}"
                    ),
                    is_error=True,
                )
            return ToolResult(
                content=f"channel_remote channel={channel} actions: {', '.join(acts)}",
            )

        ok, msg = await dispatch_channel_remote(
            self._agent_id,
            channel,
            action,
            dict(params),
        )
        return ToolResult(content=msg, is_error=not ok)


def create_channel_remote_tool(
    agent_id: str,
    agent_dir: Path | None = None,
) -> ChannelRemoteTool:
    return ChannelRemoteTool(agent_id=agent_id, agent_dir=agent_dir)
