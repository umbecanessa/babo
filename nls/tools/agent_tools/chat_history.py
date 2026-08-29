"""chat_history — search the append-only user-visible chat transcript.

The transcript lives at ``{agent_dir}/chat_transcript.jsonl`` (one message per
line).  It is **not** injected into model context automatically — use this tool
when the user references earlier conversation ("we discussed", "you said", etc.).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nls.runtime.session import query_chat_transcript

from .base import ToolResult

_DEFAULT_LIMIT = 15
_MAX_LIMIT = 100
_PREVIEW_CHARS = 600


def _format_message(row: dict) -> str:
    role = row.get("role", "?")
    line = row.get("line", "?")
    ts = row.get("ts") or ""
    content = (row.get("content") or "").strip()
    if len(content) > _PREVIEW_CHARS:
        content = content[:_PREVIEW_CHARS].rstrip() + "\n... (truncated)"
    parts = [f"[{line}] {role.upper()}"]
    if ts:
        parts[0] += f" @ {ts}"
    if content:
        parts.append(content)
    reasoning = (row.get("reasoning") or "").strip()
    if reasoning:
        r = reasoning[:200].rstrip()
        if len(reasoning) > 200:
            r += "..."
        parts.append(f"(reasoning: {r})")
    meta = row.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("agentic"):
        parts.append(
            f"(agentic: {meta.get('iterations', '?')} steps, "
            f"{meta.get('tool_calls', '?')} tool calls"
            + (
                f", aborted={meta.get('abort_reason')}"
                if meta.get("aborted")
                else ""
            )
            + ")"
        )
        events = meta.get("events") or []
        if isinstance(events, list) and events:
            prose_bits: list[str] = []
            tool_names: list[str] = []
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                prose = str(ev.get("prose") or "").strip()
                if prose:
                    prose_bits.append(prose)
                for tc in ev.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("name"):
                        tool_names.append(str(tc["name"]))
            if tool_names:
                # Dedupe while preserving order
                seen: set[str] = set()
                ordered: list[str] = []
                for name in tool_names:
                    if name in seen:
                        continue
                    seen.add(name)
                    ordered.append(name)
                parts.append("tools: " + ", ".join(ordered[:24]))
            if prose_bits:
                joined = "\n---\n".join(prose_bits)
                if len(joined) > _PREVIEW_CHARS:
                    joined = joined[:_PREVIEW_CHARS].rstrip() + "\n... (truncated)"
                parts.append("assistant prose during task:\n" + joined)
            elif not content:
                parts.append(
                    "(no assistant prose saved for this agentic turn — "
                    "use working_memory / file tools for task state)"
                )
    return "\n".join(parts)


class ChatHistoryTool:
    """Query the persistent chat transcript JSONL log."""

    def __init__(self, agent_dir: Path) -> None:
        self._agent_dir = agent_dir

    @property
    def name(self) -> str:
        return "chat_history"

    @property
    def description(self) -> str:
        return (
            "Search the full user-visible chat transcript for this agent. "
            "Use when the user references a prior discussion, decision, or "
            "something they said earlier — the transcript is NOT in your "
            "automatic context window. "
            "Actions: search (keyword), recent (latest messages), "
            "around (context near a line number), stats (log overview)."
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
                    "description": (
                        "search: find messages matching query. "
                        "recent: latest N messages in order. "
                        "around: messages near a transcript line number. "
                        "stats: overview of the log size and date span."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Keyword or phrase to search (for 'search').",
                },
                "role": {
                    "type": "string",
                    "enum": ["user", "assistant"],
                    "description": "Filter to user or assistant messages only.",
                },
                "line": {
                    "type": "integer",
                    "description": "Transcript line number (for 'around').",
                },
                "context": {
                    "type": "integer",
                    "description": "Lines before/after center line (default 5).",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max results (default {_DEFAULT_LIMIT}, max {_MAX_LIMIT}).",
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip first N matches (pagination).",
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
        role = (params.get("role") or "").strip().lower()

        if action == "stats":
            return ToolResult(content=self._stats())

        if action == "recent":
            rows, total = query_chat_transcript(
                self._agent_dir,
                limit=limit,
                offset=offset,
                newest_first=False,
            )
            if not rows and total == 0:
                return ToolResult(content="Chat transcript is empty.")
            body = self._format_block(rows, total, "Recent messages")
            return ToolResult(content=body)

        if action == "around":
            center = int(params.get("line") or 0)
            if center < 1:
                return ToolResult(
                    content="Error: 'line' (>=1) is required for action=around.",
                    is_error=True,
                )
            ctx = min(max(1, int(params.get("context") or 5)), 30)
            _, total = query_chat_transcript(self._agent_dir, limit=1)
            rows, _ = query_chat_transcript(
                self._agent_dir,
                line_start=max(1, center - ctx),
                line_end=min(total, center + ctx),
                newest_first=False,
                limit=None,
            )
            if not rows:
                return ToolResult(
                    content=f"No transcript lines near line {center} (total={total}).",
                )
            body = self._format_block(
                rows, total, f"Context around line {center} (±{ctx})",
            )
            return ToolResult(content=body)

        # default: search
        query = (params.get("query") or "").strip()
        if not query:
            return ToolResult(
                content="Error: 'query' is required for action=search.",
                is_error=True,
            )
        rows, total = query_chat_transcript(
            self._agent_dir,
            query=query,
            role=role if role in ("user", "assistant") else "",
            limit=limit,
            offset=offset,
            newest_first=True,
        )
        if not rows:
            return ToolResult(
                content=(
                    f"No chat transcript matches for '{query}' "
                    f"(searched {total} line(s)). "
                    "Try broader keywords or action=recent."
                ),
            )
        body = self._format_block(rows, total, f"Matches for '{query}'")
        return ToolResult(content=body)

    def _stats(self) -> str:
        from nls.runtime.session import chat_transcript_stats

        stats = chat_transcript_stats(self._agent_dir)
        if stats["total"] == 0:
            return "Chat transcript is empty."
        size_kb = stats["size_bytes"] / 1024
        return (
            f"Chat transcript: {stats['total']} message(s) "
            f"({stats['user']} user, {stats['assistant']} assistant)\n"
            f"Span: {stats['first_ts'] or '?'} → {stats['last_ts'] or '?'}\n"
            f"File: chat_transcript.jsonl ({size_kb:.1f} KB)\n"
            f"Use chat_history(action='search', query='...') to find prior discussion."
        )

    def _format_block(self, rows: list[dict], total: int, title: str) -> str:
        lines = [
            f"{title} — showing {len(rows)} of {total} transcript line(s):\n",
        ]
        for row in rows:
            lines.append(_format_message(row))
            lines.append("---")
        lines.append(
            "Tip: use action=around with line=<N> for more context, "
            "or increase limit/offset to paginate."
        )
        return "\n".join(lines)


def create_chat_history_tool(agent_dir: Path) -> ChatHistoryTool:
    return ChatHistoryTool(agent_dir)
