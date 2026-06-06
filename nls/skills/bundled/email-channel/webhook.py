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

    raw_attachments_preview: list[Any] = []
    if full_email:
        raw_attachments_preview = list(full_email.get("attachments") or [])
    if not raw_attachments_preview:
        raw_attachments_preview = list(data.get("attachments") or [])

    if not sender or (not text and not raw_attachments_preview):
        return {"ok": True, "status": "no_content"}

    adapter = _get_email_adapter(app)
    if adapter is None:
        return {"ok": False, "error": "email skill not loaded"}

    normalized = adapter.normalize_inbound(
        sender=sender,
        subject=subject,
        body=text,
        headers=headers,
        message_id=message_id,
        agent_id=agent_id,
    )

    if not adapter.should_respond(sender, agent_id=agent_id, headers=headers):
        logger.info("Email [%s]: policy rejected from %s", agent_id, sender)
        _broadcast_policy_skip(app, agent_id, normalized, reason="Sender not allowed by email policy")
        return {"ok": True, "status": "policy_rejected"}

    adapter.register_known_sender(sender, agent_id)

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        return {"ok": False, "error": "agent_manager not ready"}

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        return {"ok": False, "error": f"agent {agent_id} not found"}

    raw_attachments = []
    if full_email:
        raw_attachments = full_email.get("attachments", [])
    if not raw_attachments:
        raw_attachments = data.get("attachments", [])

    saved_attachments: list[dict[str, Any]] = []
    email_id = str(data.get("email_id") or (full_email or {}).get("id") or "")
    if raw_attachments:
        saved_attachments = await _save_email_attachments(
            raw_attachments, agent_id, app, email_id=email_id,
        )
    if saved_attachments:
        normalized["attachments"] = saved_attachments
    normalized.setdefault("metadata", {})["_raw_attachment_count"] = len(raw_attachments)

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


def _resend_api_key(app: Any) -> str:
    import os

    key = os.environ.get("RESEND_API_KEY", "").strip()
    if key:
        return key
    cfg = getattr(app.state, "config", None) or {}
    return str(cfg.get("resend_api_key") or cfg.get("RESEND_API_KEY") or "").strip()


async def _fetch_resend_attachment_url(
    email_id: str,
    attachment_id: str,
    api_key: str,
) -> dict[str, Any] | None:
    if not email_id or not attachment_id or not api_key:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"https://api.resend.com/emails/receiving/{email_id}/attachments/{attachment_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning(
            "Resend attachment metadata fetch failed (%s/%s)",
            email_id, attachment_id, exc_info=True,
        )
        return None


async def _save_email_attachments(
    raw_attachments: list[dict[str, Any]],
    agent_id: str,
    app: Any,
    *,
    email_id: str = "",
) -> list[dict[str, Any]]:
    """Decode, download, or fetch email attachments into workspace/uploads."""
    from nls.skills.channel_attachments import download_url_to_uploads, save_bytes_to_uploads

    api_key = _resend_api_key(app)
    saved: list[dict[str, Any]] = []

    for att in raw_attachments:
        filename = att.get("filename") or att.get("name") or f"attachment_{int(time.time())}"
        mime = att.get("content_type") or att.get("mime_type") or "application/octet-stream"
        record: dict[str, Any] | None = None

        content_b64 = att.get("content", "")
        if content_b64:
            try:
                raw = base64.b64decode(content_b64)
                record = save_bytes_to_uploads(
                    agent_id, filename=filename, data=raw, mime_type=mime,
                )
            except Exception:
                logger.warning("Email attachment base64 decode failed: %s", filename, exc_info=True)

        download_url = att.get("download_url") or att.get("url")
        if record is None and download_url:
            record = await download_url_to_uploads(
                agent_id, download_url, filename=filename, mime_type=mime,
            )

        attachment_id = att.get("id")
        if record is None and email_id and attachment_id and api_key:
            meta = await _fetch_resend_attachment_url(email_id, str(attachment_id), api_key)
            signed_url = (meta or {}).get("download_url")
            if signed_url:
                record = await download_url_to_uploads(
                    agent_id,
                    signed_url,
                    filename=(meta or {}).get("filename") or filename,
                    mime_type=(meta or {}).get("content_type") or mime,
                )

        if record:
            saved.append(record)

    if raw_attachments and len(saved) < len(raw_attachments):
        logger.warning(
            "Email [%s]: saved %d/%d attachment(s)",
            agent_id, len(saved), len(raw_attachments),
        )
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
    attachments = normalized.get("attachments") or []
    agent_id = getattr(runtime, "agent_id", "")

    from nls.skills.channel_adapter_util import channel_history_content

    display_text = channel_history_content(text, attachments)
    history = runtime.load_session_history(session_key)

    from nls.skills.channel_attachments import note_attachment_download_gaps

    raw_count = int(normalized.get("metadata", {}).get("_raw_attachment_count") or 0)
    attachment_labels = [a.get("name", "file") for a in attachments]

    from nls.skills.surface_send import channel_session_metadata
    session_meta = channel_session_metadata(normalized)

    runtime.save_session_history(
        history + [{"role": "user", "content": display_text}],
        session_key=session_key,
        metadata=session_meta,
    )

    _broadcast_channel_event(app, agent_id, normalized, response="", direction="inbound")

    try:
        from nls.skills.channel_processing import process_channel_message
        user_input = f"[Email from {sender}] Subject: {subject}\n\n{display_text}"
        user_input = note_attachment_download_gaps(
            user_input,
            expected=raw_count,
            saved=len(attachments),
            labels=attachment_labels,
        )
        response_text = await process_channel_message(
            app, runtime, agent_id, user_input, history,
            channel_adapter=adapter,
            reply_target=sender,
            session_key=session_key,
            attachments=attachments,
            raw_content=display_text,
        )
        if response_text:
            history.append({"role": "user", "content": display_text})
            history.append({"role": "assistant", "content": response_text})

        if response_text:
            runtime.save_session_history(
                history, session_key=session_key,
                metadata=session_meta,
            )

            clean_response = _strip_signal_tags(response_text)
            from nls.skills.channel_adapter_util import prepare_channel_outbound

            clean_response = prepare_channel_outbound(clean_response)
            if response_text.strip() and not clean_response:
                logger.warning(
                    "Email [%s]: response was tool-call leak — not sending (%r)",
                    agent_id,
                    response_text[:120],
                )
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


def _broadcast_policy_skip(
    app: Any,
    agent_id: str,
    normalized: dict[str, Any],
    *,
    reason: str,
) -> None:
    cm = getattr(app.state, "connection_manager", None)
    if cm is None:
        return
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cm.broadcast(agent_id, {
            "type": "channel_event",
            "channel": "email",
            "direction": "skipped",
            "skip_reason": reason,
            "sender": normalized.get("sender_name") or normalized.get("sender_id") or "?",
            "subject": normalized.get("metadata", {}).get("subject", ""),
            "content": normalized.get("content", ""),
            "content_preview": (normalized.get("content") or "")[:100],
            "session_key": normalized.get("session_key", ""),
        }))
    except Exception:
        pass
