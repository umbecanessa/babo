"""channel_history — search ambient group/channel transcripts.

Group and shared-channel messages are logged to ``channel_ambient.jsonl`` even
when mention policy blocks a reply.  Use this tool to catch up on what was
said before an @mention or when debugging channel context.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nls.runtime.channel_ambient import channel_ambient_stats, query_channel_ambient

from .base import ToolResult

_DEFAULT_LIMIT = 15
_MAX_LIMIT = 100
_PREVIEW_CHARS = 600


def _format_row(row: dict) -> str:
    line = row.get("line", "?")
    role = row.get("role", "?")
    sender = row.get("sender_name") or row.get("sender_id") or "?"
    channel = row.get("channel") or "?"
    session = row.get("session_key") or "?"
    ts = row.get("ts") or ""
    triggered = row.get("triggered")
    content = (row.get("content") or "").strip()
    if len(content) > _PREVIEW_CHARS:
        content = content[:_PREVIEW_CHARS].rstrip() + "\n... (truncated)"
    head = f"[{line}] {role} {sender} ({channel})"
    if ts:
        head += f" @ {ts}"
    if triggered:
        head += " [triggered reply]"
    parts = [head, f"session: {session}"]
    if content:
        parts.append(content)
    return "\n".join(parts)


class ChannelHistoryTool:
    """Query the ambient shared-channel transcript log."""

    def __init__(self, agent_dir: Path) -> None:
        self._agent_dir = agent_dir

    @property
    def name(self) -> str:
        return "channel_history"

    @property
    def description(self) -> str:
        return (
            "Search the ambient log of group/shared-channel messages (Telegram, "
            "Discord, Slack, WhatsApp). Includes messages that did NOT trigger a "
            "bot reply — use when @mentioned mid-conversation or you need thread "
            "context. Actions: search, recent, around, stats. Filter by "
            "session_key and/or channel."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "recent", "around", "stats"],
                },
                "query": {
                    "type": "string",
                    "description": "Keyword search (action=search).",
                },
                "session_key": {
                    "type": "string",
                    "description": (
                        "Filter to one thread, e.g. telegram:group:-100… or "
                        "discord:channel:123…"
                    ),
                },
                "channel": {
                    "type": "string",
                    "description": "Filter by channel id: telegram, discord, slack, whatsapp.",
                },
                "role": {
                    "type": "string",
                    "enum": ["user", "assistant"],
                },
                "line": {
                    "type": "integer",
                    "description": "Center line for action=around.",
                },
                "context": {
                    "type": "integer",
                    "description": "Lines before/after center (default 5).",
                },
                "limit": {
                    "type": "integer",
                },
                "offset": {
                    "type": "integer",
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = (params.get("action") or "search").strip().lower()
        limit = min(max(1, int(params.get("limit") or _DEFAULT_LIMIT)), _MAX_LIMIT)
        offset = max(0, int(params.get("offset") or 0))
        session_key = (params.get("session_key") or "").strip()
        channel = (params.get("channel") or "").strip()
        role = (params.get("role") or "").strip().lower()

        if action == "stats":
            stats = channel_ambient_stats(self._agent_dir)
            if stats["total"] == 0:
                return ToolResult(content="Channel ambient log is empty.")
            ch_lines = ", ".join(f"{k}={v}" for k, v in sorted(stats["channels"].items()))
            size_kb = stats["size_bytes"] / 1024
            return ToolResult(content=(
                f"Channel ambient log: {stats['total']} message(s) "
                f"across {stats['sessions']} session(s)\n"
                f"Channels: {ch_lines or 'none'}\n"
                f"Span: {stats['first_ts'] or '?'} → {stats['last_ts'] or '?'}\n"
                f"File: channel_ambient.jsonl ({size_kb:.1f} KB)"
            ))

        if action == "recent":
            rows, total = query_channel_ambient(
                self._agent_dir,
                session_key=session_key,
                channel=channel,
                role=role if role in ("user", "assistant") else "",
                limit=limit,
                offset=offset,
                newest_first=False,
            )
            if not rows:
                return ToolResult(content="No ambient channel messages match filters.")
            return ToolResult(content=self._block(rows, total, "Recent channel messages"))

        if action == "around":
            center = int(params.get("line") or 0)
            if center < 1:
                return ToolResult(
                    content="Error: 'line' (>=1) required for action=around.",
                    is_error=True,
                )
            ctx = min(max(1, int(params.get("context") or 5)), 30)
            _, total = query_channel_ambient(self._agent_dir, limit=1)
            rows, _ = query_channel_ambient(
                self._agent_dir,
                session_key=session_key,
                channel=channel,
                line_start=max(1, center - ctx),
                line_end=min(total, center + ctx),
                newest_first=False,
                limit=None,
            )
            if not rows:
                return ToolResult(content=f"No lines near {center} (total={total}).")
            return ToolResult(content=self._block(rows, total, f"Context around line {center}"))

        query = (params.get("query") or "").strip()
        if not query:
            return ToolResult(
                content="Error: 'query' required for action=search.",
                is_error=True,
            )
        rows, total = query_channel_ambient(
            self._agent_dir,
            query=query,
            session_key=session_key,
            channel=channel,
            role=role if role in ("user", "assistant") else "",
            limit=limit,
            offset=offset,
            newest_first=True,
        )
        if not rows:
            return ToolResult(
                content=(
                    f"No ambient channel matches for '{query}' "
                    f"(searched {total} line(s))."
                ),
            )
        return ToolResult(content=self._block(rows, total, f"Matches for '{query}'"))

    def _block(self, rows: list[dict], total: int, title: str) -> str:
        lines = [f"{title} — showing {len(rows)} of {total} line(s):\n"]
        for row in rows:
            lines.append(_format_row(row))
            lines.append("---")
        lines.append(
            "Tip: filter with session_key=… or channel=telegram|discord|slack."
        )
        return "\n".join(lines)


def create_channel_history_tool(agent_dir: Path) -> ChannelHistoryTool:
    return ChannelHistoryTool(agent_dir)
