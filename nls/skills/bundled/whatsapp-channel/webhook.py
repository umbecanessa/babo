"""WhatsApp webhook and QR pairing endpoints.

The Baileys Node.js bridge forwards inbound messages here.
The frontend calls /pair/{agent_id} to initiate QR pairing.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

_SIGNAL_TAG_RE = re.compile(r"\[(?:[A-Za-z_]+)(?:[:.](?:[^\]]*))?\]\s*")

logger = logging.getLogger(__name__)
router = APIRouter(tags=["whatsapp-channel"])

# Dedup: Baileys bridge can fire the webhook more than once for the same
# message (e.g. receipt + delivery).  Keep a bounded set of recent IDs.
_seen_message_ids: dict[str, float] = {}
_SEEN_MAX = 200
_SEEN_TTL = 120.0  # seconds


def _get_adapter(app: Any) -> Any | None:
    """Resolve the WhatsApp adapter from the skill loader.

    Works at any point after boot -- unlike ``channel_registry`` which
    only contains adapters that have completed startup/pairing.
    """
    sl = getattr(app.state, "skill_loader", None)
    if sl is None:
        return None
    sk = sl.skills.get("whatsapp-channel")
    if sk is None or sk.context is None:
        return None
    return sk.context.adapter


def _resolve_agent_id(
    agent_id: str,
    adapter: Any,
    agent_manager: Any,
) -> str | None:
    """Resolve a potentially stale agent_id (e.g. ``"default"``) to a real one.

    Uses the adapter's phone→agent map first, then falls back to checking
    which agents have WhatsApp enabled.
    """
    resolved = adapter.resolve_agent_for_bridge()
    if resolved and agent_manager.get_runtime(resolved) is not None:
        return resolved
    return None


# ── QR Pairing ─────────────────────────────────────────────────

@router.post("/pair/{agent_id}")
async def whatsapp_pair(agent_id: str, request: Request):
    """Start a WhatsApp pairing session and return the QR code.

    The frontend displays this QR code for the user to scan with
    their WhatsApp mobile app.  Returns base64-encoded QR image.
    """
    app = request.app
    adapter = _get_adapter(app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="WhatsApp skill not loaded")

    bridge_url = adapter._bridge_url(agent_id)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                f"{bridge_url}/configure",
                json={"agent_id": agent_id},
            )
            resp = await client.post(f"{bridge_url}/pair/{agent_id}")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"WhatsApp bridge not available: {exc}",
        )

    return {
        "ok": True,
        "qr": data.get("qr", ""),
        "status": data.get("status", "waiting"),
        "phone": data.get("phone", ""),
    }


@router.get("/qr/{agent_id}")
async def whatsapp_qr(agent_id: str, request: Request):
    """Get the current QR code (if pairing is in progress).

    Also returns ``phone`` when status becomes ``connected`` so the
    frontend can show the linked number immediately.
    """
    app = request.app
    adapter = _get_adapter(app)
    if adapter is None:
        return {"qr": "", "status": "not_registered"}

    bridge_url = adapter._bridge_url(agent_id)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{bridge_url}/status/{agent_id}")
            resp.raise_for_status()
            data = resp.json()

            if data.get("connected"):
                phone = data.get("phone", "")
                if phone:
                    adapter.update_config({
                        "enabled": True,
                        "linked_phone": phone,
                    }, agent_id=agent_id)
                    adapter._connected_agents.add(agent_id)
                    adapter.register_phone(phone, agent_id)
                    adapter._register_with_agent(agent_id)
                return {
                    "qr": "",
                    "status": "connected",
                    "phone": phone,
                }

            qr_resp = await client.get(f"{bridge_url}/qr/{agent_id}")
            qr_resp.raise_for_status()
            qr_data = qr_resp.json()
            return {
                "qr": qr_data.get("qr", ""),
                "status": qr_data.get("status", "connecting"),
            }
    except Exception:
        return {"qr": "", "status": "bridge_unavailable"}


# ── Inbound webhook (from Baileys bridge) ──────────────────────

@router.post("/webhook/{agent_id}")
async def whatsapp_inbound(agent_id: str, request: Request):
    """Receive an inbound WhatsApp message from the Baileys bridge.

    Expected JSON body::

        {
            "from": "15551234567@s.whatsapp.net",
            "name": "Alice",
            "text": "Hello!",
            "isGroup": false,
            "groupId": null,
            "messageId": "...",
            "timestamp": 1234567890
        }
    """
    app = request.app

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    import time as _time
    msg_id = body.get("messageId") or body.get("message_id") or ""
    if msg_id:
        now = _time.time()
        if msg_id in _seen_message_ids:
            logger.debug("WhatsApp: duplicate messageId %s — skipping", msg_id)
            return {"ok": True, "status": "duplicate"}
        _seen_message_ids[msg_id] = now
        if len(_seen_message_ids) > _SEEN_MAX:
            cutoff = now - _SEEN_TTL
            stale = [k for k, t in _seen_message_ids.items() if t < cutoff]
            for k in stale:
                del _seen_message_ids[k]

    adapter = _get_adapter(app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="WhatsApp skill not loaded")

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        bridge_phone = body.get("bridge_phone", "")
        if bridge_phone and bridge_phone in adapter._phone_agent_map:
            agent_id = adapter._phone_agent_map[bridge_phone]
            runtime = agent_manager.get_runtime(agent_id)
        if runtime is None:
            resolved = _resolve_agent_id(agent_id, adapter, agent_manager)
            if resolved:
                agent_id = resolved
                runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    normalized = adapter.normalize(body, agent_id=agent_id)
    if normalized is None:
        return {"ok": True, "status": "skip"}

    phone = normalized["sender_id"]
    reply_jid = normalized.get("sender_jid", phone)
    is_group = normalized["is_group"]

    from nls.skills.channel_ambient import record_inbound_ambient, record_outbound_ambient

    will_respond = adapter.should_respond(phone, is_group=is_group, agent_id=agent_id)
    record_inbound_ambient(runtime, normalized, triggered=will_respond)

    if not will_respond:
        logger.debug("WhatsApp: policy rejected message from %s", phone)
        if is_group:
            from nls.skills.channel_adapter_util import broadcast_group_ambient_inbound
            broadcast_group_ambient_inbound(app, agent_id, "whatsapp", normalized)
        return {"ok": True, "status": "policy_rejected"}

    session_key = normalized["session_key"]
    text = normalized["content"]
    sender_name = normalized["sender_name"]

    adapter.register_known_sender(reply_jid, agent_id, name=sender_name)
    attachments = normalized.get("attachments") or []

    history = runtime.load_session_history(session_key)

    from nls.skills.surface_send import channel_session_metadata
    session_meta = channel_session_metadata(normalized)

    from nls.skills.channel_adapter_util import channel_history_content, prepare_channel_outbound

    runtime.save_session_history(
        history + [{"role": "user", "content": channel_history_content(text, attachments)}],
        session_key=session_key,
        metadata=session_meta,
    )

    # Broadcast an immediate "inbound" event so the frontend shows the
    # new message in the sidebar/thread right away — don't wait for the
    # full agentic processing to finish.
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
            whatsapp_inbound_media_count,
        )

        user_input = f"[{sender_name} via WhatsApp]: {text}" if text else f"[{sender_name} via WhatsApp]:"
        user_input = note_attachment_download_gaps(
            user_input,
            expected=whatsapp_inbound_media_count(body),
            saved=len(attachments),
            labels=[a.get("name", "file") for a in attachments],
        )
        response_text = await process_channel_message(
            app, runtime, agent_id, user_input, history,
            channel_adapter=adapter,
            reply_target=reply_jid,
            session_key=session_key,
            attachments=attachments,
            sender_name=sender_name,
            raw_content=text or "[media]",
        )
        if response_text:
            user_content = channel_history_content(text, attachments)
            history.append({"role": "user", "content": user_content})
            history.append({"role": "assistant", "content": response_text})

        from nls.skills.channel_adapter_util import prepare_channel_outbound

        clean_response = prepare_channel_outbound(response_text or "")

        if response_text and response_text.strip() and not clean_response:
            logger.warning(
                "WhatsApp [%s]: blocked tool-call leak outbound (%r)",
                agent_id,
                response_text[:120],
            )

        if not clean_response:
            logger.warning(
                "WhatsApp [%s]: empty response for message from %s: %s",
                agent_id, sender_name, text[:80],
            )

        if clean_response:
            runtime.save_session_history(
                history, session_key=session_key,
                metadata=session_meta,
            )
            await deliver_channel_reply(
                adapter, reply_jid, clean_response, response_text or "",
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
            "WhatsApp processing failed for agent %s: %s",
            agent_id, exc, exc_info=True,
        )
        try:
            await adapter.send(reply_jid, "Sorry, I encountered an error processing your message.", agent_id=agent_id)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))


# ── Status ─────────────────────────────────────────────────────

@router.get("/status/{agent_id}")
async def whatsapp_status(agent_id: str, request: Request):
    """Get WhatsApp channel status."""
    app = request.app
    adapter = _get_adapter(app)
    if adapter is not None:
        status = adapter.get_status(agent_id=agent_id)
        logger.info(
            "WhatsApp status [%s]: connected=%s, enabled=%s, connected_agents=%s",
            agent_id, status.get("connected"), status.get("enabled"),
            list(adapter._connected_agents) if hasattr(adapter, "_connected_agents") else "?",
        )
        return status

    logger.info("WhatsApp status [%s]: no adapter loaded", agent_id)
    return {"channel": "whatsapp", "connected": False, "enabled": False}


@router.post("/connected/{agent_id}")
async def whatsapp_connected_notify(agent_id: str, request: Request):
    """Called by the Baileys bridge when it auto-connects from persisted auth.

    Updates ``_connected_agents`` so the frontend status is correct even
    when the bridge starts *after* the Python adapter's startup probe.

    If ``agent_id`` is ``"default"`` (bridge wasn't configured yet), resolves
    to the real agent via the phone→agent mapping.
    """
    app = request.app
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    adapter = _get_adapter(app)
    if adapter is None:
        return {"ok": False, "reason": "adapter_not_loaded"}

    phone = body.get("phone", "")

    if agent_id == "default":
        resolved = adapter.resolve_agent_for_bridge()
        if resolved:
            agent_id = resolved
            logger.info("Resolved 'default' agent_id → %s via phone map", agent_id)

    adapter._connected_agents.add(agent_id)
    if phone:
        adapter._agent_configs.setdefault(agent_id, {})["linked_phone"] = phone
        if agent_id != "default":
            adapter.register_phone(phone, agent_id)
            adapter.update_config({"enabled": True, "linked_phone": phone}, agent_id=agent_id)
    adapter._register_with_agent(agent_id)
    logger.info("WhatsApp connected notification [%s]: phone=%s", agent_id, phone)
    return {"ok": True}


def _broadcast_channel_event(
    app: Any,
    agent_id: str,
    normalized: dict[str, Any],
    response: str,
    direction: str = "inbound",
) -> None:
    cm = getattr(app.state, "connection_manager", None)
    if cm is None:
        return
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cm.broadcast(agent_id, {
            "type": "channel_event",
            "channel": "whatsapp",
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
