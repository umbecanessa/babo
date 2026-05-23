"""Email inbound webhook, activation, and processing.

Activation is triggered by the NestJS backend which owns the Resend SDK.
Inbound webhooks are forwarded by NestJS with the full email content
already fetched (in ``_full_email``).  The same processing logic is also
called by the background poller for messages that were queued when the
local runtime was unreachable.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["email-channel"])

_SIGNAL_TAG_RE = re.compile(r"\[(?:[A-Za-z_]+)(?:[:.](?:[^\]]*))?\]\s*")


def _strip_signal_tags(text: str) -> str:
    """Remove ANS behavioral tags like [EVALUATE:correct], [ACC:Pleased] etc."""
    return _SIGNAL_TAG_RE.sub("", text).strip()


# ── Shared processing logic ────────────────────────────────────

async def process_inbound_email(
    app: Any,
    agent_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Process an inbound email payload (from webhook or poller).

    *body* is the full payload (Resend webhook format with optional
    ``_full_email`` enrichment from NestJS).
    """
    data = body.get("data", body)
    full_email = body.get("_full_email")

    sender = data.get("from", "")
    subject = data.get("subject", "")

    text = ""
    headers: dict[str, str] = {}
    message_id = data.get("message_id", "")

    if full_email:
        text = full_email.get("text", "") or full_email.get("body", "")
        headers = full_email.get("headers", {})
        message_id = full_email.get("message_id", message_id)
        if not sender:
            sender = full_email.get("from", "")
        if not subject:
            subject = full_email.get("subject", "")
    else:
        text = data.get("text", "") or data.get("body", "")
        headers = data.get("headers", {})

    if not sender or not text:
        return {"ok": True, "status": "no_content"}

    adapter = _get_email_adapter(app)
    if adapter is None:
        return {"ok": False, "error": "email skill not loaded"}

    if not adapter.should_respond(sender, agent_id=agent_id):
        logger.info("Email [%s]: policy rejected from %s", agent_id, sender)
        return {"ok": True, "status": "policy_rejected"}

    adapter.register_known_sender(sender, agent_id)

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        return {"ok": False, "error": "agent_manager not ready"}

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        return {"ok": False, "error": f"agent {agent_id} not found"}

    normalized = adapter.normalize_inbound(
        sender=sender,
        subject=subject,
        body=text,
        headers=headers,
        message_id=message_id,
    )

    raw_attachments = []
    if full_email:
        raw_attachments = full_email.get("attachments", [])
    if not raw_attachments:
        raw_attachments = data.get("attachments", [])

    saved_attachments: list[dict[str, Any]] = []
    if raw_attachments:
        saved_attachments = _save_email_attachments(
            raw_attachments, agent_id, app,
        )
    if saved_attachments:
        normalized["attachments"] = saved_attachments

    # Record to email ledger
    try:
        from nls.tools.agent_tools.email_ledger import get_email_ledger
        _ledger = get_email_ledger(agent_id)
        if _ledger:
            _cfg = adapter._agent_cfg(agent_id)
            _to = _cfg.get("from_address", "") or _cfg.get("alias", "")
            _ledger.record_received(
                from_addr=sender,
                to=_to,
                subject=subject,
                body=text,
                message_id=message_id,
                in_reply_to=headers.get("In-Reply-To", headers.get("in-reply-to", "")),
                cc=headers.get("Cc", headers.get("cc", "")),
            )
    except Exception as _le:
        logger.debug("email_ledger record_received failed: %s", _le)

    msg_type = normalized.get("message_type", "chat")

    if msg_type == "content":
        return await _handle_content_ingestion(
            runtime, adapter, normalized, sender, subject,
        )

    return await _handle_conversation(
        app, runtime, adapter, normalized, sender, subject,
    )


def _save_email_attachments(
    raw_attachments: list[dict[str, Any]],
    agent_id: str,
    app: Any,
) -> list[dict[str, Any]]:
    """Decode and save email attachments to the agent workspace."""
    try:
        am = getattr(app.state, "agent_manager", None)
        if am is None:
            return []
    except Exception:
        return []

    uploads = am.agents_dir / agent_id / "workspace" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    saved: list[dict[str, Any]] = []
    for att in raw_attachments:
        content_b64 = att.get("content", "")
        if not content_b64:
            continue
        filename = att.get("filename") or att.get("name") or f"attachment_{int(time.time())}"
        mime = att.get("content_type") or att.get("mime_type") or "application/octet-stream"

        try:
            raw = base64.b64decode(content_b64)
        except Exception:
            continue

        dest = uploads / filename
        dest.write_bytes(raw)

        saved.append({
            "name": filename,
            "path": f"uploads/{filename}",
            "mime_type": mime,
            "size": len(raw),
            "is_voice": False,
        })

    return saved


# ── Activation (called by NestJS after provisioning alias) ─────

