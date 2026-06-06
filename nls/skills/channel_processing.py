"""Shared message processing for channel webhooks (WhatsApp, Telegram, Email).

Provides a unified pipeline that mirrors the chat WebSocket's intent gate:
1. Check if agentic v2 is available and enabled
2. Classify intent (TASK vs CHAT) using regex fast-path + LLM classifier
3. Route to `process_message_agentic_v2` for tasks or `process_message` for chat
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Module-level registry of pending ask_user queues so that a follow-up
# webhook message can be routed as the answer instead of starting a new loop.
# Key: (agent_id, session_key) → asyncio.Queue
_pending_queues: dict[tuple[str, str], asyncio.Queue] = {}

# Background autonomous dispatches use a separate queue (not tied to a WS session).
_autonomous_copilot_queues: dict[str, asyncio.Queue] = {}

# Cross-surface defer when inner loop is not up yet — flushed on register/wake.
_pending_channel_events: dict[str, list[Any]] = {}


def stash_deferred_channel_event(agent_id: str, event: Any) -> None:
    """Hold CHANNEL_MESSAGE until inner loop is available."""
    _pending_channel_events.setdefault(agent_id, []).append(event)
    logger.info(
        "Channel [%s]: stashed deferred event (pending=%d)",
        agent_id,
        len(_pending_channel_events[agent_id]),
    )


def flush_pending_channel_events(agent_id: str, inner_loop: Any) -> int:
    """Push stashed channel events into the inner loop (FIFO)."""
    pending = _pending_channel_events.pop(agent_id, [])
    for event in pending:
        inner_loop.push_event(event)
    if pending:
        logger.info(
            "Channel [%s]: flushed %d stashed channel event(s) to inner loop",
            agent_id,
            len(pending),
        )
    return len(pending)

_TASK_PATTERNS = re.compile(
    r"\b(search|find|look\s*up|fetch|get|open|go\s+to|navigate|browse|run|execute|"
    r"create|make|build|write|generate|deploy|install|set\s*up|configure|"
    r"fix|debug|solve|update|change|modify|edit|delete|remove|"
    r"download|upload|send|check|verify|test|analyze|scan|schedule|"
    r"book|order|buy|subscribe|cancel|compare|summarize|translate|convert)\b",
    re.IGNORECASE,
)

_CONVERSATIONAL_PATTERNS = re.compile(
    r"^(hi|hello|hey|yo|sup|thanks|thank\s+you|ok|okay|yes|no|yeah|nah|sure|"
    r"good\s*(morning|afternoon|evening|night)|how\s+are\s+you|what'?s\s+up|"
    r"bye|goodbye|see\s+ya|lol|haha|hmm+|wow|cool|nice|great|awesome|"
    r"👋|😊|😂|❤️|🙏|👍)\s*[!?.]*$",
    re.IGNORECASE,
)

_CLASSIFY_PROMPT = (
    "Classify the user's LATEST message into exactly one category.\n\n"
    "TASK_THINK = complex task requiring planning or reasoning "
    "(build an app, debug an error, create a multi-step project, "
    "architect something, write complex code, analyze a problem).\n"
    "TASK_NOTHINK = simple/direct task the agent should just DO "
    "(go to a URL, search for something, open a page, run a command, "
    "fetch info, look something up, book/find/check something, "
    "continue a previous task with provided info).\n"
    "CHAT_THINK = question needing a thoughtful answer "
    "(explain a concept, compare options, give advice, pros/cons).\n"
    "CHAT_NOTHINK = simple chat needing no reasoning "
    "(greeting, thanks, yes/no, how are you, what's your name).\n\n"
    "Reply with exactly one label: TASK_THINK, TASK_NOTHINK, "
    "CHAT_THINK, or CHAT_NOTHINK."
)


def _is_task_message(text: str) -> bool:
    if not text or len(text.strip()) < 5:
        return False
    stripped = text.strip()
    if _CONVERSATIONAL_PATTERNS.match(stripped):
        return False
    return bool(_TASK_PATTERNS.search(stripped))


async def _classify_intent(
    vllm_client: Any,
    message: str,
    history: list[dict] | None = None,
    *,
    adapter_name: str | None = None,
) -> str:
    try:
        msgs: list[dict] = [{"role": "system", "content": _CLASSIFY_PROMPT}]
        if history:
            for turn in history[-6:]:
                role = turn.get("role", "user")
                content = turn.get("content") or ""
                if role in ("user", "assistant") and content:
                    msgs.append({"role": role, "content": content[:300]})
        msgs.append({"role": "user", "content": message})
        result = await vllm_client.generate(
            adapter_name=adapter_name,
            messages=msgs,
            max_tokens=5,
            temperature=0.0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = (result.text if hasattr(result, "text") else str(result or "")).upper().strip()
        for label in ("TASK_THINK", "TASK_NOTHINK", "CHAT_THINK", "CHAT_NOTHINK"):
            if label in raw:
                return label.lower()
        if "TASK" in raw:
            return "task_think"
        return "chat_nothink"
    except Exception:
        logger.exception("Channel intent classifier failed")
        return "chat_nothink"


from nls.runtime.channels import (  # noqa: E402 — re-export for backward compat
    ChannelProgressReporter as ChannelProgressReporter,
    _TOOL_PROGRESS_LABELS,
    _SILENT_TOOLS,
    _rich_tool_label,
)



# (ChannelProgressReporter and helpers moved to nls.runtime.channels)


_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff"}
_AUDIO_MIMES = {"audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "audio/x-m4a", "audio/webm"}


def _file_kind(mime: str, name: str) -> str:
    """Classify a file for the agent hint text."""
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if mime in _IMAGE_MIMES:
        return "image"
    if mime == "application/pdf" or ext == ".pdf":
        return "document"
    if mime in _AUDIO_MIMES or ext in (".mp3", ".wav", ".ogg", ".m4a", ".webm"):
        return "audio"
    return "file"


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _augment_with_attachments(
    user_input: str,
    attachments: list[dict[str, Any]],
) -> str:
    """Prepend file-attachment context to the user message.

    Mirrors the WebSocket chat pattern from ``server/routes/chat.py``.
    """
    if not attachments:
        return user_input

    lines: list[str] = []
    kinds: set[str] = set()
    for att in attachments:
        name = att.get("name", "file")
        path = att.get("path", "")
        mime = att.get("mime_type", "")
        size = att.get("size", 0)
        kind = _file_kind(mime, name)
        kinds.add(kind)
        lines.append(f"  - {name} ({kind}, {_format_size(size)}) -> {path}")

    hints: list[str] = []
    if kinds & {"file", "document"}:
        hints.append("Use the read tool to examine documents and files.")
    if "audio" in kinds:
        hints.append("Use the read tool on audio files to get a transcript.")
    if "image" in kinds:
        hints.append("Use the vision tool to analyze images.")

    header = (
        f"[The user attached {len(attachments)} file(s):\n"
        + "\n".join(lines)
        + "\n" + " ".join(hints) + "]\n\n"
    )
    return header + user_input


async def _transcribe_audio(file_path: Path) -> str:
    """Transcribe an audio file using the Whisper pipeline.

    Tries the local Whisper model first (same process), falling back to
    the HTTP /transcribe endpoint.
    """
    try:
        from server.routes.transcribe import _get_whisper_model
        import tempfile

        model, backend = _get_whisper_model()

        if backend == "openai-whisper":
            result = model.transcribe(str(file_path), language=None)
            return result.get("text", "").strip()
        else:
            segments, _info = model.transcribe(
                str(file_path), beam_size=5, language=None, vad_filter=True,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
    except Exception:
        pass

    try:
        import httpx
        mime = mimetypes.guess_type(str(file_path))[0] or "audio/ogg"
        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    "http://localhost:8000/transcribe",
                    files={"audio": (file_path.name, f, mime)},
                )
                if resp.status_code == 200:
                    return resp.json().get("text", "")
    except Exception:
        logger.warning("Voice transcription failed for %s", file_path, exc_info=True)
    return ""


async def _handle_voice_attachments(
    attachments: list[dict[str, Any]],
    agent_id: str,
    app: Any,
) -> tuple[str, list[dict[str, Any]]]:
    """Transcribe voice attachments, return (transcription_text, all_attachments).

    Voice/audio attachments are transcribed; the transcription is returned
    separately as text to prepend to user_input.  The attachments
    themselves stay in the list so the agent also knows the audio file
    path and can re-listen if needed.
    """
    am = getattr(app.state, "agent_manager", None)
    if am is None:
        return "", attachments

    voice_parts: list[str] = []
    remaining: list[dict[str, Any]] = []

    for att in attachments:
        is_voice = att.get("is_voice", False)
        mime = att.get("mime_type", "")
        if is_voice or (not is_voice and mime.startswith("audio/")):
            rel_path = att.get("path", "")
            if rel_path:
                full = am.agents_dir / agent_id / "workspace" / rel_path
                if full.is_file():
                    transcript = await _transcribe_audio(full)
                    if transcript:
                        voice_parts.append(
                            f'[Voice message transcription: "{transcript}"]'
                        )
                        remaining.append(att)
                        continue
        remaining.append(att)

    return "\n".join(voice_parts), remaining


def register_autonomous_copilot_queue(
    agent_id: str, queue: asyncio.Queue,
) -> None:
    """Register the copilot queue for a background autonomous dispatch."""
    _autonomous_copilot_queues[agent_id] = queue


def unregister_autonomous_copilot_queue(agent_id: str) -> None:
    """Remove a background autonomous copilot queue when dispatch ends."""
    _autonomous_copilot_queues.pop(agent_id, None)


def try_feed_autonomous_answer(agent_id: str, text: str) -> bool:
    """Route a user answer to a background autonomous ask_user wait."""
    q = _autonomous_copilot_queues.get(agent_id)
    if q is None:
        return False
    try:
        q.put_nowait(text)
        logger.info(
            "Agent %s: routed message as autonomous ask_user answer",
            agent_id,
        )
        return True
    except Exception:
        return False


def _feed_pending_answer_raw(agent_id: str, session_key: str, answer: str) -> bool:
    if try_feed_autonomous_answer(agent_id, answer):
        return True

    key = (agent_id, session_key)
    q = _pending_queues.get(key)
    if q is not None:
        try:
            q.put_nowait(answer)
            logger.info(
                "Channel [%s]: routed message as ask_user answer (session=%s)",
                agent_id, session_key,
            )
            return True
        except Exception:
            pass
    return False


async def build_pending_answer(
    text: str,
    attachments: list[dict[str, Any]] | None,
    agent_id: str,
    app: Any,
) -> str:
    """Build an ask_user answer payload including media context."""
    from nls.skills.channel_adapter_util import channel_history_content

    base = channel_history_content(text, attachments)
    if base == "[empty]":
        base = ""

    if attachments:
        voice_text, remaining = await _handle_voice_attachments(
            attachments, agent_id, app,
        )
        parts: list[str] = []
        if voice_text:
            parts.append(voice_text)
        if base:
            parts.append(base)
        answer = _augment_with_attachments("\n".join(parts), remaining)
    else:
        answer = base

    return answer.strip() or "(media message)"


def try_feed_pending_answer(
    agent_id: str, session_key: str, text: str,
) -> bool:
    """If an agentic loop is waiting for an ask_user answer, feed it.

    Returns True if the message was consumed as an answer (caller should
    NOT start a new processing pipeline).
    """
    return _feed_pending_answer_raw(agent_id, session_key, text)


async def try_feed_pending_answer_async(
    agent_id: str,
    session_key: str,
    text: str,
    *,
    attachments: list[dict[str, Any]] | None = None,
    app: Any = None,
) -> bool:
    """Like ``try_feed_pending_answer`` but includes attachment/voice context."""
    if attachments and app:
        answer = await build_pending_answer(text, attachments, agent_id, app)
    else:
        answer = text or ""
    return _feed_pending_answer_raw(agent_id, session_key, answer)


_HIST_MSG_CAP = 800
_HIST_HEAD = 400
_HIST_TAIL = 200


def _trim_channel_history(history: list[dict]) -> list[dict]:
    """Trim long assistant messages in channel history.

    WM carries important cross-turn facts, so full historical agentic
    outputs are wasteful context.  User messages are kept as-is.
    """
    trimmed: list[dict] = []
    for msg in history:
        if msg.get("role") == "assistant":
            content = msg.get("content") or ""
            if len(content) > _HIST_MSG_CAP:
                content = (
                    content[:_HIST_HEAD]
                    + "\n[...]\n"
                    + content[-_HIST_TAIL:]
                )
                trimmed.append({**msg, "content": content})
                continue
        trimmed.append(msg)
    return trimmed


_STATUS_INQUIRY_RE = re.compile(
    r"\b(status|update|how.{0,20}going|what.{0,20}(doing|working|progress)|"
    r"where.{0,10}(are|we)|report|update\s+me|how\s+far|eta|"
    r"check\s+in|any\s+news|sitrep)\b",
    re.IGNORECASE,
)
_PROCEED_RE = re.compile(
    r"\b(proceed|go\s+ahead|continue|carry\s+on|keep\s+going|ok|yes|sure|"
    r"sounds\s+good|do\s+it|start|begin|approved|confirmed)\b",
    re.IGNORECASE,
)


def _is_status_inquiry(text: str) -> bool:
    """Lightweight check: is this a status/update question or go-ahead?"""
    stripped = text.strip()
    if len(stripped) > 300:
        return False
    return bool(_STATUS_INQUIRY_RE.search(stripped) or _PROCEED_RE.search(stripped))


def _build_quick_status(runtime: Any) -> str:
    """Build a compact status reply from WM/todos/delegates without LLM."""
    parts: list[str] = []

    wm = getattr(runtime, "working_memory", None)
    if wm is None:
        dual = getattr(runtime, "dual_wm", None)
        if dual is not None:
            wm = getattr(dual, "active", None)

    if wm is not None:
        board = wm.get_todo_board()
        if board:
            parts.append(board)

    dm = getattr(runtime, "delegate_manager", None)
    if dm is not None:
        try:
            statuses = dm.get_status()
            running = [s for s in statuses if s.state == "running"]
            if running:
                delegate_lines = []
                for s in running:
                    task_short = (s.task or "")[:80]
                    delegate_lines.append(
                        f"  - Sub-agent #{s.delegate_number}: {task_short} "
                        f"(iter {s.iteration})"
                    )
                parts.append(
                    "Active sub-agents:\n" + "\n".join(delegate_lines)
                )
        except Exception:
            pass

    if not parts:
        return "I'm currently working on it. I'll update you when I have results."

    return "Here's where things stand:\n\n" + "\n\n".join(parts)


def _update_channels_ring(runtime: Any, channel_name: str) -> None:
    """Update the cryptex Channels ring (Ring 12) when a channel message arrives."""
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
    import time as _time
    from nls.brain.working_memory import WMSlot
    ring.upsert_slot(
        domain=f"channel.{channel_name}",
        content=f"{channel_name}: active (last message {_time.strftime('%H:%M')})",
        slot_type="fact",
        salience=0.6,
        source="channel",
        position=channel_name,
    )


async def _direct_channel_dispatch(
    runtime: Any,
    agent_id: str,
    user_input: str,
    history: list[dict],
    channel_adapter: Any | None,
    reply_target: str | None,
    session_key: str | None,
    needs_thinking: bool,
    app: Any,
) -> str:
    """Fallback when the inner loop is not available (startup race)."""
    from nls.runtime.channels import ChannelProgressReporter

    copilot_queue: asyncio.Queue | None = None
    on_event: Callable | None = None
    _queue_key: tuple[str, str] | None = None

    if channel_adapter is not None and reply_target:
        copilot_queue = asyncio.Queue()
        reporter = ChannelProgressReporter(
            channel_adapter, reply_target, agent_id,
        )
        on_event = reporter.on_event
        if session_key:
            _queue_key = (agent_id, session_key)
            _pending_queues[_queue_key] = copilot_queue

    _tm = getattr(runtime, "_team_manager", None)
    if _tm is not None and copilot_queue is not None:
        _tm._copilot_queue = copilot_queue

    _cs = getattr(app.state, "consciousness_scheduler", None)
    if _cs is not None:
        await _cs.on_user_message(agent_id)
        for _ in range(10):
            if not runtime.is_busy:
                break
            await asyncio.sleep(0.3)

    try:
        from nls.runtime import AgentRuntime as _AgentRuntime
        if isinstance(runtime, _AgentRuntime):
            result = await runtime.process_message_agentic_async(
                user_input=user_input,
                history=history,
                enable_thinking=needs_thinking,
                copilot_queue=copilot_queue,
                on_event=on_event,
                source="user:channel",
                session_key=session_key or "",
            )
        else:
            result = await runtime.process_message_agentic_v2(
                user_input=user_input,
                history=history,
                enable_thinking=needs_thinking,
                copilot_queue=copilot_queue,
                on_event=on_event,
                source="user:channel",
            )
    finally:
        if _queue_key:
            _pending_queues.pop(_queue_key, None)
        if _cs is not None:
            _cs.on_user_message_complete(agent_id)

    return result.final_response or ""


async def process_channel_message(
    app: Any,
    runtime: Any,
    agent_id: str,
    user_input: str,
    history: list[dict],
    *,
    channel_adapter: Any | None = None,
    reply_target: str | None = None,
    session_key: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    sender_name: str = "",
    channel_label: str = "",
    raw_content: str = "",
) -> str:
    """Process an inbound channel message through the same pipeline as chat.

    Returns the response text (may be empty if the model produced nothing).

    Parameters
    ----------
    channel_adapter, reply_target, session_key
        When provided, enables ask_user and progress updates through the
        channel (WhatsApp, Telegram, etc.) during agentic loops.
    attachments
        File attachments saved to workspace/uploads/. Voice messages are
        auto-transcribed and prepended to user_input.
    """

    if attachments:
        voice_text, attachments = await _handle_voice_attachments(
            attachments, agent_id, app,
        )
        if voice_text:
            user_input = f"{voice_text}\n{user_input}"
        user_input = _augment_with_attachments(user_input, attachments)

    # Stop daydream/DMN immediately (same priority as web chat) without
    # pausing the inner loop — channel replies are dispatched on the next breath.
    _cs = getattr(app.state, "consciousness_scheduler", None)
    if _cs is not None:
        preempt = getattr(_cs, "preempt_background", None)
        if callable(preempt):
            preempt(agent_id)
        else:
            await _cs.on_user_message(agent_id)

    if session_key:
        from nls.runtime.channel_ambient import (
            is_shared_channel_session,
            recent_ambient_snippet,
        )
        if is_shared_channel_session(session_key):
            agent_dir = getattr(runtime, "agent_dir", None)
            if agent_dir is not None:
                try:
                    prefix = recent_ambient_snippet(
                        agent_dir, session_key, limit=8, exclude_last=1,
                    )
                    if prefix:
                        user_input = prefix + user_input
                except Exception as exc:
                    logger.debug(
                        "Channel [%s]: ambient snippet skipped: %s", agent_id, exc,
                    )

    _channel_source = "channel"
    if channel_adapter is not None:
        _channel_source = getattr(channel_adapter, "channel_name", "channel")

    if session_key:
        from nls.runtime.surface_inbox import (
            record_surface_inbound,
            should_defer_cross_surface,
            try_feed_active_copilot,
        )

        record_surface_inbound(
            agent_id,
            session_key=session_key,
            channel=_channel_source,
            channel_label=channel_label,
            sender_name=sender_name or "?",
            content=raw_content or user_input,
            runtime=runtime,
        )

        cross_surface_deferred = should_defer_cross_surface(runtime, session_key)
        if cross_surface_deferred:
            try_feed_active_copilot(runtime, user_input)
            logger.info(
                "Channel [%s]: cross-surface defer — copilot + background queue "
                "(foreground=%s, inbound=%s)",
                agent_id,
                getattr(runtime, "_foreground_session_key", ""),
                session_key,
            )
    else:
        cross_surface_deferred = False

    model_manager = getattr(app.state, "model_manager", None)
    _cs_obj = getattr(app.state, "consciousness_scheduler", None)
    _inner_loop = _cs_obj.get_inner_loop(agent_id) if _cs_obj is not None else None
    _runtime_agentic = (
        hasattr(runtime, "process_message_agentic_v2")
        or hasattr(runtime, "process_message_agentic_async")
    )
    _agentic_config_on = True
    _is_ae = getattr(runtime, "is_agentic_enabled", None)
    if callable(_is_ae):
        _agentic_config_on = _is_ae()
    # Channel turns need tools (discord_send, channel_inspect, …). Do not fall
    # back to chat mode when agentic async is available — chat mode leaks pseudo
    # tool calls like ``channel_inspect(...)`` as plain outbound text.
    # Prefer the inner-loop event queue (async reply) over blocking chat mode.
    agentic_enabled = (
        _runtime_agentic
        and _agentic_config_on
        and (_inner_loop is not None or model_manager is not None)
    )

    # V5 is self-routing: the model's first generation decides whether to call
    # tools (→ loop) or respond with text (→ chat).  No pre-classification
    # needed.  Always enter the agentic path when the runtime supports it so
    # the model can call todo.add, plan, browser, etc. from any channel.
    needs_thinking = True

    if agentic_enabled:
        logger.info("Channel [%s]: agentic entry (think=%s)", agent_id, needs_thinking)

        from nls.engine.events import AgentEvent, EventType

        from nls.runtime.squad_channel_policy import channel_delivery_allowed

        _allowed, _refusal = channel_delivery_allowed(
            app, agent_id, _channel_source,
        )
        if not _allowed:
            logger.info(
                "Channel [%s]: blocked for agent %s — not squad lead",
                _channel_source, agent_id,
            )
            return _refusal

        # Update cryptex Channels ring (Ring 12)
        _update_channels_ring(runtime, _channel_source)

        # Trim history to avoid bloating the event payload
        _trimmed_history = _trim_channel_history(history)

        # Build event with full reply metadata (survives serialization)
        _ch_event = AgentEvent(
            type=EventType.CHANNEL_MESSAGE,
            source=_channel_source,
            payload={
                "user_input": user_input,
                "session_key": session_key or "",
                "channel_name": _channel_source,
                "reply_target": reply_target or "",
                "needs_thinking": needs_thinking,
                "agent_id": agent_id,
                "history": _trimmed_history,
                "user_direct": True,
            },
        )

        # Push to event queue — the inner loop owns all routing & dispatch
        if _inner_loop is not None:
            if cross_surface_deferred:
                logger.info(
                    "Channel [%s]: cross-surface defer — inbox only "
                    "(foreground=%s, inbound=%s)",
                    agent_id,
                    getattr(runtime, "_foreground_session_key", ""),
                    session_key,
                )
                return ""
            flush_pending_channel_events(agent_id, _inner_loop)
            _inner_loop.push_event(_ch_event)
            logger.info(
                "Channel [%s]: event pushed to inner loop (channel=%s, target=%s)",
                agent_id, _channel_source, reply_target or "none",
            )
        elif cross_surface_deferred:
            stash_deferred_channel_event(agent_id, _ch_event)
            logger.info(
                "Channel [%s]: cross-surface defer — stashed until inner loop "
                "available (foreground=%s)",
                agent_id,
                getattr(runtime, "_foreground_session_key", ""),
            )
        else:
            logger.warning(
                "Channel [%s]: no inner loop — falling back to direct dispatch",
                agent_id,
            )
            return await _direct_channel_dispatch(
                runtime, agent_id, user_input, _trimmed_history,
                channel_adapter, reply_target, session_key, needs_thinking, app,
            )

        return ""
    else:
        logger.info("Channel [%s]: chat mode", agent_id)

        _cs = getattr(app.state, "consciousness_scheduler", None)
        if _cs is not None:
            await _cs.on_user_message(agent_id)

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    runtime.process_message,
                    user_input,
                    history=history,
                ),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Channel [%s]: chat mode timed out after 120s", agent_id,
            )
            return (
                "Sorry, I'm taking too long to respond right now. "
                "Please try again in a moment."
            )
        finally:
            if _cs is not None:
                _cs.on_user_message_complete(agent_id)
        from nls.runtime.response_cleanup import sanitize_channel_outbound

        raw = ""
        if hasattr(result, "response"):
            raw = result.response or ""
        elif isinstance(result, dict):
            raw = result.get("response", "")
        cleaned = sanitize_channel_outbound(raw)
        if raw.strip() and not cleaned:
            logger.warning(
                "Channel [%s]: chat response was tool-call leak — not sending (%r)",
                agent_id,
                raw[:120],
            )
        return cleaned
