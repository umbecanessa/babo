"""Server-side channel message I/O — read/delete/send via saved credentials.

``channel_history`` logs ambient traffic for Discord, Slack, Telegram, and
WhatsApp from the moment the bot is connected — use that for ongoing context.

``channel_remote`` ``read`` backfills *pre-connect* history via platform APIs
(Discord/Slack only). Telegram has no Bot API history fetch; WhatsApp has no
bridge history endpoint. Never expose tokens to bash/curl.
"""

from __future__ import annotations

import logging
from typing import Any

from nls.runtime.channel_manage import _load_adapter, resolve_skill_name
from nls.runtime.channel_policy_profiles import CHANNEL_TO_SKILL

logger = logging.getLogger(__name__)

_REMOTE_ACTIONS = frozenset({"read", "delete", "send", "help"})

# Shared decision guide for tool descriptions and error nudges.
AMBIENT_VS_REMOTE_GUIDANCE = (
    "channel_history = ambient log for Discord, Slack, Telegram, WhatsApp "
    "(all traffic since the bot joined/received messages). "
    "channel_remote read = platform API backfill when you need messages from "
    "before ambient started (Discord/Slack only)."
)

_READ_UNAVAILABLE: dict[str, str] = {
    "telegram": (
        "Telegram Bot API cannot fetch chat history. "
        "Use channel_history(action='recent', session_key=...) for messages "
        "since the bot has been in the chat. "
        "channel_remote supports delete and send on Telegram."
    ),
    "whatsapp": (
        "WhatsApp (Baileys bridge) has no history fetch API. "
        "Use channel_history(action='recent', session_key=...) for messages "
        "since the bot linked. channel_remote supports send only."
    ),
}


def ambient_vs_remote_guidance() -> str:
    return AMBIENT_VS_REMOTE_GUIDANCE


def format_read_unavailable(channel: str) -> str:
    ch = (channel or "").strip().lower()
    if ch in _READ_UNAVAILABLE:
        return _READ_UNAVAILABLE[ch]
    return (
        f"{ch} does not support platform message read. "
        f"{AMBIENT_VS_REMOTE_GUIDANCE}"
    )


def _parse_limit(value: Any, *, default: int = 50) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _validate_remote_target(
    adapter: Any,
    agent_id: str,
    channel_id: str,
    action: str,
) -> tuple[bool, str]:
    """Mirror bundled send-tool scoping for channel_remote I/O."""
    act = (action or "").strip().lower()
    if act not in {"send", "read", "delete"}:
        return True, ""

    allowed_fn = getattr(adapter, "get_allowed_target_ids", None)
    if callable(allowed_fn):
        allowed = allowed_fn(agent_id)
        restricted_fn = getattr(adapter, "_outbound_restricted", None)
        if callable(restricted_fn) and restricted_fn(agent_id):
            if channel_id not in allowed:
                return False, (
                    f"Cannot {act} on {channel_id} — not in allowed targets. "
                    "Enable the channel in Tools, save a contact, or wait for "
                    "them to message first."
                )
        elif allowed and channel_id not in allowed:
            return False, f"Cannot {act} on {channel_id} — not in allowed targets."
        return True, ""

    chat_fn = getattr(adapter, "get_allowed_chat_ids", None)
    if callable(chat_fn):
        allowed = chat_fn(agent_id)
        if allowed and str(channel_id) not in allowed:
            return False, f"Cannot {act} on {channel_id} — not in allowed chats."
    return True, ""


def list_channel_remote_channels() -> list[str]:
    return sorted(CHANNEL_TO_SKILL.keys())


def channel_remote_actions(channel: str) -> list[str]:
    ch = (channel or "").strip().lower()
    if not ch:
        return sorted(_REMOTE_ACTIONS)
    adapter = _load_adapter(resolve_skill_name(ch))
    if adapter is None:
        return []
    fn = getattr(adapter, "channel_remote_actions", None)
    if callable(fn):
        return list(fn())
    # Default: only channels that implement fetch/delete/send hooks
    actions: list[str] = []
    if hasattr(adapter, "fetch_channel_messages"):
        actions.append("read")
    if hasattr(adapter, "delete_channel_message"):
        actions.append("delete")
    if hasattr(adapter, "send"):
        actions.append("send")
    return actions


