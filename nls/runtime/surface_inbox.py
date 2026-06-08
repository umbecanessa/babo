"""Cross-surface inbox — agent awareness of conversations on other channels.

When the lead is active on Home (``websocket:main``) and a Discord/Telegram/etc.
message arrives, we record it here instead of spawning a parallel chat turn.
Pending items are injected into the foreground agentic loop as steering messages
(``[SURFACE INBOX — …]``), mirroring the owner UI inbox.

Persisted per agent at ``data/agents/{id}/surface_inbox.json``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ITEMS_PER_AGENT = 32
_PREVIEW_CHARS = 160
_INBOX_FILENAME = "surface_inbox.json"

# agent_id → pending cross-surface items (mirrored to disk)
_inboxes: dict[str, list[SurfaceInboxItem]] = {}
_loaded_agents: set[str] = set()


@dataclass
class SurfaceInboxItem:
    session_key: str
    channel: str
    channel_label: str
    sender_name: str
    content: str
    preview: str
    received_at: float = field(default_factory=time.time)
    steered_at: float = 0.0
    handled: bool = False
    # Legacy field — migrated on load; do not write on new items.
    delivered_to_loop: bool = False


# Re-steer cross-surface items that were shown but not handled on target surface.
_RESURFACE_AFTER_SECONDS = 120.0


def _agents_dir() -> Path | None:
    try:
        from server.main import app

        am = getattr(app.state, "agent_manager", None)
        if am is not None:
            return Path(am.agents_dir)
    except Exception:
        pass
    return None


def _inbox_path(agent_id: str) -> Path | None:
    base = _agents_dir()
    if base is None:
        return None
    return base / agent_id / _INBOX_FILENAME


def load_agent_inbox(agent_id: str) -> None:
    """Load persisted inbox for an agent (idempotent)."""
    if not agent_id or agent_id in _loaded_agents:
        return
    _loaded_agents.add(agent_id)
    path = _inbox_path(agent_id)
    if path is None or not path.is_file():
        _inboxes.setdefault(agent_id, [])
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return
        items: list[SurfaceInboxItem] = []
        for row in raw[-_MAX_ITEMS_PER_AGENT:]:
            if isinstance(row, dict):
                fields = {
                    k: v for k, v in row.items()
                    if k in SurfaceInboxItem.__dataclass_fields__
                }
                # Legacy: delivered_to_loop meant "steered once" — not handled.
                if fields.get("handled") is not True and fields.get("delivered_to_loop"):
                    fields.setdefault(
                        "steered_at",
                        float(fields.get("received_at") or 0) or time.time(),
                    )
                    fields["handled"] = False
                    fields["delivered_to_loop"] = False
                items.append(SurfaceInboxItem(**fields))
        _inboxes[agent_id] = items
        logger.debug("Surface inbox [%s]: loaded %d item(s) from disk", agent_id, len(items))
    except Exception as exc:
        logger.warning("Surface inbox [%s]: load failed: %s", agent_id, exc)
        _inboxes.setdefault(agent_id, [])


def _persist_inbox(agent_id: str) -> None:
    path = _inbox_path(agent_id)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        items = _inboxes.get(agent_id, [])
        path.write_text(
            json.dumps([asdict(i) for i in items], indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Surface inbox [%s]: persist failed: %s", agent_id, exc)


def _trim_inbox(agent_id: str) -> None:
    items = _inboxes.get(agent_id)
    if not items:
        return
    if len(items) > _MAX_ITEMS_PER_AGENT:
        _inboxes[agent_id] = items[-_MAX_ITEMS_PER_AGENT:]


def record_surface_inbound(
    agent_id: str,
    *,
    session_key: str,
    channel: str,
    channel_label: str = "",
    sender_name: str,
    content: str,
    runtime: Any | None = None,
) -> SurfaceInboxItem:
    """Append an inbound message to the agent's surface inbox."""
    load_agent_inbox(agent_id)

    preview = (content or "").strip()
    if len(preview) > _PREVIEW_CHARS:
        preview = preview[:_PREVIEW_CHARS].rstrip() + "…"

    item = SurfaceInboxItem(
        session_key=session_key,
        channel=channel,
        channel_label=channel_label or channel,
        sender_name=sender_name or "?",
        content=(content or "").strip(),
        preview=preview,
    )
    _inboxes.setdefault(agent_id, []).append(item)
    _trim_inbox(agent_id)
    _persist_inbox(agent_id)

    if runtime is not None:
        _sync_channels_ring_preview(
            runtime,
            channel=channel,
            channel_label=item.channel_label,
            sender_name=item.sender_name,
            preview=preview,
        )
        _sync_surface_inbox_ring(runtime, agent_id)

    logger.info(
        "Surface inbox [%s]: recorded %s from %s on %s (session=%s)",
        agent_id,
        preview[:60],
        sender_name,
        channel,
        session_key,
    )
    return item


