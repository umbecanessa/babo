"""Detect duplicate outbound channel sends within one agentic loop."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

CHANNEL_SEND_TOOL_NAMES = frozenset({
    "discord_send",
    "slack_send",
    "telegram_send",
    "whatsapp_send",
    "channel_remote",
})

_WHITESPACE_RE = re.compile(r"\s+")
_SCALAR_FIELD_RE = re.compile(
    r'"(channel_id|chat_id|group_id|phone|to|number|channel|action)"\s*:\s*"([^"]*)"',
)
_TEXT_FIELD_RE = re.compile(
    r'"(text|message|body)"\s*:\s*"((?:[^"\\]|\\.)*)',
)


def _parse_signature_args(sig: str) -> dict[str, Any] | None:
    """Parse tool args from a signature (handles 200-char loop truncation)."""
    if ":" not in sig:
        return None
    raw = sig.split(":", 1)[1]
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    data: dict[str, Any] = {}
    for match in _SCALAR_FIELD_RE.finditer(raw):
        data[match.group(1)] = match.group(2)
    text_match = _TEXT_FIELD_RE.search(raw)
    if text_match:
        data[text_match.group(1)] = text_match.group(2)
    return data or None


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text)


def _text_fingerprint(text: str) -> str:
    """Stable digest from full text, or first+last 200 chars when long."""
    norm = _normalize_text(text)
    if not norm:
        return ""
    if len(norm) <= 400:
        payload = norm
    else:
        payload = f"{norm[:200]}|{norm[-200:]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _target_from_args(tool: str, data: dict[str, Any]) -> str:
    if tool == "channel_remote":
        channel = _normalize_text(data.get("channel")).lower()
        target = _normalize_text(data.get("channel_id"))
        if channel:
            return f"{channel}:{target}" if target else channel
        return target
    if tool == "whatsapp_send":
        return _normalize_text(
            data.get("group_id") or data.get("phone") or data.get("to") or data.get("number"),
        )
    if tool == "telegram_send":
        return _normalize_text(data.get("chat_id"))
    return _normalize_text(data.get("channel_id"))


def _is_channel_send_signature(sig: str) -> bool:
    if ":" not in sig:
        return False
    tool = sig.split(":", 1)[0]
    if tool not in CHANNEL_SEND_TOOL_NAMES:
        return False
    if tool == "channel_remote":
        data = _parse_signature_args(sig)
        if not data or _normalize_text(data.get("action")).lower() != "send":
            return False
    return True


def channel_send_fingerprint(sig: str) -> str | None:
    """Stable fingerprint for text sends: tool|target|normalized_text."""
    if not _is_channel_send_signature(sig):
        return None
    tool = sig.split(":", 1)[0]
    data = _parse_signature_args(sig)
    if not data:
        return None
    target = _target_from_args(tool, data)
    text = _normalize_text(
        data.get("text") or data.get("message") or data.get("body"),
    )
    if not target or not text:
        return None
    return f"{tool}|{target}|{_text_fingerprint(text)}"


def find_duplicate_channel_send(
    signatures: list[str],
    tool_history: list[tuple[str, bool]] | None = None,
) -> tuple[str, str] | None:
    """Return (target_id, tool_name) when the same send appears 2+ times.

    Only successful sends count — retries after a failure are allowed.
    """
    exact_counts: dict[str, int] = {}
    exact_meta: dict[str, tuple[str, str]] = {}
    semantic_counts: dict[str, int] = {}
    semantic_meta: dict[str, tuple[str, str]] = {}

    for index, sig in enumerate(signatures):
        if tool_history is not None:
            if index >= len(tool_history):
                continue
            _name, had_error = tool_history[index]
            if had_error:
                continue
        if not _is_channel_send_signature(sig):
            continue

        tool = sig.split(":", 1)[0]
        exact_counts[sig] = exact_counts.get(sig, 0) + 1
        if sig not in exact_meta:
            data = _parse_signature_args(sig) or {}
            target = _target_from_args(tool, data) or "?"
            exact_meta[sig] = (target, tool)

        fp = channel_send_fingerprint(sig)
        if fp:
            semantic_counts[fp] = semantic_counts.get(fp, 0) + 1
            if fp not in semantic_meta:
                semantic_meta[fp] = (fp.split("|", 2)[1], tool)

    for sig, count in exact_counts.items():
        if count >= 2:
            return exact_meta[sig]
    for fp, count in semantic_counts.items():
        if count >= 2:
            return semantic_meta[fp]
    return None


def format_duplicate_channel_send_nudge(target_id: str, tool_name: str) -> str:
    return (
        f"You already sent this exact message to {target_id} via {tool_name} "
        f"in this task. Do not post duplicates — continue with the next channel "
        f"or call task_complete(summary='...'). To verify delivery use "
        f"channel_remote(action='read', ...) or channel_history(action='recent', ...)."
    )
