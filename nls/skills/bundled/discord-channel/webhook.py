"""Discord inbound webhook — receives Gateway events relayed from NestJS."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .adapter import enrich_discord_normalized_labels
from nls.skills.channel_adapter_util import broadcast_channel_event, prepare_channel_outbound, strip_signal_tags, channel_history_content

logger = logging.getLogger(__name__)
router = APIRouter(tags=["discord-channel"])


def _get_adapter(app: Any) -> Any | None:
    sl = getattr(app.state, "skill_loader", None)
    if sl is None:
        return None
    sk = sl.skills.get("discord-channel")
    if sk and sk.context:
        return sk.context.adapter
    return None


@router.post("/webhook/{agent_id}")
async def discord_inbound(agent_id: str, request: Request):
    app = request.app
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    adapter = _get_adapter(app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Discord skill not loaded")

    agent_manager = getattr(app.state, "agent_manager", None)
    if agent_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager not ready")
    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    event_type = body.get("t") or body.get("type")
    if event_type in ("CHANNEL_CREATE", "CHANNEL_UPDATE", "GUILD_CREATE"):
        await adapter.sync_channels_from_platform(agent_id, auto_enable=True)
        return {"ok": True, "status": "synced"}

    message = body.get("d") if body.get("t") == "MESSAGE_CREATE" else body
    if not isinstance(message, dict):
        return {"ok": True, "status": "no_message"}

    normalized = adapter.normalize(body if body.get("t") else message, agent_id=agent_id)
    if normalized is None:
        return {"ok": True, "status": "skip"}

    cfg = adapter._agent_cfg(agent_id)
    enrich_discord_normalized_labels(cfg, normalized)

    raw_for_policy = message if message.get("author") else (body.get("d") or {})
    skip_reason = adapter.explain_policy_block(raw_for_policy, agent_id=agent_id)

    from nls.skills.channel_ambient import record_inbound_ambient, record_outbound_ambient

    record_inbound_ambient(runtime, normalized, triggered=not skip_reason)

    if skip_reason:
        from .adapter import broadcast_channel_policy_skip

        logger.info("Discord webhook [%s]: policy rejected — %s", agent_id, skip_reason)
        if normalized.get("is_group"):
            from nls.skills.channel_adapter_util import broadcast_group_ambient_inbound
            broadcast_group_ambient_inbound(app, agent_id, "discord", normalized)
        else:
            broadcast_channel_policy_skip(app, agent_id, normalized, reason=skip_reason)
        return {"ok": True, "status": "policy_rejected", "reason": skip_reason}

    adapter.register_known_sender(normalized["sender_id"], agent_id)

    raw_message = message if message.get("author") else (body.get("d") or {})
    downloaded = await adapter.download_inbound_attachments(raw_message, agent_id)
    if downloaded:
        normalized["attachments"] = downloaded

    session_key = normalized["session_key"]
    text = normalized.get("content", "")
    attachments = normalized.get("attachments") or []
    channel_id = normalized["metadata"]["channel_id"]
    sender_name = normalized["sender_name"]

    from nls.skills.surface_send import channel_session_metadata

    session_meta = channel_session_metadata(normalized)
    history = runtime.load_session_history(session_key)
    runtime.save_session_history(
        history + [{"role": "user", "content": channel_history_content(text, attachments)}],
        session_key=session_key,
        metadata=session_meta,
    )
    broadcast_channel_event(app, agent_id, "discord", normalized, direction="inbound")

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
            discord_inbound_media_count,
            note_attachment_download_gaps,
        )

        user_input = (
            f"[{sender_name} via Discord]: {text}" if text
            else f"[{sender_name} via Discord]:"
        )
        user_input = note_attachment_download_gaps(
            user_input,
            expected=discord_inbound_media_count(raw_message),
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
            channel_label=normalized["metadata"].get("channel_name", ""),
            raw_content=text or ("[media]" if attachments else ""),
        )
        clean = prepare_channel_outbound(response_text or "")
        if response_text and response_text.strip() and not clean:
            logger.warning(
                "Discord webhook [%s]: blocked tool-call leak outbound (%r)",
                agent_id,
                (response_text or "")[:120],
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
            )
            record_outbound_ambient(runtime, normalized, clean)
            broadcast_channel_event(
                app, agent_id, "discord", normalized, clean, direction="response",
            )
        return {"ok": True, "response_length": len(response_text or "")}
    except Exception as exc:
        logger.error("Discord webhook failed [%s]: %s", agent_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/channels/{agent_id}")
async def discord_list_channels(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Discord skill not loaded")
    await adapter.sync_channels_from_platform(agent_id)
    return adapter.get_status(agent_id=agent_id)


@router.post("/channels/{agent_id}/sync")
async def discord_sync_channels(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Discord skill not loaded")
    await adapter.sync_channels_from_platform(agent_id)
    return {"ok": True, **adapter.get_status(agent_id=agent_id)}


@router.patch("/channels/{agent_id}/{channel_id}")
async def discord_update_channel(
    agent_id: str,
    channel_id: str,
    request: Request,
):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Discord skill not loaded")
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
    return {"ok": True, "scoped_channels": updated.get("scoped_channels", {}), "permission_warning": updated.get("_permission_warning", "")}


@router.put("/channels/{agent_id}/desired")
async def discord_set_channels_desired(agent_id: str, request: Request):
    """Bulk save channel scope from the Tools UI."""
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Discord skill not loaded")
    try:
        body = await request.json()
    except Exception:
        body = {}
    selections = body.get("channels") or []
    if not isinstance(selections, list):
        raise HTTPException(status_code=400, detail="channels must be a list")
    updated = await adapter.apply_channels_bulk(agent_id, selections)
    return {"ok": True, **adapter.get_status(agent_id=agent_id)}


@router.get("/roles/{agent_id}")
async def discord_list_roles(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Discord skill not loaded")
    guilds = await adapter.fetch_guild_roles(agent_id)
    cfg = adapter._agent_cfg(agent_id)
    return {
        "guilds": guilds,
        "moderator_role_ids": list(cfg.get("moderator_role_ids") or []),
    }


@router.patch("/roles/{agent_id}")
async def discord_update_roles(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Discord skill not loaded")
    try:
        body = await request.json()
    except Exception:
        body = {}
    role_ids = body.get("moderator_role_ids") or []
    if not isinstance(role_ids, list):
        raise HTTPException(status_code=400, detail="moderator_role_ids must be a list")
    adapter.update_config(
        {"moderator_role_ids": [str(r) for r in role_ids if r]},
        agent_id=agent_id,
    )
    return {"ok": True, "moderator_role_ids": role_ids}


@router.get("/status/{agent_id}")
async def discord_status(agent_id: str, request: Request):
    adapter = _get_adapter(request.app)
    if adapter is not None:
        return adapter.get_status(agent_id=agent_id)
    return {"channel": "discord", "connected": False, "enabled": False}
