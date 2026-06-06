"""Telegram inbound webhook -- receives updates from Telegram Bot API.

Mounted at ``/skills/telegram-channel/webhook/{agent_id}`` by the
skill's ``register()`` function.  Normalizes the update, enforces
policies, and routes to the agent's process pipeline with proper
session isolation.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

_SIGNAL_TAG_RE = re.compile(r"\[(?:[A-Za-z_]+)(?:[:.](?:[^\]]*))?\]\s*")

logger = logging.getLogger(__name__)
router = APIRouter(tags=["telegram-channel"])


def _get_adapter(app: Any) -> Any | None:
    """Resolve the Telegram adapter from the skill loader."""
    sl = getattr(app.state, "skill_loader", None)
    if sl is None:
        return None
    sk = sl.skills.get("telegram-channel")
    if sk and sk.context:
        return sk.context.adapter
    return None


@router.post("/webhook/{agent_id}")
async def telegram_inbound(agent_id: str, request: Request):
    """Receive a Telegram webhook update for *agent_id*."""
    app = request.app

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(
        "Telegram inbound [%s]: body keys=%s, has message=%s, size=%d",
        agent_id,
        list(body.keys()) if isinstance(body, dict) else type(body).__name__,
        "message" in body if isinstance(body, dict) else False,
        len(str(body)),
    )

    message = body.get("message") or body.get("edited_message")
    if not message:
        logger.warning(
            "Telegram inbound [%s]: no 'message' key in body — "
            "returning no_message (body_type=%s, keys=%s)",
            agent_id,
            type(body).__name__,
            list(body.keys())[:10] if isinstance(body, dict) else "N/A",
        )
        return {"ok": True, "status": "no_message"}

    chat = message.get("chat", {})
    preview = (message.get("text") or message.get("caption") or "")[:120]
    entity_types = [e.get("type") for e in message.get("entities", [])]
    logger.info(
        "Telegram inbound [%s]: chat=%s type=%s text=%r entities=%s",
        agent_id,
        chat.get("id"),
        chat.get("type"),
        preview or "[no text]",
        entity_types or "none",
    )

    adapter = _get_adapter(app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Telegram skill not loaded")

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    normalized = adapter.normalize(body, agent_id=agent_id)
    if normalized is None:
        chat = message.get("chat", {})
        logger.info(
            "Telegram [%s]: skipped non-text update (chat=%s type=%s keys=%s)",
            agent_id,
            chat.get("id"),
            chat.get("type"),
            [k for k in message.keys() if k not in ("chat", "from", "date")][:8],
        )
        return {"ok": True, "status": "skip"}

    from nls.skills.channel_ambient import record_inbound_ambient, record_outbound_ambient

    will_respond = adapter.should_respond(message, agent_id=agent_id)
    record_inbound_ambient(runtime, normalized, triggered=will_respond)

    if not will_respond:
        sender = message.get("from", {})
        chat = message.get("chat", {})
        preview = (message.get("text") or message.get("caption") or "")[:120]
        is_mention = adapter._is_mention(
            message, message.get("text", "") or message.get("caption", ""), agent_id,
        )
        logger.info(
            "Telegram [%s]: policy REJECTED message from %s (@%s) in chat=%s (%s) "
            "mention=%s text=%r",
            agent_id,
            normalized["sender_id"],
            sender.get("username", "?"),
            chat.get("id"),
            chat.get("type", "?"),
            is_mention,
            preview,
        )
        return {"ok": True, "status": "policy_rejected"}

    adapter.register_known_sender(normalized["metadata"]["chat_id"], agent_id)

    session_key = normalized["session_key"]
    text = normalized["content"]
    chat_id = normalized["metadata"]["chat_id"]
    sender_name = normalized["sender_name"]
    attachments = normalized.get("attachments") or []

    logger.info(
        "Telegram [%s]: processing message from %s: %s",
        agent_id, sender_name, (text or "[media]")[:80],
    )

    history = runtime.load_session_history(session_key)

    from nls.skills.surface_send import channel_session_metadata
    session_meta = channel_session_metadata(normalized)

    from nls.skills.channel_adapter_util import channel_history_content, prepare_channel_outbound

    runtime.save_session_history(
        history + [{"role": "user", "content": channel_history_content(text, attachments)}],
        session_key=session_key,
        metadata=session_meta,
    )

    _broadcast_channel_event(app, agent_id, normalized, response="", direction="inbound")

    try:
        from nls.skills.channel_processing import (
            process_channel_message,
            try_feed_pending_answer_async,
        )

        if await try_feed_pending_answer_async(
            agent_id, session_key, text, attachments=attachments, app=app,
        ):
            return {"ok": True, "status": "answer_routed"}

        from nls.skills.channel_attachments import (
            deliver_channel_reply,
            note_attachment_download_gaps,
            telegram_inbound_media_count,
        )

        user_input = f"[{sender_name} via Telegram]: {text}" if text else f"[{sender_name} via Telegram]:"
        user_input = note_attachment_download_gaps(
            user_input,
            expected=telegram_inbound_media_count(message),
            saved=len(attachments),
            labels=[a.get("name", "file") for a in attachments],
        )
        response_text = await process_channel_message(
            app, runtime, agent_id, user_input, history,
            channel_adapter=adapter,
            reply_target=chat_id,
            session_key=session_key,
            attachments=attachments,
            sender_name=sender_name,
            raw_content=text or "[media]",
        )
        if response_text:
            user_content = channel_history_content(text, attachments)
            history.append({"role": "user", "content": user_content})
            history.append({"role": "assistant", "content": response_text})

        clean_response = prepare_channel_outbound(response_text or "")

        if response_text and response_text.strip() and not clean_response:
            logger.warning(
                "Telegram [%s]: blocked tool-call leak outbound (%r)",
                agent_id,
                response_text[:120],
            )

        if not clean_response:
            logger.warning(
                "Telegram [%s]: empty response for message from %s: %s",
                agent_id, sender_name, text[:80],
            )

        if clean_response:
            runtime.save_session_history(
                history, session_key=session_key,
                metadata=session_meta,
            )
            await deliver_channel_reply(
                adapter, chat_id, clean_response, response_text or "",
                agent_id=agent_id,
            )
            record_outbound_ambient(runtime, normalized, clean_response)

        _broadcast_channel_event(app, agent_id, normalized, clean_response, direction="response")

        return {
            "ok": True,
            "response_length": len(response_text),
            "session_key": session_key,
        }

    except Exception as exc:
        logger.error(
            "Telegram webhook processing failed for agent %s: %s",
            agent_id, exc, exc_info=True,
        )
        try:
            await adapter.send(
                chat_id,
                "Sorry, I encountered an error processing your message.",
                agent_id=agent_id,
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/setup/{agent_id}")
async def telegram_setup(agent_id: str, request: Request):
    """Validate a bot token and configure Telegram for an agent.

    POST body::

        {
            "bot_token": "...",
            "webhook_url": "...",           // optional
        }
    """
    app = request.app
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    bot_token = body.get("bot_token", "")
    webhook_url = body.get("webhook_url", "")

    if not bot_token:
        raise HTTPException(status_code=400, detail="bot_token required")

    adapter = _get_adapter(app)

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{bot_token}/getMe",
            )
            resp.raise_for_status()
            me_data = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid bot token -- Telegram API error: {exc}",
        )

    bot_username = me_data.get("result", {}).get("username", "")

    if adapter is not None:
        cfg_update: dict[str, Any] = {
            "bot_token": bot_token,
            "enabled": True,
        }
        adapter.update_config(cfg_update, agent_id=agent_id)
        adapter._bot_usernames[agent_id] = bot_username
        adapter._connected_agents.add(agent_id)
        adapter._register_with_agent(agent_id)

    webhook_set = False
    if webhook_url:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/setWebhook",
                    json={"url": f"{webhook_url}/skills/telegram-channel/webhook/{agent_id}"},
                )
                resp.raise_for_status()
                webhook_set = True
        except Exception as exc:
            logger.warning("Failed to set Telegram webhook: %s", exc)

    return {
        "ok": True,
        "bot_username": bot_username,
        "credentials_stored": True,
        "webhook_set": webhook_set,
    }


@router.get("/status/{agent_id}")
async def telegram_status(agent_id: str, request: Request):
    """Get Telegram channel status for an agent."""
    app = request.app
    adapter = _get_adapter(app)
    if adapter is not None:
        return adapter.get_status(agent_id=agent_id)

    return {"channel": "telegram", "connected": False, "enabled": False}


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
            "channel": "telegram",
            "direction": direction,
            "sender": normalized["sender_name"],
            "content": normalized["content"],
            "content_preview": normalized["content"][:100],
            "session_key": normalized["session_key"],
            "response": response,
            "response_preview": response[:100] if response else "",
        }))
    except Exception:
        pass