def pending_count(agent_id: str) -> int:
    load_agent_inbox(agent_id)
    items = _inboxes.get(agent_id, [])
    return sum(1 for i in items if not i.handled)


def resolve_foreground_session_key(runtime: Any) -> str:
    """Best-effort active conversation thread for this runtime."""
    sk = getattr(runtime, "_foreground_session_key", "") or ""
    if sk:
        return sk
    src = getattr(runtime, "_foreground_source", "") or ""
    if src.startswith("user:channel"):
        return sk
    if src in ("user", "user:ws", "ws", ""):
        get_home = getattr(runtime, "get_default_home_session_key", None)
        if callable(get_home):
            return get_home()
        return "websocket:main"
    return sk or "websocket:main"


def _runtime_is_busy(runtime: Any) -> bool:
    """``AgentRuntime.is_busy`` is a bool property; tests may use a callable."""
    busy = getattr(runtime, "is_busy", False)
    if callable(busy):
        return bool(busy())
    return bool(busy)


def should_defer_cross_surface(runtime: Any, session_key: str) -> bool:
    """True when another surface is mid-turn — queue background channel processing."""
    if not session_key:
        return False
    if not _runtime_is_busy(runtime):
        return False
    fg = resolve_foreground_session_key(runtime)
    if not fg or fg == session_key:
        return False
    return True


def try_feed_active_copilot(runtime: Any, text: str) -> bool:
    """Route cross-surface text into whichever copilot queue is live."""
    if not text.strip():
        return False

    queues: list[Any] = []

    fg_q = getattr(runtime, "_foreground_copilot_queue", None)
    if fg_q is not None:
        queues.append(fg_q)

    _tm = getattr(runtime, "_team_manager", None)
    tm_q = getattr(_tm, "_copilot_queue", None) if _tm is not None else None
    if tm_q is not None and tm_q not in queues:
        queues.append(tm_q)

    agent_id = getattr(runtime, "agent_id", "") or ""
    if agent_id:
        try:
            from nls.skills.channel_processing import _autonomous_copilot_queues

            auto_q = _autonomous_copilot_queues.get(agent_id)
            if auto_q is not None and auto_q not in queues:
                queues.append(auto_q)
        except Exception:
            pass

    for q in queues:
        try:
            q.put_nowait(text.strip())
            logger.info(
                "Surface inbox: fed copilot_queue for agent %s (len=%d)",
                agent_id or "?",
                len(text),
            )
            return True
        except Exception:
            continue
    return False


def mark_session_inbox_handled(agent_id: str, session_key: str) -> None:
    """Mark cross-surface inbox items for *session_key* as handled."""
    if not session_key:
        return
    load_agent_inbox(agent_id)
    changed = False
    for item in _inboxes.get(agent_id, []):
        if item.session_key == session_key and not item.handled:
            item.handled = True
            changed = True
    if changed:
        _persist_inbox(agent_id)
        try:
            from server.main import app

            am = getattr(app.state, "agent_manager", None)
            rt = am.get_runtime(agent_id) if am else None
            if rt is not None:
                _sync_surface_inbox_ring(rt, agent_id)
        except Exception:
            pass


