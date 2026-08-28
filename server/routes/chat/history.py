"""History persistence helpers for the chat module."""

from __future__ import annotations

import logging
import re

from .helpers import _build_agentic_metadata

logger = logging.getLogger(__name__)

_SKILL_ONBOARDING_RE = re.compile(
    r"\n\n\[SKILL ONBOARDING[^\]]*\].*?\[/SKILL ONBOARDING\]",
    re.DOTALL,
)

# Loop-control / stall nudges — omit from persisted conversation history.
_HISTORY_INTERNAL_MARKERS = (
    "[REMEMBERED",
    "Review: did you complete ALL parts",
    "[SKILL ONBOARDING",
    "[VISUAL FEEDBACK]",
    "[RELEVANT KNOWLEDGE]",
    "[PLAN POSITION",
    "Reminder: after completing a step",
    "You have been on this step for",
    "DIAGNOSE: Read the error output above",
    "PIVOT: Your current approach is not working",
    "The tool returned a result. If this answers",
    "You have not taken any action yet. Use a tool call NOW",
    "VERIFY: You just took an action",
    "[DELEGATE CHECK-BACK]",
    "[AGENT_MSG|",
    "[PAGE after ",
    "Your last responses were text, not tool calls",
    "Your last responses were text-only",
    "Now communicate your findings to the user",
    "You appear to be stuck",
    "[Loop stopped: stalled",
    "[SYSTEM — Mission Context]",
)


def _strip_internal_blocks(text: str) -> str:
    """Remove injected system blocks before persisting to history."""
    return _SKILL_ONBOARDING_RE.sub("", text)


def branch_session_metadata(
    session_key: str | None,
    *,
    label: str | None = None,
) -> dict | None:
    """Metadata persisted alongside branch session history."""
    sk = (session_key or "websocket:main").strip()
    if is_main_chat_session(sk):
        return None
    meta: dict = {"channel": "websocket"}
    if label and str(label).strip():
        meta["label"] = str(label).strip()
    return meta


def record_visible_chat_turn(
    runtime,
    *,
    user: str | None = None,
    assistant: str | None = None,
    reasoning: str | None = None,
    metadata: dict | None = None,
    attachments: list | None = None,
    session_key: str | None = None,
) -> None:
    """Persist a user-visible turn for UI transcript restore."""
    sk = (session_key or "websocket:main").strip()
    if sk != "websocket:main":
        record = getattr(runtime, "record_session_turn", None)
        if callable(record):
            try:
                record(
                    session_key=sk,
                    user=_strip_internal_blocks(user) if user else None,
                    assistant=assistant,
                    reasoning=reasoning,
                    metadata=metadata,
                    attachments=attachments,
                )
                return
            except Exception:
                logger.debug("record_session_turn failed", exc_info=True)
    record = getattr(runtime, "record_chat_turn", None)
    if not callable(record):
        return
    try:
        record(
            user=_strip_internal_blocks(user) if user else None,
            assistant=assistant,
            reasoning=reasoning,
            metadata=metadata,
            attachments=attachments,
        )
    except Exception:
        logger.debug("record_visible_chat_turn failed", exc_info=True)


def is_main_chat_session(session_key: str | None) -> bool:
    return (session_key or "websocket:main").strip() == "websocket:main"


def persist_conversation_turn(
    runtime,
    session_key: str | None,
    history: list[dict],
    *,
    user: str | None = None,
    assistant: str | None = None,
    reasoning: str | None = None,
    metadata: dict | None = None,
    attachments: list | None = None,
    session_metadata: dict | None = None,
    max_history: int = 40,
) -> None:
    """Save model history + UI transcript for main or branch threads."""
    _persist_model_history(
        runtime,
        session_key,
        history,
        session_metadata=session_metadata,
        max_history=max_history,
    )
    record_visible_chat_turn(
        runtime,
        user=user,
        assistant=assistant,
        reasoning=reasoning,
        metadata=metadata,
        attachments=attachments,
        session_key=session_key,
    )


def _persist_model_history(
    runtime,
    session_key: str | None,
    history: list[dict],
    *,
    session_metadata: dict | None = None,
    max_history: int = 40,
) -> None:
    sk = (session_key or "websocket:main").strip()
    trimmed = history[-max_history:] if len(history) > max_history else history
    if is_main_chat_session(sk):
        runtime.save_conversation_history(trimmed)
        return

    meta = dict(session_metadata or {})
    meta.setdefault("channel", "websocket")
    runtime.save_session_history(
        trimmed,
        session_key=sk,
        max_turns=200,
        metadata=meta or None,
    )


