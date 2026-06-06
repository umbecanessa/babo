"""Download inbound channel media into agent workspace/uploads."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_INBOUND_ATTACHMENTS = 20

_UNSAFE_NAME = re.compile(r"[^\w.\- ()\[\]]+")
_OUTBOUND_FILE_RE = re.compile(r"\buploads/[\w.\- ()\[\]]+\.\w{1,10}\b")
_ATTACH_MARKER_RE = re.compile(r"\[\[attach:([^\]]+)\]\]", re.IGNORECASE)


def _safe_filename(name: str, fallback_prefix: str = "attachment") -> str:
    cleaned = _UNSAFE_NAME.sub("_", (name or "").strip()) or f"{fallback_prefix}_{int(time.time())}"
    return cleaned[:200]


def agent_uploads_dir(agent_id: str) -> Path | None:
    try:
        from server.main import app

        am = getattr(app.state, "agent_manager", None)
        if am is None:
            return None
        uploads = am.agents_dir / agent_id / "workspace" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        return uploads
    except Exception:
        return None


def save_bytes_to_uploads(
    agent_id: str,
    *,
    filename: str,
    data: bytes,
    mime_type: str = "application/octet-stream",
    is_voice: bool = False,
) -> dict[str, Any] | None:
    uploads = agent_uploads_dir(agent_id)
    if uploads is None or not data:
        return None
    safe = _safe_filename(filename)
    dest = uploads / safe
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        dest = uploads / f"{stem}_{int(time.time())}{suffix}"
    try:
        dest.write_bytes(data)
    except Exception:
        logger.warning("Failed to save attachment %s for agent %s", safe, agent_id, exc_info=True)
        return None
    rel = f"uploads/{dest.name}"
    return {
        "name": dest.name,
        "path": rel,
        "mime_type": mime_type,
        "size": len(data),
        "is_voice": is_voice,
    }


async def download_url_to_uploads(
    agent_id: str,
    url: str,
    *,
    filename: str,
    mime_type: str = "application/octet-stream",
    headers: dict[str, str] | None = None,
    is_voice: bool = False,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    if not url or not agent_id:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers or {})
            resp.raise_for_status()
            raw = resp.content
            if not mime_type or mime_type == "application/octet-stream":
                mime_type = resp.headers.get("content-type", mime_type).split(";")[0].strip()
    except Exception:
        logger.warning("Channel attachment download failed: %s", url, exc_info=True)
        return None
    return save_bytes_to_uploads(
        agent_id,
        filename=filename,
        data=raw,
        mime_type=mime_type,
        is_voice=is_voice,
    )


def note_attachment_download_gaps(
    user_input: str,
    *,
    expected: int,
    saved: int,
    labels: list[str] | None = None,
) -> str:
    """Warn the agent when some inbound attachments could not be saved."""
    if expected <= 0 or saved >= expected:
        return user_input
    failed = expected - saved
    hint = ""
    if labels:
        hint = f" ({', '.join(labels[:5])})"
    note = (
        f"[{failed} of {expected} attachment(s) could not be downloaded{hint}. "
        "Ask the user to re-send or describe the file if needed.]"
    )
    if user_input.strip():
        return f"{note}\n\n{user_input}"
    return note


def detect_outbound_workspace_files(
    text: str,
    agent_id: str,
    *,
    limit: int = 3,
) -> list[str]:
    """Find workspace upload paths referenced in an agent response."""
    from nls.skills.channel_adapter_util import resolve_workspace_file

    candidates: list[str] = []
    for match in _ATTACH_MARKER_RE.finditer(text or ""):
        candidates.append(match.group(1).strip())
    for match in _OUTBOUND_FILE_RE.finditer(text or ""):
        candidates.append(match.group(0).strip())

    seen: set[str] = set()
    resolved: list[str] = []
    for raw in candidates:
        path = raw.replace("\\", "/").lstrip("./")
        if not path.startswith("uploads/"):
            continue
        if path in seen:
            continue
        seen.add(path)
        if resolve_workspace_file(agent_id, path) is not None:
            resolved.append(path)
        if len(resolved) >= limit:
            break
    return resolved


async def deliver_channel_reply(
    adapter: Any,
    target: str,
    clean_text: str,
    raw_response: str,
    *,
    agent_id: str,
    send_kwargs: dict[str, Any] | None = None,
) -> None:
    """Send a channel auto-reply, attaching workspace files when referenced."""
    send_kwargs = dict(send_kwargs or {})
    files = detect_outbound_workspace_files(raw_response, agent_id)

    async def _send_files() -> None:
        for fp in files:
            try:
                if hasattr(adapter, "send_file"):
                    result = await adapter.send_file(
                        target,
                        fp,
                        caption="",
                        agent_id=agent_id,
                        reply_to=send_kwargs.get("reply_to"),
                    )
                    if getattr(result, "is_error", False):
                        logger.warning("Channel outbound file failed: %s", fp)
                elif hasattr(adapter, "upload_file"):
                    result = await adapter.upload_file(
                        target,
                        fp,
                        initial_comment="",
                        agent_id=agent_id,
                    )
                    if getattr(result, "is_error", False):
                        logger.warning("Channel outbound file failed: %s", fp)
            except Exception:
                logger.warning("Channel outbound file failed: %s", fp, exc_info=True)

    if files:
        await _send_files()
    if clean_text:
        await adapter.send(target, clean_text, agent_id=agent_id, **send_kwargs)
    elif not files:
        return


def discord_inbound_media_count(message: dict[str, Any]) -> int:
    stickers = [
        s for s in (message.get("stickers") or [])
        if s.get("format_type") != 3
    ]
    return len(message.get("attachments") or []) + len(stickers)


def slack_inbound_media_count(event: dict[str, Any]) -> int:
    return len(event.get("files") or [])


def telegram_inbound_media_count(message: dict[str, Any]) -> int:
    media_keys = ("document", "photo", "voice", "audio", "video", "video_note", "sticker")
    return 1 if any(key in message for key in media_keys) else 0


def whatsapp_inbound_media_count(body: dict[str, Any]) -> int:
    msg = body.get("message") or body
    return 1 if msg.get("media") else 0
