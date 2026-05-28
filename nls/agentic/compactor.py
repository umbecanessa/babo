"""v4 anchored iterative context compaction.

Replaces v3's full re-summarization with incremental structured anchor
updates.  The anchor tracks goal, progress, decisions, files touched,
and next steps — only new dropped spans get summarized and merged in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .generator import sanitize_context
from .types import LoopConfig

logger = logging.getLogger(__name__)

_TOOL_RESULT_MAX_FOR_SUMMARY = 500


# -------------------------------------------------------------------
# Anchor + Delta
# -------------------------------------------------------------------

@dataclass
class CompactionAnchor:
    """Persistent structured summary, updated incrementally."""

    goal: str = ""
    progress_done: list[str] = field(default_factory=list)
    progress_pending: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    communications_sent: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    available_tools: list[str] = field(default_factory=list)
    iteration_at: int = 0

    def to_context_message(self) -> dict:
        """Render anchor as a system-injected context summary."""
        parts = [f"[CONTEXT SUMMARY — updated at iteration {self.iteration_at}]"]
        if self.goal:
            parts.append(f"\n## Goal\n{self.goal}")
        if self.available_tools:
            parts.append(
                "\n## Available Tools (use these, NOT bash equivalents)\n"
                + ", ".join(self.available_tools)
            )
        if self.communications_sent:
            parts.append("\n## Communications Already Sent (DO NOT re-send)")
            for item in self.communications_sent:
                parts.append(f"- {item}")
        if self.progress_done or self.progress_pending:
            parts.append("\n## Progress")
            if self.progress_done:
                parts.append("### Done")
                for item in self.progress_done[-20:]:
                    parts.append(f"- [x] {item}")
            if self.progress_pending:
                parts.append("### Pending")
                for item in self.progress_pending[-10:]:
                    parts.append(f"- [ ] {item}")
        if self.decisions:
            parts.append("\n## Key Decisions")
            for item in self.decisions[-10:]:
                parts.append(f"- {item}")
        if self.files_read or self.files_modified:
            parts.append("\n## Files Touched")
            if self.files_read:
                parts.append(f"Read: {', '.join(self.files_read[-15:])}")
            if self.files_modified:
                parts.append(f"Modified: {', '.join(self.files_modified[-15:])}")
        if self.next_steps:
            parts.append("\n## Next Steps")
            for item in self.next_steps[-5:]:
                parts.append(f"- {item}")
        parts.append("\n[END CONTEXT SUMMARY]")
        return {
            "role": "system",
            "content": "\n".join(parts),
        }

    def merge(self, delta: "CompactionDelta", iteration: int) -> None:
        """Merge a new delta into the anchor."""
        if delta.goal and not self.goal:
            self.goal = delta.goal
        for item in delta.progress_done:
            if item not in self.progress_done:
                self.progress_done.append(item)
        promoted = set(delta.progress_done)
        self.progress_pending = [
            p for p in self.progress_pending if p not in promoted
        ]
        for item in delta.progress_pending:
            if item not in self.progress_pending and item not in self.progress_done:
                self.progress_pending.append(item)
        for item in delta.decisions:
            if item not in self.decisions:
                self.decisions.append(item)
        for f in delta.files_read:
            if f not in self.files_read:
                self.files_read.append(f)
        for f in delta.files_modified:
            if f not in self.files_modified:
                self.files_modified.append(f)
        for item in delta.communications_sent:
            if item not in self.communications_sent:
                self.communications_sent.append(item)
        if delta.next_steps:
            self.next_steps = delta.next_steps
        self.iteration_at = iteration


@dataclass
class CompactionDelta:
    """Changes from a newly-dropped span."""

    goal: str = ""
    progress_done: list[str] = field(default_factory=list)
    progress_pending: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    communications_sent: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


# -------------------------------------------------------------------
# Token estimation
# -------------------------------------------------------------------

def _estimate_message_chars(context: list[dict]) -> int:
    """Total character count across all message bodies (excludes tool schemas)."""
    total_chars = 0
    for msg in context:
        content = msg.get("content") or ""
        if content:
            total_chars += len(content)
        for tc in msg.get("tool_calls", []):
            total_chars += len(tc.get("function", {}).get("arguments", ""))
    return total_chars


def _estimate_tokens(context: list[dict]) -> int:
    """Estimate token count as chars / 4 (rough heuristic)."""
    return _estimate_message_chars(context) // 4


# -------------------------------------------------------------------
# Cut point logic
# -------------------------------------------------------------------

def _find_cut_point(context: list[dict], keep_tokens: int) -> int:
    """Walk backwards and return the index where to cut.

    context[:cut] is summarized/dropped, context[cut:] is kept.
    Never cuts between an assistant tool_calls message and its tool results.
    """
    accum = 0
    cut = len(context)
    i = len(context) - 1
    while i >= 0:
        msg = context[i]
        msg_chars = len(msg.get("content") or "")
        for tc in msg.get("tool_calls", []):
            msg_chars += len(tc.get("function", {}).get("arguments", ""))
        msg_tokens = msg_chars // 4
        if accum + msg_tokens > keep_tokens and cut < len(context):
            break
        accum += msg_tokens
        cut = i
        i -= 1

    if cut <= 1:
        return 0

    # Ensure we don't orphan tool results from their assistant message.
    # If context[cut] is a tool message, pull cut back to include
    # the assistant with tool_calls that produced those tool results.
    while cut > 1 and context[cut].get("role") == "tool":
        cut -= 1
    if (
        cut > 0
        and context[cut].get("role") == "assistant"
        and context[cut].get("tool_calls")
    ):
        pass  # good — assistant + its tools are together in kept section
    elif cut > 1 and context[cut - 1].get("role") == "assistant" and context[cut - 1].get("tool_calls"):
        cut -= 1  # include the assistant

    return cut


_COMM_TOOLS = frozenset({"whatsapp_send", "telegram_send", "email_send"})


def _extract_file_ops(
    messages: list[dict],
) -> tuple[list[str], list[str], list[str]]:
    """Extract file paths and communication sends from messages being dropped.

    Returns (files_read, files_modified, communications_sent).
    Communications are structurally extracted so they survive compaction
    even if the LLM summary misses them.
    """
    files_read: list[str] = []
    files_modified: list[str] = []
    comms_sent: list[str] = []
    _read_re = re.compile(r'"path"\s*:\s*"([^"]+)"')
    _write_re = re.compile(r'"(?:path|file_path)"\s*:\s*"([^"]+)"')
    _phone_re = re.compile(r'"(?:phone|to|number)"\s*:\s*"([^"]+)"')
    _text_re = re.compile(r'"(?:text|body|subject)"\s*:\s*"([^"]{1,80})')

    # Also check tool results for successful sends
    _tool_results: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "tool":
            call_id = msg.get("tool_call_id", "")
            content = msg.get("content", "")
            if call_id:
                _tool_results[call_id] = content

    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args_str = fn.get("arguments", "")
            call_id = tc.get("id", "")
            if name == "read":
                m = _read_re.search(args_str)
                if m:
                    files_read.append(m.group(1))
            elif name in ("write", "edit"):
                m = _write_re.search(args_str)
                if m:
                    files_modified.append(m.group(1))
            elif name in _COMM_TOOLS:
                result_content = _tool_results.get(call_id, "")
                is_error = "error" in result_content.lower()[:50]
                if not is_error and result_content:
                    recipient = ""
                    m_phone = _phone_re.search(args_str)
                    if m_phone:
                        recipient = m_phone.group(1)
                    preview = ""
                    m_text = _text_re.search(args_str)
                    if m_text:
                        preview = m_text.group(1)
                    channel = name.replace("_send", "")
                    desc = f"{channel} to {recipient}" if recipient else channel
                    if preview:
                        desc += f": {preview}..."
                    comms_sent.append(desc)
    return files_read, files_modified, comms_sent


# -------------------------------------------------------------------
# Trigger check
# -------------------------------------------------------------------

def should_compact(
    context: list[dict],
    config: LoopConfig,
    anchor: CompactionAnchor,
) -> bool:
    """Check if compaction is needed based on token budget or relay body size.

    Uses ``config.compaction_trigger_ratio`` (default 0.85) as a safety
    margin to trigger compaction proactively before the model hits a hard
    context-length error.  When ``relay_compact_message_chars`` is set,
    compaction also triggers when message bodies alone exceed that threshold
    (leaves headroom for tool JSON schemas on cloud relay paths).
    """
    msg_chars = _estimate_message_chars(context)
    relay_threshold = getattr(config, "relay_compact_message_chars", 0)
    if relay_threshold and msg_chars > relay_threshold:
        return True

    est = msg_chars // 4
    hard_limit = config.context_window_tokens - config.reserve_tokens
    ratio = getattr(config, "compaction_trigger_ratio", 0.85)
    proactive_threshold = int(hard_limit * ratio)
    return est > proactive_threshold


# -------------------------------------------------------------------
# Serialization for summarization
# -------------------------------------------------------------------

def _serialize_for_summary(messages: list[dict]) -> str:
    """Serialize context messages into text for the summarization LLM."""
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        if role == "system":
            continue
        if role == "user":
            parts.append(f"[User]: {content[:1000]}")
        elif role == "assistant":
            text_parts: list[str] = []
            if content.strip():
                text_parts.append(content[:800])
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                args = fn.get("arguments", "")[:200]
                text_parts.append(f"  tool: {name}({args})")
            if text_parts:
                parts.append(f"[Assistant]: {chr(10).join(text_parts)}")
        elif role == "tool":
            preview = content[:_TOOL_RESULT_MAX_FOR_SUMMARY]
            if len(content) > _TOOL_RESULT_MAX_FOR_SUMMARY:
                preview += f"\n[...{len(content) - _TOOL_RESULT_MAX_FOR_SUMMARY} chars truncated]"
            parts.append(f"[Tool result]: {preview}")
    return "\n\n".join(parts)


# -------------------------------------------------------------------
# Summary → Delta parsing
# -------------------------------------------------------------------

_SUMMARIZE_PROMPT = (
    "Summarize the conversation span below into a structured delta.\n\n"
    "Return a JSON object with EXACTLY these fields:\n"
    '{\n'
    '  "goal": "what the user wants (only if first summary, else empty string)",\n'
    '  "progress_done": ["completed action 1", "completed action 2"],\n'
    '  "progress_pending": ["still pending 1"],\n'
    '  "decisions": ["decision: rationale"],\n'
    '  "files_read": ["/path/to/file"],\n'
    '  "files_modified": ["/path/to/file"],\n'
    '  "communications_sent": ["whatsapp to +1234: project update", "email to user@example.com: report"],\n'
    '  "next_steps": ["what should happen next"]\n'
    '}\n\n'
    "IMPORTANT: communications_sent must include ALL messages sent via "
    "whatsapp_send, telegram_send, email_send, or similar tools — "
    "include the channel, recipient, and a brief description. "
    "These must NEVER be forgotten.\n\n"
    "Be concise. Preserve exact file paths and identifiers. "
    "Return ONLY the JSON object."
)

_SUMMARIZE_SYSTEM = (
    "You are a context summarization assistant. Read a conversation span "
    "and extract structured information. Do NOT continue the conversation. "
    "Output ONLY the JSON object requested."
)


def _parse_delta(text: str) -> CompactionDelta:
    """Parse LLM output into a CompactionDelta."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return CompactionDelta()
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return CompactionDelta()

    def _list(key: str) -> list[str]:
        val = data.get(key, [])
        if isinstance(val, list):
            return [str(v).strip() for v in val if str(v).strip()]
        return []

    return CompactionDelta(
        goal=str(data.get("goal", "")).strip(),
        progress_done=_list("progress_done"),
        progress_pending=_list("progress_pending"),
        decisions=_list("decisions"),
        files_read=_list("files_read"),
        files_modified=_list("files_modified"),
        communications_sent=_list("communications_sent"),
        next_steps=_list("next_steps"),
    )