def finalize_agentic_turn_after_checkpoint(
    runtime,
    session_key: str | None,
    history: list[dict],
    *,
    assistant: str | None = None,
    reasoning: str | None = None,
    metadata: dict | None = None,
    attachments: list | None = None,
    session_metadata: dict | None = None,
    max_history: int = 40,
) -> None:
    """Upgrade eager checkpoint: authoritative model history, patch UI row."""
    _persist_model_history(
        runtime,
        session_key,
        history,
        session_metadata=session_metadata,
        max_history=max_history,
    )
    agent_dir = getattr(runtime, "agent_dir", None)
    if agent_dir is None:
        return
    from nls.runtime.session import patch_last_transcript_turn

    patch_last_transcript_turn(
        agent_dir,
        (session_key or "websocket:main").strip(),
        assistant=assistant,
        reasoning=reasoning,
        metadata=metadata,
        attachments=attachments,
    )


def persist_partial_agentic_transcript(
    runtime,
    *,
    user_input: str,
    eager_events: list[dict],
    initial_thinking: str | None = None,
    aborted: bool = True,
    abort_reason: str = "Connection closed during task",
    attachments: list | None = None,
    session_key: str | None = None,
) -> None:
    """Save in-progress agentic trace when the client disconnects mid-loop."""
    user_text = _strip_internal_blocks(user_input)
    if not eager_events and not user_text.strip():
        return
    metadata: dict | None = None
    if eager_events:
        total_tool_calls = sum(
            len(ev.get("tool_calls") or []) for ev in eager_events
        )
        iterations = max((ev.get("step") or 0) for ev in eager_events)
        metadata = {
            "agentic": True,
            "iterations": iterations,
            "tool_calls": total_tool_calls,
            "aborted": aborted,
            "abort_reason": abort_reason,
            "events": list(eager_events),
        }
    record_visible_chat_turn(
        runtime,
        user=user_text or None,
        assistant=None,
        reasoning=initial_thinking or None,
        metadata=metadata,
        attachments=attachments or None,
        session_key=session_key,
    )
    sk = (session_key or "websocket:main").strip()
    if is_main_chat_session(sk) or not user_text.strip():
        return
    load = getattr(runtime, "load_session_history", None)
    save = getattr(runtime, "save_session_history", None)
    if not callable(load) or not callable(save):
        return
    try:
        history = list(load(session_key=sk, max_turns=200) or [])
        history.append({"role": "user", "content": user_text})
        if metadata:
            history.append({
                "role": "assistant",
                "content": (
                    "[Task interrupted — partial progress saved. "
                    "Continue from here on the next message.]"
                ),
                "metadata": metadata,
            })
        save(
            history,
            session_key=sk,
            max_turns=200,
            metadata=branch_session_metadata(sk),
        )
    except Exception:
        logger.debug("partial agentic session history save failed", exc_info=True)


def _save_agentic_history(
    history: list[dict],
    result,
) -> None:
    """Append agentic loop results to conversation history.

    Saves a condensed tool exchange so the model has in-context examples
    of making function calls.
    """
    for event in result.events:
        if not event.tool_calls:
            continue

        import json as _json
        openai_tc = []
        for i, tc in enumerate(event.tool_calls):
            call_id = f"call_{tc['name']}_{event.iteration}_{i}"
            openai_tc.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": _json.dumps(
                        tc.get("arguments", {}), ensure_ascii=False,
                    ),
                },
            })

        history.append({
            "role": "assistant",
            "content": "",
            "tool_calls": openai_tc,
        })

        for i, tr in enumerate(event.tool_results):
            call_id = openai_tc[i]["id"] if i < len(openai_tc) else f"call_{i}"
            preview = tr.get("result_preview", "")
            success = tr.get("success", True)
            prefix = "" if success else "[ERROR] "
            history.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"{prefix}{preview}",
            })

    if result.final_response:
        meta: dict = {
            "agentic": True,
            "iterations": result.iterations,
            "tool_calls": result.total_tool_calls,
            "aborted": result.aborted,
            "events": [
                {
                    "step": ev.iteration,
                    "tool_calls": [{"name": tc.get("name", "tool")} for tc in ev.tool_calls],
                    "tool_results": [{"success": tr.get("success", True)} for tr in ev.tool_results],
                    "hormones": ev.hormones,
                    "duration_ms": ev.duration_ms,
                }
                for ev in result.events if ev.tool_calls
            ],
        }
        history.append({
            "role": "assistant",
            "content": result.final_response,
            "metadata": meta,
        })