def drain_surface_inbox_steering(
    agent_id: str,
    active_session_key: str,
) -> list[dict[str, str]]:
    """Non-delivered cross-surface items → user-role steering for the active loop."""
    load_agent_inbox(agent_id)
    items = _inboxes.get(agent_id, [])
    if not items:
        return []

    msgs: list[dict[str, str]] = []
    changed = False
    now = time.time()
    for item in items:
        if item.handled:
            continue
        if item.session_key == active_session_key:
            continue
        if item.steered_at > 0 and (now - item.steered_at) < _RESURFACE_AFTER_SECONDS:
            continue
        item.steered_at = now
        changed = True
        label = item.channel_label or item.channel
        msgs.append({
            "role": "user",
            "content": (
                f"[SURFACE INBOX — {item.channel} {label}] "
                f"{item.sender_name}: {item.content}"
            ),
        })

    if changed:
        _persist_inbox(agent_id)

    if msgs:
        logger.info(
            "Surface inbox [%s]: draining %d item(s) into active loop (session=%s)",
            agent_id,
            len(msgs),
            active_session_key,
        )
        try:
            from server.main import app

            am = getattr(app.state, "agent_manager", None)
            rt = am.get_runtime(agent_id) if am else None
            if rt is not None:
                _sync_surface_inbox_ring(rt, agent_id)
        except Exception:
            pass
    return msgs


def format_surface_inbox_summary(agent_id: str) -> str:
    """Compact summary for Cryptex Channels ring."""
    load_agent_inbox(agent_id)
    pending = [i for i in _inboxes.get(agent_id, []) if not i.handled]
    if not pending:
        return ""
    lines = [f"Surface inbox: {len(pending)} unhandled on other channel(s):"]
    for item in pending[-5:]:
        label = item.channel_label or item.channel
        tag = " (steered, awaiting reply)" if item.steered_at > 0 else ""
        lines.append(
            f"  • {item.channel} {label} — {item.sender_name}: {item.preview}{tag}"
        )
    return "\n".join(lines)


def _sync_channels_ring_preview(
    runtime: Any,
    *,
    channel: str,
    channel_label: str,
    sender_name: str,
    preview: str,
) -> None:
    try:
        from nls.brain.cryptex import CryptexMemory, RING_CHANNELS
    except ImportError:
        return
    wm = getattr(runtime, "working_memory", None)
    if not isinstance(wm, CryptexMemory):
        return
    ring = wm.get_ring(RING_CHANNELS)
    if ring is None:
        return
    ts = time.strftime("%H:%M")
    ring.upsert_slot(
        domain=f"channel.{channel}",
        content=(
            f"{channel_label or channel}: last inbound {ts} from {sender_name} — "
            f"{preview or '(empty)'}"
        ),
        slot_type="fact",
        salience=0.72,
        source="surface_inbox",
        position=channel_label or channel,
    )


def clear_surface_inbox_ring(runtime: Any, agent_id: str) -> None:
    """Refresh surface.inbox slot when nothing is pending."""
    if pending_count(agent_id):
        return
    try:
        from nls.brain.cryptex import CryptexMemory, RING_CHANNELS
    except ImportError:
        return
    wm = getattr(runtime, "working_memory", None)
    if not isinstance(wm, CryptexMemory):
        return
    ring = wm.get_ring(RING_CHANNELS)
    if ring is None:
        return
    ring.upsert_slot(
        domain="surface.inbox",
        content="Surface inbox: no pending cross-surface messages.",
        slot_type="fact",
        salience=0.35,
        source="surface_inbox",
        position="inbox",
    )


def _sync_surface_inbox_ring(runtime: Any, agent_id: str) -> None:
    summary = format_surface_inbox_summary(agent_id)
    if not summary:
        clear_surface_inbox_ring(runtime, agent_id)
        return
    try:
        from nls.brain.cryptex import CryptexMemory, RING_CHANNELS
    except ImportError:
        return
    wm = getattr(runtime, "working_memory", None)
    if not isinstance(wm, CryptexMemory):
        return
    ring = wm.get_ring(RING_CHANNELS)
    if ring is None:
        return
    ring.upsert_slot(
        domain="surface.inbox",
        content=summary,
        slot_type="fact",
        salience=0.85,
        source="surface_inbox",
        position="inbox",
    )


def clear_agent_inbox(agent_id: str) -> None:
    """Test helper — drop all pending items."""
    _inboxes.pop(agent_id, None)
    _loaded_agents.discard(agent_id)
    path = _inbox_path(agent_id)
    if path is not None and path.is_file():
        try:
            path.unlink()
        except Exception:
            pass