async def _summarize_span(
    messages: list[dict],
    previous_anchor: CompactionAnchor,
    vllm_client: Any,
    config: LoopConfig,
) -> CompactionDelta:
    """Summarize a dropped span into a structured delta via LLM."""
    conversation_text = _serialize_for_summary(messages)

    context_note = ""
    if previous_anchor.goal:
        context_note = (
            f"\n\nExisting context: Goal is '{previous_anchor.goal}'. "
            f"Already done: {len(previous_anchor.progress_done)} items. "
            f"Only extract NEW information from the span below."
        )

    prompt = (
        f"<span>\n{conversation_text}\n</span>\n\n"
        f"{_SUMMARIZE_PROMPT}{context_note}"
    )

    result = await asyncio.wait_for(
        vllm_client.generate(
            messages=[
                {"role": "system", "content": _SUMMARIZE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            adapter_name=None,
            max_tokens=512,
            temperature=0.2,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": False},
            },
        ),
        timeout=config.compaction_timeout,
    )
    text = (result.text or "").strip()
    delta = _parse_delta(text)

    file_reads, file_mods, comms = _extract_file_ops(messages)
    for f in file_reads:
        if f not in delta.files_read:
            delta.files_read.append(f)
    for f in file_mods:
        if f not in delta.files_modified:
            delta.files_modified.append(f)
    for c in comms:
        if c not in delta.communications_sent:
            delta.communications_sent.append(c)

    return delta


# -------------------------------------------------------------------
# Simple fallback compaction
# -------------------------------------------------------------------

def _simple_compact(
    context: list[dict],
    config: LoopConfig,
    force: bool = False,
) -> list[dict]:
    """Fallback: simple recency-based compaction (no LLM call).

    Does not truncate tool result bodies — bash/list_dir output is preserved
    in kept messages.  Large reads keep their full tool output; cognitive
    digests live in working memory only.
    """
    est = _estimate_tokens(context)
    threshold = config.context_window_tokens - config.reserve_tokens
    if not force and est <= threshold:
        return context

    system_msgs = [m for m in context if m.get("role") == "system"]
    other_msgs = [m for m in context if m.get("role") != "system"]
    keep_recent = max(4 if force else 6, len(other_msgs) // 3)
    old_msgs = other_msgs[:-keep_recent] if keep_recent < len(other_msgs) else []
    priority_msgs = [
        m for m in old_msgs
        if "[USER MESSAGE" in (m.get("content") or "")
        or (m.get("content") or "").startswith("[Digest of ")
    ]
    trimmed = system_msgs + priority_msgs + other_msgs[-keep_recent:]

    # vLLM requires at least one user-role message.  If compaction
    # dropped all of them, re-inject the most recent one from old_msgs,
    # or synthesize a placeholder so the request doesn't fail with
    # "No user query found in messages."
    has_user = any(m.get("role") == "user" for m in trimmed)
    if not has_user:
        last_user = None
        for m in reversed(other_msgs):
            if m.get("role") == "user":
                last_user = m
                break
        if last_user is not None:
            insert_at = len(system_msgs)
            trimmed.insert(insert_at, last_user)
        else:
            insert_at = len(system_msgs)
            trimmed.insert(insert_at, {
                "role": "user",
                "content": "[Continued from previous context — execute the active plan.]",
            })

    logger.info(
        "Simple compaction: %d → %d messages (force=%s)",
        len(context), len(trimmed), force,
    )
    return sanitize_context(trimmed)


# -------------------------------------------------------------------
# Main compaction function
# -------------------------------------------------------------------

async def compact(
    context: list[dict],
    anchor: CompactionAnchor,
    config: LoopConfig,
    vllm_client: Any,
    *,
    force: bool = False,
    iteration: int = 0,
) -> tuple[list[dict], CompactionAnchor]:
    """Anchored iterative compaction.

    1. Find cut point keeping recent ~keep_recent_tokens.
    2. Extract file operations from dropped span.
    3. Summarize ONLY the new dropped span via LLM → CompactionDelta.
    4. Merge delta into anchor.
    5. Return [system_msgs] + [anchor_msg] + [kept_msgs].

    Falls back to simple compaction if LLM call fails.
    """
    keep = config.keep_recent_tokens // 2 if force else config.keep_recent_tokens
    cut = _find_cut_point(context, keep)
    if cut <= 1:
        return _simple_compact(context, config, force=force), anchor

    system_msgs = [m for m in context[:cut] if m.get("role") == "system"]
    msgs_to_summarize = [m for m in context[:cut] if m.get("role") != "system"]
    recent_msgs = list(context[cut:])

    # Move priority old messages (user messages, digests) to recent
    priority = [
        m for m in msgs_to_summarize
        if "[USER MESSAGE" in (m.get("content") or "")
        or (m.get("content") or "").startswith("[Digest of ")
    ]
    if priority:
        msgs_to_summarize = [m for m in msgs_to_summarize if m not in priority]
        recent_msgs = priority + recent_msgs

    if not msgs_to_summarize:
        return _simple_compact(context, config, force=force), anchor

    if vllm_client is None:
        return _simple_compact(context, config, force=force), anchor

    # Remove existing anchor from system messages (we'll re-inject updated one)
    system_msgs = [
        m for m in system_msgs
        if not (m.get("content") or "").startswith("[CONTEXT SUMMARY")
    ]

    try:
        delta = await _summarize_span(
            msgs_to_summarize, anchor, vllm_client, config,
        )
        anchor.merge(delta, iteration)

        compacted = system_msgs + [anchor.to_context_message()] + recent_msgs

        has_user = any(m.get("role") == "user" for m in compacted)
        if not has_user:
            last_user = None
            for m in reversed(msgs_to_summarize):
                if m.get("role") == "user":
                    last_user = m
                    break
            insert_at = len(system_msgs) + 1  # after anchor
            if last_user is not None:
                compacted.insert(insert_at, last_user)
            else:
                compacted.insert(insert_at, {
                    "role": "user",
                    "content": "[Continued from previous context — execute the active plan.]",
                })
            logger.warning(
                "Anchored compaction: re-injected user message "
                "(all were dropped by summarization)",
            )

        logger.info(
            "Anchored compaction: %d msgs summarized → anchor (iter %d), "
            "kept %d recent msgs",
            len(msgs_to_summarize), iteration, len(recent_msgs),
        )
        return sanitize_context(compacted), anchor

    except Exception as exc:
        logger.warning(
            "Anchored compaction failed (%s), using simple fallback", exc,
        )
        return _simple_compact(context, config, force=force), anchor
