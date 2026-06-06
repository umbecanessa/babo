"""Append-only ambient log for shared channel / group conversations.

Records inbound group traffic even when mention policy blocks a reply, so the
agent can reconstruct context via ``channel_history`` without stuffing every
message into the active session window.

One JSONL file per agent: ``{agent_dir}/channel_ambient.jsonl``.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_AMBIENT_FILENAME = "channel_ambient.jsonl"
_MAX_TOTAL_LINES = 10_000
_TRIM_TO_LINES = 8_000
_PREVIEW_CHARS = 600


def is_shared_channel_session(session_key: str | None) -> bool:
    """True for group/supergroup/channel sessions (not DM or Home).

    Email threads (``email:thread:…``) are intentionally excluded: each accepted
    inbound is appended to the session transcript, which already carries the full
    reply chain. Ambient logging is for mention-gated *group chat* traffic only.
    """
    if not session_key or session_key in ("websocket:main",):
        return False
    parts = session_key.split(":")
    if len(parts) < 3:
        return False
    return parts[1] in ("group", "channel")


def _message_id_from_normalized(normalized: dict[str, Any]) -> str:
    meta = normalized.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    return str(
        normalized.get("message_id")
        or meta.get("message_id")
        or meta.get("ts")
        or meta.get("thread_ts")
        or ""
    )


def _ambient_path(agent_dir: Path) -> Path:
    return agent_dir / _AMBIENT_FILENAME


def _ts_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError as exc:
        logger.warning("channel_ambient: read failed: %s", exc)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("channel_ambient: write failed: %s", exc)


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    row = dict(entry)
    row.setdefault("ts", _ts_iso())
    row.setdefault("timestamp", time.time())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("channel_ambient: append failed: %s", exc)


def _compact_if_needed(path: Path) -> None:
    rows = _read_jsonl(path)
    if len(rows) <= _MAX_TOTAL_LINES:
        return
    logger.info(
        "channel_ambient: compacting %d -> %d lines",
        len(rows), _TRIM_TO_LINES,
    )
    _write_jsonl(path, rows[-_TRIM_TO_LINES:])


def _dedupe_message_id(
    agent_dir: Path,
    session_key: str,
    message_id: str,
) -> bool:
    """True if *message_id* was already logged for *session_key*."""
    if not message_id:
        return False
    path = _ambient_path(agent_dir)
    if not path.exists():
        return False
    try:
        tail: list[str] = []
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if raw.strip():
                    tail.append(raw.strip())
                    if len(tail) > 200:
                        tail.pop(0)
        for raw in reversed(tail):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if (
                row.get("session_key") == session_key
                and str(row.get("message_id") or "") == message_id
            ):
                return True
    except OSError:
        return False
    return False


def append_channel_ambient(
    agent_dir: Path,
    normalized: dict[str, Any],
    *,
    triggered: bool = False,
    role: str = "user",
    content: str | None = None,
) -> None:
    """Append one group/channel ambient line from a normalized inbound payload."""
    if not normalized.get("is_group"):
        return

    text = (content if content is not None else normalized.get("content") or "").strip()
    if not text:
        text = "[media]"
    if text == "[media]" and role == "user" and not normalized.get("attachments"):
        return

    session_key = normalized.get("session_key") or ""
    if not session_key:
        return

    message_id = _message_id_from_normalized(normalized)
    if role == "user" and _dedupe_message_id(agent_dir, session_key, message_id):
        return

    path = _ambient_path(agent_dir)
    entry: dict[str, Any] = {
        "role": role,
        "channel": normalized.get("channel") or session_key.split(":")[0],
        "session_key": session_key,
        "sender_id": normalized.get("sender_id") or "",
        "sender_name": normalized.get("sender_name") or "",
        "content": text,
        "triggered": bool(triggered),
        "is_mention": bool(normalized.get("is_mention")),
        "group_id": normalized.get("group_id"),
    }
    if message_id:
        entry["message_id"] = message_id
    meta = normalized.get("metadata") or {}
    if isinstance(meta, dict):
        for key in ("channel_name", "chat_type", "thread_ts"):
            if meta.get(key):
                entry[key] = meta[key]

    _append_jsonl(path, entry)
    _compact_if_needed(path)


def append_channel_ambient_reply(
    agent_dir: Path,
    normalized: dict[str, Any],
    content: str,
) -> None:
    """Log the bot's outbound reply in the ambient group transcript."""
    if not normalized.get("is_group"):
        return
    text = (content or "").strip()
    if not text:
        return
    meta = normalized.get("metadata") or {}
    bot_name = ""
    if isinstance(meta, dict):
        bot_name = str(meta.get("bot_username") or meta.get("bot_name") or "")
    if not bot_name:
        bot_name = f"{normalized.get('channel') or 'bot'} bot"
    reply_norm = {
        **normalized,
        "sender_name": bot_name.lstrip("@"),
        "sender_id": "bot",
        "content": text,
    }
    append_channel_ambient(
        agent_dir, reply_norm, triggered=True, role="assistant", content=text,
    )