def _get_email_adapter(app: Any) -> Any:
    """Resolve the shared EmailAdapter singleton from the skill loader."""
    skill_loader = getattr(app.state, "skill_loader", None)
    if not skill_loader:
        return None
    sk = skill_loader.skills.get("email-channel")
    if sk and sk.context:
        return sk.context.adapter
    return None


@router.post("/activate/{agent_id}")
async def email_activate(agent_id: str, request: Request):
    """Accept an alias provisioned by the NestJS backend."""
    app = request.app

    try:
        body = await request.json()
    except Exception:
        body = {}

    alias = body.get("alias", "")
    from_address = body.get("from_address", "")

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    if not alias:
        return {"ok": False, "detail": "No alias provided"}

    adapter = _get_email_adapter(app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Email adapter not loaded")

    adapter.update_config({
        "enabled": True,
        "alias": alias,
        "from_address": from_address or alias,
    }, agent_id=agent_id)

    await adapter.startup_agent(agent_id)

    return {"ok": True, "alias": alias, "from_address": from_address}


# ── Inbound webhook (forwarded by NestJS) ─────────────────────

@router.post("/webhook/{agent_id}")
async def email_inbound(agent_id: str, request: Request):
    """Receive an inbound email for *agent_id*.

    Called by NestJS or directly during local development.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    result = await process_inbound_email(request.app, agent_id, body)

    if not result.get("ok", True) and "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])

    return result


# ── Status ─────────────────────────────────────────────────────

@router.get("/status/{agent_id}")
async def email_status(agent_id: str, request: Request):
    app = request.app
    adapter = _get_email_adapter(app)
    if adapter is not None:
        return adapter.get_status(agent_id=agent_id)

    return {"channel": "email", "connected": False, "enabled": False}


# ── Internal handlers ──────────────────────────────────────────

async def _handle_conversation(
    app: Any,
    runtime: Any,
    adapter: Any,
    normalized: dict[str, Any],
    sender: str,
    subject: str,
) -> dict[str, Any]:
    import asyncio

    session_key = normalized["session_key"]
    text = normalized["content"]
    agent_id = getattr(runtime, "agent_id", "")

    history = runtime.load_session_history(session_key)

    _broadcast_channel_event(app, agent_id, normalized, response="", direction="inbound")

    try:
        from nls.skills.channel_processing import process_channel_message
        user_input = f"[Email from {sender}] Subject: {subject}\n\n{text}"
        attachments = normalized.get("attachments") or []
        response_text = await process_channel_message(
            app, runtime, agent_id, user_input, history,
            channel_adapter=adapter,
            reply_target=sender,
            session_key=session_key,
            attachments=attachments,
        )
        if response_text:
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": response_text})

        if response_text:
            runtime.save_session_history(
                history, session_key=session_key,
                metadata={"channel": "email", "sender": sender, "subject": subject},
            )

            clean_response = _strip_signal_tags(response_text)
            if not clean_response:
                logger.warning(
                    "Email [%s]: response became empty after signal-tag "
                    "stripping — using original. Original: %s",
                    agent_id, response_text[:200],
                )
                clean_response = response_text.strip()

            if clean_response:
                in_reply_to = normalized.get("message_id", "") or normalized["metadata"].get("in_reply_to", "")
                references = normalized["metadata"].get("references", "")
                reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
                await adapter.send(
                    sender,
                    clean_response,
                    subject=reply_subject,
                    in_reply_to=in_reply_to,
                    references=references,
                    agent_id=agent_id,
                )

        _broadcast_channel_event(app, agent_id, normalized, response_text, direction="response")

        return {
            "ok": True,
            "type": "conversation",
            "response_length": len(response_text),
            "session_key": session_key,
        }
    except Exception as exc:
        logger.error("Email processing failed: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}


async def _handle_content_ingestion(
    runtime: Any,
    adapter: Any,
    normalized: dict[str, Any],
    sender: str,
    subject: str,
) -> dict[str, Any]:
    text = normalized["content"]

    if hasattr(runtime, "ingest_content"):
        import asyncio
        result = await asyncio.to_thread(
            runtime.ingest_content,
            content=text,
            metadata={
                "source": "email",
                "sender": sender,
                "subject": subject,
            },
            session_key=normalized["session_key"],
        )
        return {"ok": True, "type": "content_ingestion", "result": result}

    logger.info("Email content ingestion: study pipeline not available")
    return {"ok": True, "type": "content_skipped", "reason": "ingest_content not available"}


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
    clean_response = _strip_signal_tags(response) if response else ""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cm.broadcast(agent_id, {
            "type": "channel_event",
            "channel": "email",
            "direction": direction,
            "sender": normalized["sender_name"],
            "subject": normalized["metadata"].get("subject", ""),
            "content": normalized["content"],
            "content_preview": normalized["content"][:100],
            "response": clean_response,
            "session_key": normalized["session_key"],
            "message_type": normalized.get("message_type", "chat"),
        }))
    except Exception:
        pass
