"""UI operator outbound sends for external channel sessions.

When the desktop Chat composer is on a surface thread (Discord, Telegram, …),
messages must route through the channel adapter — not only the WebSocket chat
history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_SURFACE_CHANNELS = frozenset({"discord", "telegram", "whatsapp", "slack", "email"})


@dataclass
class SurfaceSendTarget:
    channel: str
    reply_target: str
    session_key: str
    send_kwargs: dict[str, Any] = field(default_factory=dict)


def is_surface_session_key(session_key: str | None, runtime: Any | None = None) -> bool:
    from nls.runtime.session_routing.surface import is_routable_surface_session_key

    return is_routable_surface_session_key(session_key, runtime)


def channel_session_metadata(normalized: dict[str, Any]) -> dict[str, Any]:
    """Build session index metadata including adapter reply_target."""
    meta = normalized.get("metadata") or {}
    channel = str(normalized.get("channel") or "")
    out: dict[str, Any] = {
        "channel": channel,
        "sender": normalized.get("sender_name") or normalized.get("sender_id") or "",
    }
    subject = normalized.get("subject") or meta.get("subject")
    if subject:
        out["subject"] = subject

    reply_target = (
        meta.get("channel_id")
        or meta.get("chat_id")
        or meta.get("jid")
    )
    if channel == "email":
        reply_target = normalized.get("sender_id") or reply_target
    session_key = normalized.get("session_key") or ""
    if not reply_target and session_key:
        reply_target = _fallback_reply_target_from_key(session_key, channel)
    if reply_target:
        out["reply_target"] = str(reply_target)
    ch_name = meta.get("channel_name") or normalized.get("channel_name")
    if ch_name:
        out["channel_name"] = ch_name
    guild_name = meta.get("guild_name") or normalized.get("guild_name")
    if guild_name:
        out["guild_name"] = guild_name
    if meta.get("thread_ts"):
        out["thread_ts"] = meta["thread_ts"]
    return out


def _fallback_reply_target_from_key(session_key: str, channel: str) -> str | None:
    parts = session_key.split(":")
    if len(parts) < 3:
        return None
    thread_type, ident = parts[1], parts[2]
    if channel in ("discord", "slack", "telegram", "whatsapp"):
        if thread_type in ("channel", "group"):
            return ident
        if channel == "telegram" and thread_type == "dm":
            return ident
        if channel == "whatsapp" and thread_type == "dm":
            return ident
    if channel == "email" and thread_type == "thread":
        return None
    return None


def resolve_surface_target(
    session_key: str,
    session_meta: dict[str, Any] | None = None,
    runtime: Any | None = None,
) -> SurfaceSendTarget | None:
    """Resolve adapter send target from session key + persisted metadata."""
    if not is_surface_session_key(session_key, runtime):
        return None

    meta = session_meta or {}
    parts = session_key.split(":")
    channel = parts[0]
    send_kwargs: dict[str, Any] = {}

    reply_target = str(meta.get("reply_target") or "").strip()
    if not reply_target:
        reply_target = _fallback_reply_target_from_key(session_key, channel) or ""

    if channel == "email":
        reply_target = reply_target or str(meta.get("sender") or "").strip()
        subject = meta.get("subject") or "Message from your agent"
        send_kwargs["subject"] = (
            subject if str(subject).lower().startswith("re:") else f"Re: {subject}"
        )

    if meta.get("thread_ts"):
        send_kwargs["thread_ts"] = meta["thread_ts"]

    if not reply_target:
        return None

    return SurfaceSendTarget(
        channel=channel,
        reply_target=reply_target,
        session_key=session_key,
        send_kwargs=send_kwargs,
    )


def get_session_meta(runtime: Any, session_key: str) -> dict[str, Any]:
    registry = getattr(runtime, "channel_registry", None)
    if registry is None:
        return {}
    try:
        sessions = registry.session_router.list_sessions()
        return dict(sessions.get(session_key) or {})
    except Exception:
        return {}


def _get_channel_adapter(runtime: Any, channel: str) -> Any | None:
    registry = getattr(runtime, "channel_registry", None)
    if registry is None:
        return None
    return registry.get(channel)


async def send_surface_message(
    app: Any,
    runtime: Any,
    agent_id: str,
    session_key: str,
    text: str,
    *,
    attachments: list[dict[str, Any]] | None = None,
    operator_prefix: bool = False,
) -> dict[str, Any]:
    """Send operator text to the external channel for *session_key*."""
    text = (text or "").strip()
    if not text and not attachments:
        return {"ok": False, "error": "empty_message"}

    meta = get_session_meta(runtime, session_key)
    target = resolve_surface_target(session_key, meta, runtime)
    if target is None:
        return {
            "ok": False,
            "error": "Could not resolve channel send target for this conversation. "
            "Wait for an inbound message on this thread first.",
        }

    adapter = _get_channel_adapter(runtime, target.channel)
    if adapter is None:
        return {"ok": False, "error": f"{target.channel} adapter is not connected"}

    outbound = text
    if operator_prefix and outbound:
        outbound = f"[Operator via Babo]: {outbound}"

    ok = False
    try:
        if attachments:
            for att in attachments:
                path = att.get("path") or att.get("file_path") or ""
                if not path:
                    continue
                if hasattr(adapter, "send_file"):
                    result = await adapter.send_file(
                        target.reply_target,
                        path,
                        caption=outbound if att is attachments[0] else "",
                        agent_id=agent_id,
                    )
                    ok = not getattr(result, "is_error", True)
                    outbound = ""
                else:
                    logger.warning(
                        "Surface send: %s does not support file attachments",
                        target.channel,
                    )
        if outbound:
            ok = await adapter.send(
                target.reply_target,
                outbound,
                agent_id=agent_id,
                **target.send_kwargs,
            )
        elif attachments and ok:
            pass
        elif not attachments:
            return {"ok": False, "error": "empty_message"}
    except Exception as exc:
        logger.error(
            "Surface send failed [%s] %s: %s",
            agent_id, session_key, exc,
            exc_info=True,
        )
        return {"ok": False, "error": str(exc)}

    if not ok:
        return {"ok": False, "error": f"Failed to send via {target.channel}"}

    history = runtime.load_session_history(session_key=session_key, max_turns=40)
    display = text or "(attachment)"
    history.append({"role": "assistant", "content": display})
    runtime.save_session_history(
        history,
        session_key=session_key,
        metadata={
            "channel": target.channel,
            "sender": meta.get("sender", ""),
            "reply_target": target.reply_target,
            **({"subject": meta["subject"]} if meta.get("subject") else {}),
        },
    )

    _broadcast_outbound(app, agent_id, target, display, meta)
    return {"ok": True, "channel": target.channel, "session_key": session_key}


def _broadcast_outbound(
    app: Any,
    agent_id: str,
    target: SurfaceSendTarget,
    content: str,
    session_meta: dict[str, Any] | None = None,
) -> None:
    from nls.skills.channel_adapter_util import broadcast_channel_event

    meta = session_meta or {}
    normalized = {
        "session_key": target.session_key,
        "sender_name": meta.get("sender", "Operator"),
        "content": "",
        "channel": target.channel,
    }
    broadcast_channel_event(
        app,
        agent_id,
        target.channel,
        normalized,
        response=content,
        direction="response",
    )
