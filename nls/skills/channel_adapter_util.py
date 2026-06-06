"""Shared helpers for bundled channel adapters (Telegram-style)."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SIGNAL_TAG_RE = re.compile(r"\[(?:[A-Za-z_]+)(?:[:.](?:[^\]]*))?\]\s*")


def strip_signal_tags(text: str) -> str:
    return _SIGNAL_TAG_RE.sub("", text).strip()


def prepare_channel_outbound(text: str) -> str:
    """Signal-tag strip + tool-leak sanitize for public channel replies."""
    from nls.runtime.response_cleanup import sanitize_channel_outbound

    return sanitize_channel_outbound(strip_signal_tags(text or ""))


def channel_history_content(
    text: str,
    attachments: list[Any] | None = None,
) -> str:
    """Stable session-history label for inbound channel messages."""
    if (text or "").strip():
        return text
    if attachments:
        return "[media]"
    return "[empty]"


def get_relay_base_url(cfg: dict[str, Any]) -> str:
    if cfg.get("webhook_relay_base_url"):
        return str(cfg["webhook_relay_base_url"]).rstrip("/")
    import os
    env_url = os.environ.get("NESTJS_URL", "")
    if env_url:
        return env_url.rstrip("/")
    try:
        from server.main import app
        cfg_state = getattr(app.state, "config", {})
        nest_url = cfg_state.get("nestjs_url", "")
        if nest_url:
            return nest_url.rstrip("/")
    except Exception:
        pass
    return ""


def get_runtime_secret() -> str:
    import os
    return os.environ.get("RUNTIME_SHARED_SECRET", "") or os.environ.get(
        "NLS_SHARED_SECRET", "",
    )


async def ensure_relay(
    relay_clients: dict[str, Any],
    agent_id: str,
    relay_base_url: str,
) -> None:
    if agent_id in relay_clients:
        existing = relay_clients[agent_id]
        if getattr(existing, "connected", False):
            return
        await existing.disconnect()

    if not relay_base_url:
        logger.warning("Channel [%s]: no NESTJS_URL — relay WS skipped", agent_id)
        return

    from nls.runtime.channels import ChannelRelayClient
    client = ChannelRelayClient(relay_base_url, agent_id, get_runtime_secret())
    relay_clients[agent_id] = client
    await client.connect()


def register_with_agent(agent_id: str, adapter: Any) -> None:
    try:
        from server.main import app
        agent_manager = getattr(app.state, "agent_manager", None)
        if agent_manager is None:
            return
        runtime = agent_manager.get_runtime(agent_id)
        if runtime is not None and hasattr(runtime, "channel_registry"):
            cr = runtime.channel_registry
            if cr is not None:
                cr.register(adapter.name, adapter)
    except Exception:
        pass


def resolve_workspace_file(agent_id: str, file_path: str) -> Path | None:
    try:
        from server.main import app
        am = getattr(app.state, "agent_manager", None)
        if am is None:
            return None
        workspace = am.agents_dir / agent_id / "workspace"
        resolved = (workspace / file_path).resolve()
        if not str(resolved).startswith(str(workspace.resolve())):
            logger.warning("Channel: path traversal blocked: %s", file_path)
            return None
        if not resolved.is_file():
            return None
        return resolved
    except Exception:
        return None


def broadcast_channel_event(
    app: Any,
    agent_id: str,
    channel: str,
    normalized: dict[str, Any],
    response: str = "",
    *,
    direction: str = "inbound",
) -> None:
    cm = getattr(app.state, "connection_manager", None)
    if cm is None:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cm.broadcast(agent_id, {
            "type": "channel_event",
            "channel": channel,
            "direction": direction,
            "sender": normalized.get("sender_name", "?"),
            "content": normalized.get("content", ""),
            "content_preview": (normalized.get("content") or "")[:100],
            "session_key": normalized.get("session_key", ""),
            "channel_name": (normalized.get("metadata") or {}).get("channel_name", ""),
            "guild_name": (normalized.get("metadata") or {}).get("guild_name", ""),
            "subject": (normalized.get("metadata") or {}).get("subject") or normalized.get("subject") or "",
            "response": response,
            "response_preview": response[:100] if response else "",
        }))
    except Exception:
        pass


def broadcast_group_ambient_inbound(
    app: Any,
    agent_id: str,
    channel: str,
    normalized: dict[str, Any],
) -> None:
    """Push untriggered group traffic to the UI as ambient timeline entries."""
    if not normalized.get("is_group"):
        return
    content = (normalized.get("content") or "").strip()
    if not content:
        if normalized.get("attachments"):
            content = "[media]"
        else:
            return
    if content == "[empty]":
        return
    payload = normalized
    if content == "[media]" and not (normalized.get("content") or "").strip():
        payload = {**normalized, "content": content}
    broadcast_channel_event(app, agent_id, channel, payload, direction="ambient")


def chunk_message(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at < max_len // 2:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