def format_message_rows(channel: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"No messages returned for {channel}."
    lines = [f"{channel} messages ({len(rows)}):\n"]
    for row in rows:
        mid = row.get("id") or row.get("message_id") or "?"
        ts = row.get("timestamp") or row.get("ts") or ""
        author = row.get("author") or row.get("sender") or "?"
        content = str(row.get("content") or row.get("text") or "").strip()
        if len(content) > 400:
            content = content[:400].rstrip() + "…"
        head = f"[{ts}] id={mid} @{author}"
        lines.append(head)
        if content:
            lines.append(content)
        lines.append("---")
    lines.append(
        "Tip: delete with channel_remote(action='delete', channel_id=..., "
        "message_id=<id above>)."
    )
    return "\n".join(lines)


def format_discord_message_content(msg: dict[str, Any]) -> str:
    """Combine plain text, embeds, and attachment names for read output."""
    parts: list[str] = []
    content = str(msg.get("content") or "").strip()
    if content:
        parts.append(content)
    for embed in msg.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        embed_parts: list[str] = []
        title = str(embed.get("title") or "").strip()
        description = str(embed.get("description") or "").strip()
        if title:
            embed_parts.append(title)
        if description:
            embed_parts.append(description)
        for field in embed.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            value = str(field.get("value") or "").strip()
            if name or value:
                embed_parts.append(f"{name}: {value}".strip(": "))
        footer = embed.get("footer")
        if isinstance(footer, dict):
            foot = str(footer.get("text") or "").strip()
            if foot:
                embed_parts.append(f"[footer: {foot}]")
        if embed_parts:
            parts.append("[embed] " + " | ".join(embed_parts))
    att_names = [
        str(a.get("filename") or "").strip()
        for a in (msg.get("attachments") or [])
        if isinstance(a, dict) and a.get("filename")
    ]
    if att_names:
        parts.append("[attachments: " + ", ".join(att_names) + "]")
    return "\n".join(parts)


def _normalize_file_paths(params: dict[str, Any]) -> tuple[str, list[str]]:
    text = str(params.get("text") or "")
    file_path = str(params.get("file_path") or "").strip()
    raw_paths = params.get("file_paths") or []
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    file_paths = [str(p).strip() for p in raw_paths if str(p).strip()]
    if file_path:
        if file_path not in file_paths:
            file_paths.insert(0, file_path)
    return text, file_paths


async def _dispatch_remote_send(
    adapter: Any,
    agent_id: str,
    channel_id: str,
    channel: str,
    params: dict[str, Any],
) -> tuple[bool, str]:
    """Send text and/or workspace file attachments via adapter hooks."""
    from nls.tools.agent_tools.base import ToolResult

    text, file_paths = _normalize_file_paths(params)
    if not text.strip() and not file_paths:
        return False, "Error: text, file_path, or file_paths required for send."

    extra: dict[str, Any] = {"agent_id": agent_id}
    reply_to = str(params.get("reply_to_message_id") or "").strip()
    thread_ts = str(params.get("thread_ts") or "").strip()
    if reply_to:
        extra["reply_to"] = reply_to
        extra["reply_to_message_id"] = reply_to
    if thread_ts:
        extra["thread_ts"] = thread_ts

    send_fn = getattr(adapter, "send", None)
    send_file = getattr(adapter, "send_file", None)
    upload_file = getattr(adapter, "upload_file", None)

    async def _tool_result_ok(result: Any) -> tuple[bool, str]:
        if isinstance(result, ToolResult):
            if result.is_error:
                return False, str(result.content or "Send failed.")
            return True, str(result.content or "OK")
        return True, str(result)

    if len(file_paths) == 1 and callable(send_file):
        result = await send_file(
            channel_id,
            file_paths[0],
            caption=text,
            agent_id=agent_id,
            reply_to=reply_to or None,
        )
        ok, msg = await _tool_result_ok(result)
        if ok:
            return True, msg or f"Sent file to {channel_id} via {channel}."
        return False, msg

    if len(file_paths) == 1 and callable(upload_file):
        result = await upload_file(
            channel_id,
            file_paths[0],
            initial_comment=text,
            agent_id=agent_id,
        )
        ok, msg = await _tool_result_ok(result)
        if ok:
            return True, msg or f"Sent file to {channel_id} via {channel}."
        return False, msg

    if file_paths and callable(upload_file):
        for fp in file_paths:
            result = await upload_file(
                channel_id,
                fp,
                initial_comment="",
                agent_id=agent_id,
            )
            ok, msg = await _tool_result_ok(result)
            if not ok:
                return False, msg
        if text.strip():
            if not callable(send_fn):
                return True, f"Sent {len(file_paths)} file(s) to {channel_id} via {channel}."
            ok = await send_fn(channel_id, text, **extra)
            if not ok:
                return False, f"Sent {len(file_paths)} file(s) but follow-up text failed."
        return True, f"Sent {len(file_paths)} file(s) to {channel_id} via {channel}."

    if file_paths and callable(send_file):
        for fp in file_paths:
            result = await send_file(
                channel_id,
                fp,
                caption="",
                agent_id=agent_id,
                reply_to=reply_to or None,
            )
            ok, msg = await _tool_result_ok(result)
            if not ok:
                return False, msg
        if text.strip():
            if not callable(send_fn):
                return True, f"Sent {len(file_paths)} file(s) to {channel_id} via {channel}."
            ok = await send_fn(channel_id, text, **extra)
            if not ok:
                return False, f"Sent {len(file_paths)} file(s) but follow-up text failed."
        return True, f"Sent {len(file_paths)} file(s) to {channel_id} via {channel}."

    if not callable(send_fn):
        return False, f"{channel} send is not available on this adapter."
    if not text.strip():
        return False, "Error: text, file_path, or file_paths required for send."
    ok = await send_fn(channel_id, text, **extra)
    if ok:
        return True, f"Message sent to {channel_id} via {channel}."
    return False, f"Failed to send message via {channel}."


async def dispatch_channel_remote(
    agent_id: str,
    channel: str,
    action: str,
    params: dict[str, Any],
) -> tuple[bool, str]:
    ch = (channel or "").strip().lower()
    act = (action or "").strip().lower()
    if not ch:
        return False, "Error: channel is required (discord, slack, telegram, whatsapp)."
    if not act:
        return False, "Error: action is required (read, delete, send, help)."

    if act == "help":
        supported = channel_remote_actions(ch)
        if not supported:
            return False, (
                f"Channel '{ch}' skill is not loaded or has no remote message actions."
            )
        return True, (
            f"channel_remote for '{ch}' supports: {', '.join(supported)}.\n"
            "read: channel_id, limit (default 50), before (optional cursor) "
            "(Discord/Slack backfill only — not Telegram/WhatsApp)\n"
            "delete: channel_id, message_id (Discord/Slack ts, Telegram id)\n"
            "send: channel_id, text and/or file_path/file_paths (+ reply/thread params)\n"
            f"{AMBIENT_VS_REMOTE_GUIDANCE}\n"
            "Uses saved credentials — never bash/curl with tokens."
        )

    if act not in _REMOTE_ACTIONS:
        return False, f"Unknown action '{action}'. Use help."

    skill = resolve_skill_name(ch)
    adapter = _load_adapter(skill)
    if adapter is None:
        return False, (
            f"Error: channel skill '{skill}' is not loaded. "
            "Enable the integration in Tools first."
        )

    channel_id = str(params.get("channel_id") or "").strip()
    if act in ("read", "delete", "send") and not channel_id:
        return False, "Error: channel_id is required."

    ok_target, target_err = _validate_remote_target(adapter, agent_id, channel_id, act)
    if not ok_target:
        return False, target_err

    if act == "read":
        fetch = getattr(adapter, "fetch_channel_messages", None)
        if not callable(fetch):
            return False, format_read_unavailable(ch)
        limit = _parse_limit(params.get("limit"), default=50)
        before = str(params.get("before") or "").strip() or None
        return await fetch(agent_id, channel_id, limit=limit, before=before)

    if act == "delete":
        delete = getattr(adapter, "delete_channel_message", None)
        if not callable(delete):
            return False, f"{ch} does not support platform message delete yet."
        message_id = str(params.get("message_id") or "").strip()
        if not message_id:
            return False, "Error: message_id is required for delete."
        return await delete(agent_id, channel_id, message_id)

    if act == "send":
        return await _dispatch_remote_send(adapter, agent_id, channel_id, ch, params)

    return False, f"Unhandled action '{action}'."
