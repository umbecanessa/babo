"""Redact inline secrets before logging, journaling, or previews."""

from __future__ import annotations

import re
from typing import Any

# Discord bot tokens: three base64-ish segments
_DISCORD_BOT_TOKEN = re.compile(
    r"\b[MN][A-Za-z\d]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b"
)

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "sk-ant-***"),
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"), "sk-proj-***"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-***"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_***"),
    (re.compile(r"gho_[A-Za-z0-9]{20,}"), "gho_***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github_pat_***"),
    (re.compile(r"xoxb-[A-Za-z0-9\-]{20,}"), "xoxb-***"),
    (re.compile(r"xoxp-[A-Za-z0-9\-]{20,}"), "xoxp-***"),
    (_DISCORD_BOT_TOKEN, "discord-bot-token-***"),
    (re.compile(r"postgres://[^\s]{10,}"), "postgres://***"),
    (re.compile(r"mongodb\+srv://[^\s]{10,}"), "mongodb+srv://***"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "AKIA***"),
]


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace inline secrets with placeholders. Returns (sanitized, count)."""
    if not text:
        return text, 0
    count = 0
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out, n = pattern.subn(replacement, out)
        count += n
    return out, count


def redact_message_dict(msg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a chat message with redacted content."""
    copy = dict(msg)
    content = copy.get("content")
    if isinstance(content, str) and content:
        copy["content"] = redact_secrets(content)[0]
    tool_calls = copy.get("tool_calls")
    if isinstance(tool_calls, list):
        redacted_calls = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                redacted_calls.append(tc)
                continue
            tc_copy = dict(tc)
            fn = tc_copy.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                fn = dict(fn)
                fn["arguments"] = redact_secrets(fn["arguments"])[0]
                tc_copy["function"] = fn
            redacted_calls.append(tc_copy)
        copy["tool_calls"] = redacted_calls
    return copy


def redact_context_for_log(context: list[dict]) -> list[dict]:
    """Redact secrets from a message list before writing to disk."""
    return [redact_message_dict(m) for m in context if isinstance(m, dict)]