def query_channel_ambient(
    agent_dir: Path,
    *,
    query: str = "",
    session_key: str = "",
    channel: str = "",
    role: str = "",
    limit: int | None = 20,
    offset: int = 0,
    line_start: int | None = None,
    line_end: int | None = None,
    newest_first: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Search the ambient channel log. Returns ``(matches, total_lines)``."""
    rows = _read_jsonl(_ambient_path(agent_dir))
    total = len(rows)
    indexed: list[dict[str, Any]] = []
    for i, msg in enumerate(rows, start=1):
        row = dict(msg)
        row["line"] = i
        indexed.append(row)

    sk = (session_key or "").strip()
    if sk:
        indexed = [m for m in indexed if m.get("session_key") == sk]

    ch = (channel or "").strip().lower()
    if ch:
        indexed = [m for m in indexed if str(m.get("channel") or "").lower() == ch]

    if line_start is not None or line_end is not None:
        ls = max(1, line_start or 1)
        le = min(total, line_end or total)
        indexed = [m for m in indexed if ls <= m["line"] <= le]

    if role in ("user", "assistant"):
        indexed = [m for m in indexed if m.get("role") == role]

    q = (query or "").strip().lower()
    if q:
        def _matches(row: dict[str, Any]) -> bool:
            blob = " ".join(
                str(row.get(k) or "")
                for k in ("content", "sender_name", "session_key", "channel")
            ).lower()
            return q in blob

        indexed = [m for m in indexed if _matches(m)]

    if q or (role and newest_first):
        if newest_first:
            indexed = list(reversed(indexed))

    if not q and not role and line_start is None and not newest_first:
        end = len(indexed) - offset if offset else len(indexed)
        start = max(0, end - (limit or len(indexed)))
        indexed = indexed[start:end]
    elif offset > 0:
        indexed = indexed[offset:]

    if limit is not None and limit > 0:
        indexed = indexed[:limit]
    return indexed, total


def channel_ambient_stats(agent_dir: Path) -> dict[str, Any]:
    rows = _read_jsonl(_ambient_path(agent_dir))
    path = _ambient_path(agent_dir)
    sessions: dict[str, int] = {}
    channels: dict[str, int] = {}
    for row in rows:
        sk = str(row.get("session_key") or "")
        sessions[sk] = sessions.get(sk, 0) + 1
        ch = str(row.get("channel") or "")
        channels[ch] = channels.get(ch, 0) + 1
    return {
        "total": len(rows),
        "sessions": len(sessions),
        "channels": channels,
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "first_ts": rows[0].get("ts") if rows else None,
        "last_ts": rows[-1].get("ts") if rows else None,
    }


def recent_ambient_snippet(
    agent_dir: Path,
    session_key: str,
    *,
    limit: int = 6,
    exclude_last: int = 0,
) -> str:
    """Compact recent group lines for injection on triggered replies."""
    if not session_key:
        return ""
    rows, _ = query_channel_ambient(
        agent_dir,
        session_key=session_key,
        limit=limit + exclude_last,
        newest_first=False,
    )
    if exclude_last and len(rows) > exclude_last:
        rows = rows[:-exclude_last]
    elif exclude_last:
        rows = []
    if not rows:
        return ""
    lines = [
        f"  [{r.get('sender_name') or '?'}] {(r.get('content') or '')[:180]}"
        for r in rows
    ]
    return (
        "[Recent group activity — full log: channel_history tool]\n"
        + "\n".join(lines)
        + "\n\n"
    )


def ambient_timeline_for_session(
    agent_dir: Path,
    session_key: str,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Chronological ambient rows for UI thread restore (group/channel sessions only)."""
    if not is_shared_channel_session(session_key):
        return []
    rows, _ = query_channel_ambient(
        agent_dir,
        session_key=session_key,
        limit=limit,
        newest_first=False,
    )
    timeline: list[dict[str, Any]] = []
    for row in rows:
        timeline.append({
            "role": row.get("role"),
            "content": row.get("content") or "",
            "sender": row.get("sender_name") or row.get("sender_id") or "",
            "triggered": bool(row.get("triggered")),
            "is_mention": bool(row.get("is_mention")),
            "timestamp": row.get("ts"),
            "message_id": row.get("message_id"),
        })
    return timeline