def _salvage_agentic_context(
    history: list[dict],
    shared_context: list[dict],
    user_input: str,
    runtime,
    agent_id: str,
    *,
    session_key: str | None = None,
) -> None:
    """Best-effort salvage of agentic context on crash."""
    if not shared_context:
        return

    first_tool_idx = -1
    for i, msg in enumerate(shared_context):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            first_tool_idx = i
            break

    if first_tool_idx < 0:
        logger.info(
            "Agent %s: crash salvage \u2014 no tool_calls in shared_context "
            "(%d msgs), nothing to save",
            agent_id, len(shared_context),
        )
        return

    history.append({"role": "user", "content": _strip_internal_blocks(user_input)})

    loop_msgs = shared_context[first_tool_idx:]
    for msg in loop_msgs:
        entry = dict(msg)
        if entry.get("role") == "tool":
            content = entry.get("content", "")
            if len(content) > 500:
                entry["content"] = content[:500] + "\n... (truncated)"
        elif entry.get("role") == "user":
            content = entry.get("content") or ""
            if isinstance(content, list):
                continue
            if any(marker in content for marker in _HISTORY_INTERNAL_MARKERS):
                continue
        history.append(entry)

    if history and history[-1].get("role") != "assistant":
        history.append({
            "role": "assistant",
            "content": (
                "[Task interrupted \u2014 progress saved up to this point. "
                "The agent can continue from here on the next message.]"
            ),
        })

    if len(history) > 40:
        history[:] = history[-40:]

    try:
        sk = (session_key or "websocket:main").strip()
        meta = branch_session_metadata(sk) if not is_main_chat_session(sk) else None
        persist_conversation_turn(
            runtime,
            sk,
            history,
            user=_strip_internal_blocks(user_input),
            assistant=history[-1].get("content") if history else None,
            session_metadata=meta,
        )
        logger.info(
            "Agent %s: crash salvage saved %d history entries "
            "(%d context msgs recovered, session=%s)",
            agent_id, len(history), len(loop_msgs), sk,
        )
    except Exception as exc:
        logger.warning(
            "Agent %s: crash salvage save failed: %s", agent_id, exc,
        )


def _save_agentic_history_v2(
    history: list[dict],
    result,
) -> None:
    """Append v2/v3 agentic loop context to conversation history.

    Anchors on the first assistant message that carries ``tool_calls``
    (the loop's first inference) to extract the loop's own messages.
    """
    ctx = getattr(result, "context_messages", None) or []
    agentic_meta = _build_agentic_metadata(result)

    _loop_start = getattr(result, "loop_start_idx", 0) or 0
    first_tool_idx = -1
    for i, msg in enumerate(ctx):
        if i < _loop_start:
            continue
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            first_tool_idx = i
            break

    _role_counts = {}
    for _m in ctx:
        _r = _m.get("role", "?")
        _role_counts[_r] = _role_counts.get(_r, 0) + 1
    logger.info(
        "_save_agentic_history_v2: ctx_len=%d roles=%s "
        "first_tool_idx=%d meta=%s final_resp=%d",
        len(ctx), _role_counts, first_tool_idx,
        {k: v for k, v in agentic_meta.items() if k != "events"},
        len(result.final_response or ""),
    )

    if first_tool_idx < 0:
        logger.info(
            "_save_agentic_history_v2: no tool_calls in context \u2014 "
            "saving final_response only",
        )
        if result.final_response:
            history.append({
                "role": "assistant",
                "content": result.final_response,
                "metadata": agentic_meta,
            })
        return

    loop_messages = ctx[first_tool_idx:]
    _loop_roles = {}
    for _m in loop_messages:
        _r = _m.get("role", "?")
        _loop_roles[_r] = _loop_roles.get(_r, 0) + 1
    logger.info(
        "_save_agentic_history_v2: loop_messages=%d roles=%s",
        len(loop_messages), _loop_roles,
    )

    _last_content_assistant = None
    for msg in loop_messages:
        if msg.get("role") == "tool":
            content = msg.get("content") or ""
            if len(content) > 500:
                msg["content"] = content[:500] + "\n... (truncated)"
        elif msg.get("role") == "user":
            content = msg.get("content") or ""
            if isinstance(content, list):
                continue
            if any(marker in content for marker in _HISTORY_INTERNAL_MARKERS):
                continue
        history.append(msg)
        if msg.get("role") == "assistant" and msg.get("content"):
            _last_content_assistant = msg

    if _last_content_assistant is not None:
        _last_content_assistant["metadata"] = agentic_meta
    elif result.final_response:
        history.append({
            "role": "assistant",
            "content": result.final_response,
            "metadata": agentic_meta,
        })
