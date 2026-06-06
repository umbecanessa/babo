"""Slack inbound webhook — Events API payloads relayed from NestJS."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from nls.skills.channel_adapter_util import broadcast_channel_event, channel_history_content, strip_signal_tags

logger = logging.getLogger(__name__)
router = APIRouter(tags=["slack-channel"])


def _get_adapter(app: Any) -> Any | None:
    sl = getattr(app.state, "skill_loader", None)
    if sl is None:
        return None
    sk = sl.skills.get("slack-channel")
    if sk and sk.context:
        return sk.context.adapter
    return None


@router.post("/webhook/{agent_id}")
async def slack_inbound(agent_id: str, request: Request):
    app = request.app
    import json
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8") if raw else "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    adapter = _get_adapter(app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Slack skill not loaded")

    cfg = adapter._agent_cfg(agent_id)
    signing_secret = cfg.get("signing_secret", "")
    sig = request.headers.get("x-slack-signature", "")
    ts = request.headers.get("x-slack-request-timestamp", "")
    # Relay path (NestJS → sidecar) does not forward Slack headers; only verify
    # when the request came directly from Slack with signature headers present.
    if signing_secret and sig and ts and not adapter.verify_signature(signing_secret, ts, raw, sig):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    event = body.get("event") or {}
    if event.get("type") in ("member_joined_channel", "member_left_channel"):
        await adapter.handle_member_event(agent_id, event)
        return {"ok": True, "status": "synced"}

    normalized = adapter.normalize(body, agent_id=agent_id)
    if normalized is None:
        return {"ok": True, "status": "skip"}

    adapter.enrich_normalized_labels(agent_id, normalized)

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    from nls.skills.channel_ambient import record_inbound_ambient, record_outbound_ambient

    will_respond = adapter.should_respond(event, agent_id=agent_id)
    record_inbound_ambient(runtime, normalized, triggered=will_respond)

    if not will_respond:
        if normalized.get("is_group"):
            from nls.skills.channel_adapter_util import broadcast_group_ambient_inbound
            broadcast_group_ambient_inbound(app, agent_id, "slack", normalized)
        return {"ok": True, "status": "policy_rejected"}

    adapter.register_known_sender(normalized["sender_id"], agent_id)

    downloaded = await adapter.download_inbound_attachments(event, agent_id)
    if downloaded:
        normalized["attachments"] = downloaded

    session_key = normalized["session_key"]
    text = normalized.get("content", "")
    attachments = normalized.get("attachments") or []
    channel_id = normalized["metadata"]["channel_id"]
    thread_ts = normalized["metadata"].get("thread_ts") or normalized["metadata"].get("ts")
    sender_name = normalized["sender_name"]

    history = runtime.load_session_history(session_key)
    from nls.skills.surface_send import channel_session_metadata
    session_meta = channel_session_metadata(normalized)
    runtime.save_session_history(
        history + [{"role": "user", "content": channel_history_content(text, attachments)}],
        session_key=session_key,
        metadata=session_meta,
    )
    broadcast_channel_event(app, agent_id, "slack", normalized, direction="inbound")

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
            slack_inbound_media_count,
        )

        user_input = f"[{sender_name} via Slack]: {text}" if text else f"[{sender_name} via Slack]:"
        user_input = note_attachment_download_gaps(
            user_input,
            expected=slack_inbound_media_count(event),
            saved=len(attachments),
            labels=[a.get("name", "file") for a in attachments],
        )
        response_text = await process_channel_message(
            app, runtime, agent_id, user_input, history,
            channel_adapter=adapter,
            reply_target=channel_id,
            session_key=session_key,
            attachments=attachments,
            sender_name=sender_name,
            raw_content=text or ("[media]" if attachments else ""),
        )
        from nls.skills.channel_adapter_util import prepare_channel_outbound

        clean = prepare_channel_outbound(response_text or "")
        if response_text and response_text.strip() and not clean:
            logger.warning(
                "Slack webhook [%s]: blocked tool-call leak outbound (%r)",
                agent_id,
                response_text[:120],
            )
        if clean:
            user_content = channel_history_content(text, attachments)
            history.append({"role": "user", "content": user_content})
            history.append({"role": "assistant", "content": clean})
            runtime.save_session_history(
                history, session_key=session_key,
                metadata=session_meta,
            )
            await deliver_channel_reply(
                adapter, channel_id, clean, response_text or "",
                agent_id=agent_id,
                send_kwargs={"thread_ts": thread_ts or None},
            )
            record_outbound_ambient(runtime, normalized, clean)
            broadcast_channel_event(
                app, agent_id, "slack", normalized, clean, direction="response",
            )
        return {"ok": True, "response_length": len(response_text or "")}
    except Exception as exc:
        logger.error("Slack webhook failed [%s]: %s", agent_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/channels/{agent_id}")
async def slack_list_channels(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Slack skill not loaded")
    await adapter.sync_channels_from_platform(agent_id)
    return adapter.get_status(agent_id=agent_id)


@router.post("/channels/{agent_id}/sync")
async def slack_sync_channels(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Slack skill not loaded")
    await adapter.sync_channels_from_platform(agent_id)
    return {"ok": True, **adapter.get_status(agent_id=agent_id)}


@router.patch("/channels/{agent_id}/{channel_id}")
async def slack_update_channel(agent_id: str, channel_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Slack skill not loaded")
    try:
        body = await request.json()
    except Exception:
        body = {}
    updated = await adapter.apply_channel_desired(
        agent_id,
        channel_id,
        enabled=bool(body.get("enabled", True)),
        require_mention=body.get("require_mention"),
    )
    return {"ok": True, "scoped_channels": updated.get("scoped_channels", {})}


@router.put("/channels/{agent_id}/desired")
async def slack_set_channels_desired(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Slack skill not loaded")
    try:
        body = await request.json()
    except Exception:
        body = {}
    selections = body.get("channels") or []
    if not isinstance(selections, list):
        raise HTTPException(status_code=400, detail="channels must be a list")
    await adapter.apply_channels_bulk(agent_id, selections)
    return {"ok": True, **adapter.get_status(agent_id=agent_id)}


@router.get("/status/{agent_id}")
async def slack_status(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is not None:
        return adapter.get_status(agent_id=agent_id)
    return {"channel": "slack", "connected": False, "enabled": False}
