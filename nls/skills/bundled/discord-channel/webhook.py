"""Discord inbound webhook -- REST fallback endpoint for NestJS relay.

Mounted at ``/skills/discord-channel/webhook/{agent_id}`` by the
skill's ``register()`` function. Used primarily when Discord messages
arrive via the NestJS cloud relay (e.g. webhook proxy mode).

Primary inbound path is through discord.py Gateway WebSocket events
handled directly in ``adapter.py``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

SIGNAL_TAG_RE = re.compile(r"\[(?:[A-Za-z_]+)(?:[:.](?:[^\]]*))?\]\s*")

logger = logging.getLogger(__name__)
router = APIRouter(tags=["discord-channel"])


def _get_adapter(app: Any) -> Any | None:
    """Resolve the Discord adapter from the skill loader."""
    sl = getattr(app.state, "skill_loader", None)
    if sl is None:
        return None
    sk = sl.skills.get("discord-channel")
    if sk and sk.context:
        return sk.context.adapter
    return None


@router.post("/webhook/{agent_id}")
async def discord_inbound(agent_id: str, request: Request):
    """Receive a Discord webhook update for *agent_id*.

    This endpoint is used when Discord webhooks (via Slash commands or
    interactions) land on NestJS and are relayed here. The primary real-time
    path uses discord.py Gateway events instead.
    """
    app = request.app

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(
        "Discord inbound [%s]: body keys=%s, size=%d",
        agent_id,
        list(body.keys()) if isinstance(body, dict) else type(body).__name__,
        len(str(body)),
    )

    # Try to extract message content from various Discord interaction formats
    message = None
    if "message" in body:
        message = body["message"]
    elif "interaction" in body and "data" in body.get("interaction", {}):
        # Slash command submission
        message = body["interaction"]["data"].get("options", [{}])[-1] if body["interaction"]["data"].get("options") else {}
    elif "message_reference" in body:
        message = body["message_reference"]
    elif "content" in body:
        # Direct message payload
        message = body

    if not message:
        logger.warning(
            "Discord inbound [%s]: no recognizable message structure — "
            "returning ok",
            agent_id,
        )
        return {"ok": True, "status": "no_message"}

    adapter = _get_adapter(app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Discord skill not loaded")

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    # Normalize the message payload
    normalized = adapter.normalize_webhook(body, agent_id=agent_id)
    if normalized is None:
        return {"ok": True, "status": "skip"}

    # Enforce DM/channel policy
    dm_check = normalized.get("is_dm", False)
    allowed = adapter.get_allowed_channel_ids(agent_id)
    if dm_check and allowed == set():
        logger.warning(
            "Discord [%s]: DM policy REJECTED message from %s",
            agent_id,
            normalized.get("sender_name", "?"),
        )
        return {"ok": True, "status": "policy_rejected"}

    session_key = normalized["session_key"]
    text = normalized.get("content", "") or ""
    chat_id = normalized.get("channel_id", "")
    sender_name = normalized.get("sender_name", "?")
    attachments = normalized.get("attachments") or []

    logger.info(
        "Discord [%s]: processing message from %s: %s",
        agent_id, sender_name, (text or "[media]")[:80],
    )

    history = runtime.load_session_history(session_key)

    runtime.save_session_history(
        history + [{"role": "user", "content": text or "[media]"}],
        session_key=session_key,
        metadata={"channel": "discord", "sender": sender_name},
    )

    _broadcast_channel_event(app, agent_id, normalized, response="", direction="inbound")

    try:
        from nls.skills.channel_processing import (
            process_channel_message,
            try_feed_pending_answer,
        )

        if try_feed_pending_answer(agent_id, session_key, text):
            return {"ok": True, "status": "answer_routed"}

        user_input = f"[{sender_name} via Discord]: {text}" if text else f"[{sender_name} via Discord]:"
        response_text = await process_channel_message(
            app, runtime, agent_id, user_input, history,
            channel_adapter=adapter,
            reply_target=chat_id,
            session_key=session_key,
            attachments=attachments,
        )

        if response_text:
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": response_text})

        clean_response = SIGNAL_TAG_RE.sub("", response_text).strip() if response_text else ""

        if clean_response:
            runtime.save_session_history(
                history, session_key=session_key,
                metadata={"channel": "discord", "sender": sender_name},
            )
            # Route response back through the adapter
            if hasattr(adapter, "send_message"):
                await adapter.send_message(chat_id, clean_response)

        _broadcast_channel_event(app, agent_id, normalized, clean_response, direction="response")

        return {
            "ok": True,
            "response_length": len(response_text) if response_text else 0,
            "session_key": session_key,
        }

    except Exception as exc:
        logger.error(
            "Discord webhook processing failed for agent %s: %s",
            agent_id, exc, exc_info=True,
        )
        try:
            if hasattr(adapter, "send_message"):
                await adapter.send_message(
                    chat_id,
                    "Sorry, I encountered an error processing your message.",
                )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/setup/{agent_id}")
async def discord_setup(agent_id: str, request: Request):
    """Validate a Discord bot token and configure the connection.

    POST body::

        {
            "bot_token": "...",
        }
    """
    app = request.app
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    bot_token = body.get("bot_token", "")

    if not bot_token:
        raise HTTPException(status_code=400, detail="bot_token required")

    adapter = _get_adapter(app)

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {bot_token}"},
            )
            resp.raise_for_status()
            me_data = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid bot token -- Discord API error: {exc}",
        )

    bot_username = me_data.get("user", {}).get("username", me_data.get("username", ""))

    if adapter is not None:
        cfg_update: dict[str, Any] = {
            "bot_token": bot_token,
            "enabled": True,
            "bot_username": bot_username,
        }
        adapter.update_config(cfg_update, agent_id=agent_id)
        adapter._bot_username = bot_username
        adapter._connected_agents.add(agent_id)

    return {
        "ok": True,
        "bot_username": bot_username,
        "credentials_stored": True,
        "gateway_ready": adapter._running if adapter else False,
    }


@router.get("/status/{agent_id}")
async def discord_status(agent_id: str, request: Request):
    """Get Discord channel status for an agent."""
    app = request.app
    adapter = _get_adapter(app)
    if adapter is not None:
        return {
            "channel": "discord",
            "connected": adapter._running and adapter._bot is not None,
            "bot_username": adapter._bot_username or "",
            "guild_count": len(getattr(adapter._bot, "guilds", [])) if adapter._bot else 0,
            "enabled": bool(adapter._config.get("bot_token")),
        }

    return {"channel": "discord", "connected": False, "enabled": False}


def _broadcast_channel_event(
    app: Any,
    agent_id: str,
    normalized: dict[str, Any],
    response: str,
    direction: str = "inbound",
) -> None:
    """Notify the frontend about inbound/outbound channel messages."""
    cm = getattr(app.state, "connection_manager", None)
    if cm is None:
        return
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cm.broadcast(agent_id, {
            "type": "channel_event",
            "channel": "discord",
            "direction": direction,
            "sender": normalized.get("sender_name", "?"),
            "content": normalized.get("content", ""),
            "content_preview": (normalized.get("content", "") or "")[:100],
            "session_key": normalized.get("session_key", ""),
            "response": response,
            "response_preview": response[:100] if response else "",
        }))
    except Exception:
        pass
