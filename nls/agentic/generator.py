"""v4 LLM generation module.

Handles streaming generation, context sanitization, thinking mode selection,
and inline tool call recovery.  Uses lazy tool loading (matching v3): only
virtual tools + get_tool_schema are exposed initially.  Real tool schemas
are unlocked on demand when the model calls get_tool_schema("tool_name").
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

from nls.tools.agent_tools.base import AgentTool, tool_to_openai_schema

from .events import AgentEvent, EventType, emit
from .types import (
    GenerationResult,
    LoopConfig,
    LoopState,
    _ASK_USER_TOOL_SCHEMA,
    _COMMUNICATE_TOOL_SCHEMA,
    _DELEGATE_TOOL_SCHEMA,
)

logger = logging.getLogger(__name__)

_TOOLCALL_BLOCK_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)
_INLINE_JSON_TOOLCALL_RE = re.compile(
    r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*'
    r"\{(?:[^{}]|\{[^{}]*\})*\}\s*\}",
    re.DOTALL,
)


# -------------------------------------------------------------------
# Thinking mode
# -------------------------------------------------------------------

def select_thinking(state: LoopState, enable_thinking: bool = True) -> bool:
    """Per-iteration thinking toggle (currently passthrough).

    Returns ``enable_thinking`` directly.  In the future this can
    integrate a micro-inference classifier (like v3's
    ``classify_thinking_need``) to decide per-iteration whether to
    inject ``/no_think`` — e.g. suppress thinking on simple tool
    re-invocations but allow it on complex planning iterations.

    IMPORTANT — how ``thinking`` controls generation:

    - ``True``  → template ``enable_thinking=True`` + NO ``/no_think``
      → model reasons in ``<think>`` blocks, then calls tools (v3 proven)
    - ``False`` → template ``enable_thinking=True`` + ``/no_think`` injected
      → tool-calling structure preserved, reasoning suppressed

    The template flag is **always** ``True`` (required for Qwen3.5
    structured tool calls per KL §21, KL §46, vLLM PR #37414).
    ``/no_think`` is the training-level soft-switch.
    """
    return enable_thinking


# -------------------------------------------------------------------
# Context sanitization (ported from types.py)
# -------------------------------------------------------------------

def sanitize_context(messages: list[dict]) -> list[dict]:
    """Ensure context conforms to vLLM/Qwen3.5 requirements.

    - Consolidates all system messages to the front.
    - Fixes assistant messages with tool_calls (content must be None).
    - Repairs broken JSON in tool_call arguments.
    - Ensures tool messages have non-empty tool_call_id.
    - Adds placeholder tool messages for orphan tool_call_ids.
    """
    first_system_idx = -1
    extra_system_parts: list[str] = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            if first_system_idx < 0:
                first_system_idx = i
            else:
                content = (msg.get("content") or "").strip()
                if content:
                    extra_system_parts.append(content)

    if extra_system_parts:
        base = (messages[first_system_idx].get("content") or "").rstrip()
        messages[first_system_idx] = {
            **messages[first_system_idx],
            "content": base + "\n\n" + "\n\n".join(extra_system_parts),
        }

    result: list[dict] = []
    seen_tool_call_ids: set[str] = set()
    expected_tool_ids: set[str] = set()

    for msg in messages:
        role = msg.get("role")
        if role == "system" and result and result[0].get("role") == "system":
            if msg is not messages[first_system_idx]:
                continue

        # Qwen3.5: "No Thinking Content in History" — strip <think>
        # blocks from ALL assistant messages. Only keep visible text.
        if role == "assistant":
            _existing = (msg.get("content") or "").strip()
            if "<think>" in _existing:
                import re as _re_san
                _existing = _re_san.sub(
                    r"<think>.*?</think>\s*", "", _existing, flags=_re_san.DOTALL,
                ).strip()
            if _existing != (msg.get("content") or "").strip():
                msg = {**msg, "content": _existing or None}

        if role == "assistant" and msg.get("tool_calls"):
            import copy
            msg = {**msg, "tool_calls": copy.deepcopy(msg["tool_calls"])}
            if not msg.get("content"):
                msg["content"] = None
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                args_str = fn.get("arguments", "")
                if args_str:
                    try:
                        json.loads(args_str)
                    except (json.JSONDecodeError, TypeError):
                        fn["arguments"] = "{}"
                tc_id = tc.get("id", "")
                if tc_id:
                    expected_tool_ids.add(tc_id)

        if role == "tool":
            tc_id = msg.get("tool_call_id") or "unknown"
            msg = {**msg, "tool_call_id": tc_id}
            seen_tool_call_ids.add(tc_id)

        result.append(msg)

    # Insert orphan tool placeholders right after the assistant that issued them
    orphan_ids = expected_tool_ids - seen_tool_call_ids
    if orphan_ids:
        insert_idx = len(result)
        for i in range(len(result) - 1, -1, -1):
            if result[i].get("role") == "assistant" and result[i].get("tool_calls"):
                tc_ids_here = {tc.get("id") for tc in result[i]["tool_calls"]}
                if tc_ids_here & orphan_ids:
                    insert_idx = i + 1
                    while insert_idx < len(result) and result[insert_idx].get("role") == "tool":
                        insert_idx += 1
                    break
        for oid in orphan_ids:
            result.insert(insert_idx, {
                "role": "tool",
                "tool_call_id": oid,
                "content": "[No result captured]",
            })
            insert_idx += 1

    return result


# -------------------------------------------------------------------
# Inline tool call recovery
# -------------------------------------------------------------------

def _try_xml_tool_recovery(text: str) -> list[dict]:
    """Recover XML tool calls in various Qwen3 formats:

    - ``<tool_call><function=name><parameter=k>v</parameter></function></tool_call>``
    - ``<function=name><parameter=k>v</parameter></function></tool_call>``  (missing open tag)
    - ``<function=name><parameter=k>v</parameter></function>``              (bare function)
    """
    recovered: list[dict] = []
    for m in re.finditer(
        r"(?:<tool_call>\s*)?<function=(\w+)>(.*?)</function>(?:\s*</tool_call>)?",
        text, re.DOTALL,
    ):
        fn_name = m.group(1)
        params_block = m.group(2)
        args: dict = {}
        for pm in re.finditer(
            r"<parameter=(\w+)>(.*?)</parameter>", params_block, re.DOTALL,
        ):
            args[pm.group(1)] = pm.group(2).strip()
        if fn_name and args:
            recovered.append({"name": fn_name, "arguments": args})
    return recovered


def _try_inline_recovery(text: str) -> list[dict]:
    """Attempt to recover tool calls from text when structured parsing fails."""
    recovered: list[dict] = []

    for m in _TOOLCALL_BLOCK_RE.finditer(text):
        inner = m.group(0).replace("<tool_call>", "").replace("</tool_call>", "").strip()
        try:
            obj = json.loads(inner)
            if isinstance(obj, dict) and "name" in obj:
                recovered.append(obj)
        except json.JSONDecodeError:
            pass

    for m in _INLINE_JSON_TOOLCALL_RE.finditer(text):
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "name" in obj:
                if not any(
                    r.get("name") == obj["name"]
                    and r.get("arguments") == obj.get("arguments")
                    for r in recovered
                ):
                    recovered.append(obj)
        except json.JSONDecodeError:
            pass

    return recovered


def _normalize_recovered_calls(raw: list[dict]) -> list[dict]:
    """Normalize recovered inline tool calls to OpenAI-compatible format."""
    import uuid
    result = []
    for obj in raw:
        name = obj.get("name", "")
        args = obj.get("arguments", {})
        if isinstance(args, dict):
            args = json.dumps(args)
        result.append({
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {"name": name, "arguments": args},
        })
    return result


def _strip_thinking(text: str) -> tuple[str, str]:
    """Separate thinking blocks from visible text."""
    thinking = ""
    clean = text
    think_start = text.find("<think>")
    if think_start != -1:
        think_end = text.find("</think>")
        if think_end != -1:
            thinking = text[think_start + 7 : think_end].strip()
            clean = text[:think_start] + text[think_end + 8:]
        else:
            thinking = text[think_start + 7 :].strip()
            clean = text[:think_start]
    return clean.strip(), thinking


# -------------------------------------------------------------------
# Main generation function
# -------------------------------------------------------------------

async def generate(
    context: list[dict],
    tools: dict[str, Any],
    config: LoopConfig,
    vllm_client: Any,
    *,
    thinking: bool = False,
    adapter_name: str | None = None,
    on_event: Callable[[AgentEvent], Any] | None = None,
    abort_signal: asyncio.Event | None = None,
    iteration: int = 0,
    base_schemas: list[dict] | None = None,
    unlocked_tools: set[str] | None = None,
    prefill_msg: dict | None = None,
    loop_id: str = "",
) -> GenerationResult:
    """Stream a single LLM generation turn with lazy tool loading.

    V5 additions:
    - ``prefill_msg``: optional assistant message appended to context for
      reasoning continuation (v3 pattern). When provided, generation uses
      ``continue_final_message=True`` so the model continues from where
      it left off rather than starting fresh.

    Only base_schemas (virtual tools + get_tool_schema) are passed to vLLM
    initially.  As tools are unlocked via get_tool_schema, their schemas
    are added to the set.  This matches v3's proven approach (M-016) and
    keeps the Qwen3.5 template's tool section small (~4 tools vs ~15+).
    Inline recovery (_try_inline_recovery) is kept as fallback.
    """
    logger.info(
        "[GEN] iter=%d thinking=%s ctx_msgs=%d base_schemas=%d "
        "unlocked=%s adapter=%s",
        iteration, thinking, len(context),
        len(base_schemas or []),
        list(unlocked_tools or set()),
        adapter_name,
    )

    safe_ctx = sanitize_context(list(context))

    # Lazy tool loading: base schemas + unlocked tool schemas only
    tool_schemas: list[dict] = list(base_schemas or [])
    _base_names = {
        s.get("function", {}).get("name") for s in tool_schemas
    }
    for name in (unlocked_tools or set()):
        if name in _base_names:
            continue
        t = tools.get(name)
        if t and isinstance(t, AgentTool):
            try:
                tool_schemas.append(tool_to_openai_schema(t))
            except Exception:
                pass

    # If vLLM gets tool_choice=auto but an empty tool list, Qwen often emits
    # pseudo tool calls as XML inside thinking text — attach real schemas
    # whenever the loop registered AgentTools but none were passed above.
    if not tool_schemas and tools:
        for _tobj in tools.values():
            if isinstance(_tobj, AgentTool):
                try:
                    tool_schemas.append(tool_to_openai_schema(_tobj))
                except Exception:
                    pass

    # Hard guard: if tools were registered but we still have no schemas,
    # something went wrong in schema serialization.  Log loudly — this is
    # exactly the condition that causes the model to fall back to XML.
    if not tool_schemas and tools:
        logger.error(
            "[GEN] iter=%d: tool_schemas is EMPTY despite %d registered tools — "
            "model will likely fall back to XML tool calling.  "
            "Check tool_to_openai_schema for each tool.",
            iteration, len(tools),
        )
    elif tool_schemas:
        logger.debug("[GEN] iter=%d: sending %d tool schemas to vLLM", iteration, len(tool_schemas))

    extra_body: dict[str, Any] = {
        "repetition_penalty": config.repetition_penalty,
        "top_k": config.top_k,
        "min_p": config.min_p,
        "presence_penalty": config.presence_penalty,
    }
    if config.vllm_xargs:
        extra_body["vllm_xargs"] = config.vllm_xargs

    # Template enable_thinking is ALWAYS True — Qwen3.5 requires it
    # for structured tool calls (KL §21, §46, vLLM PR #37414).
    # The /no_think soft-switch (below) controls actual reasoning output.
    extra_body["chat_template_kwargs"] = {"enable_thinking": True}

    # --- DIAGNOSTIC DUMP: capture what we send to vLLM ---
    try:
        import os, tempfile
        _dump_dir = os.path.join(
            tempfile.gettempdir(), "nls_agentic_diag", loop_id or "unknown",
        )
        os.makedirs(_dump_dir, exist_ok=True)
        _dump_path = os.path.join(_dump_dir, f"iter_{iteration}.json")
        _dump = {
            "iteration": iteration,
            "thinking": thinking,
            "n_messages": len(safe_ctx),
            "messages_summary": [
                {
                    "role": m.get("role"),
                    "content_len": len(m.get("content") or ""),
                    "content_preview": (m.get("content") or "")[:300],
                    "has_tool_calls": bool(m.get("tool_calls")),
                }
                for m in safe_ctx
            ],
            "n_tool_schemas": len(tool_schemas),
            "tool_schema_names": [
                s.get("function", {}).get("name", "?") for s in tool_schemas
            ],
            "extra_body_keys": list(extra_body.keys()),
            "vllm_xargs_keys": list((extra_body.get("vllm_xargs") or {}).keys()),
            "temperature": config.temperature,
            "top_p": config.top_p,
            "top_k": config.top_k,
            "min_p": config.min_p,
            "presence_penalty": config.presence_penalty,
            "repetition_penalty": config.repetition_penalty,
            "max_new_tokens": config.max_new_tokens,
            "has_prefill": prefill_msg is not None,
            "prefill_len": len((prefill_msg or {}).get("content", "")),
        }
        with open(_dump_path, "w") as _f:
            json.dump(_dump, _f, indent=2, default=str)
        logger.info("DIAG DUMP: %s", _dump_path)
    except Exception:
        pass

    # Match v3 logic: only inject /no_think when thinking is disabled.
    # When thinking=True (agentic default), the model reasons in <think>
    # blocks — this is critical for planning and tool selection.
    # When thinking=False (e.g. simple chat or classifier says NOTHINK),
    # /no_think suppresses reasoning while preserving tool structure.
    if not thinking:
        for _i in range(len(safe_ctx) - 1, -1, -1):
            if safe_ctx[_i].get("role") == "user":
                _uc = safe_ctx[_i].get("content") or ""
                if not _uc.startswith("/no_think"):
                    safe_ctx[_i] = {
                        **safe_ctx[_i],
                        "content": f"/no_think\n{_uc}",
                    }
                break

    # V5 reasoning continuation: append prefill assistant message so
    # the model continues thinking from where it left off (v3 pattern).
    _is_continuation = False
    if prefill_msg is not None and thinking:
        safe_ctx.append(prefill_msg)
        extra_body["continue_final_message"] = True
        extra_body["add_generation_prompt"] = False
        _is_continuation = True
        logger.info(
            "[GEN] iter=%d reasoning continuation (prefill len=%d)",
            iteration, len(prefill_msg.get("content", "")),
        )

    acc_tokens: list[str] = []
    in_think_block = False
    think_buf = ""
    structured_calls = None
    visible_chars = 0
    _MAX_VISIBLE_CHARS = 3000

    try:
        async for chunk in vllm_client.generate_stream(
            adapter_name=adapter_name,
            messages=safe_ctx,
            max_tokens=config.max_new_tokens,
            temperature=config.temperature,
            top_p=config.top_p,
            tools=tool_schemas or None,
            tool_choice="auto" if tool_schemas else None,
            yield_tool_deltas=True,
            extra_body=extra_body,
        ):
            if abort_signal and abort_signal.is_set():
                break

            if isinstance(chunk, str):
                acc_tokens.append(chunk)
                think_buf += chunk
                if not in_think_block:
                    visible_chars += len(chunk)
                    if "<think>" in think_buf:
                        in_think_block = True
                        pre = think_buf.split("<think>", 1)[0]
                        if pre.strip():
                            await emit(on_event, AgentEvent(
                                EventType.TOKEN,
                                {"token": pre, "iteration": iteration},
                            ))
                        think_buf = ""
                    elif not any(
                        "<think>"[:i] == think_buf[-i:]
                        for i in range(1, min(len(think_buf), 7) + 1)
                    ):
                        if len(think_buf) > 50 or "\n" in think_buf:
                            await emit(on_event, AgentEvent(
                                EventType.TOKEN,
                                {"token": think_buf, "iteration": iteration},
                            ))
                            think_buf = ""
                    if visible_chars > _MAX_VISIBLE_CHARS:
                        logger.warning(
                            "Visible text exceeded %d chars — "
                            "likely spiral, stopping generation",
                            _MAX_VISIBLE_CHARS,
                        )
                        break
                else:
                    if "</think>" in think_buf:
                        in_think_block = False
                        _think_content = think_buf.split("</think>", 1)[0]
                        post = think_buf.split("</think>", 1)[1]
                        if _think_content.strip():
                            await emit(on_event, AgentEvent(
                                EventType.TOKEN,
                                {"token": _think_content, "iteration": iteration, "thinking": True},
                            ))
                        think_buf = ""
                        if post.strip():
                            await emit(on_event, AgentEvent(
                                EventType.TOKEN,
                                {"token": post, "iteration": iteration},
                            ))
                    else:
                        if len(think_buf) > 80:
                            await emit(on_event, AgentEvent(
                                EventType.TOKEN,
                                {"token": think_buf, "iteration": iteration, "thinking": True},
                            ))
                            think_buf = ""

            elif isinstance(chunk, dict):
                if chunk.get("type") == "tool_delta":
                    await emit(on_event, AgentEvent(
                        EventType.TOOL_DELTA,
                        {
                            "index": chunk.get("index", 0),
                            "function_name": chunk.get("function_name", ""),
                            "arguments_delta": chunk.get("arguments_delta", ""),
                            "iteration": iteration,
                        },
                    ))

            elif hasattr(chunk, "text") and chunk.text:
                acc_tokens.append(chunk.text)
                await emit(on_event, AgentEvent(
                    EventType.TOKEN,
                    {"token": chunk.text, "iteration": iteration},
                ))

            if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                structured_calls = chunk.tool_calls

        if think_buf and not in_think_block:
            await emit(on_event, AgentEvent(
                EventType.TOKEN,
                {"token": think_buf, "iteration": iteration},
            ))

        structured_calls = getattr(
            vllm_client, "last_stream_tool_calls", None
        ) or structured_calls

        response_text = "".join(acc_tokens)
        clean_text, thinking_text = _strip_thinking(response_text)

        if thinking_text:
            await emit(on_event, AgentEvent(
                EventType.THINKING,
                {"content": thinking_text, "iteration": iteration},
            ))

        tool_calls_list: list[dict] = []
        if structured_calls:
            tool_calls_list = structured_calls
        elif clean_text:
            recovered = _try_inline_recovery(clean_text)
            if recovered:
                tool_calls_list = _normalize_recovered_calls(recovered)
                clean_text = _TOOLCALL_BLOCK_RE.sub("", clean_text)
                clean_text = _INLINE_JSON_TOOLCALL_RE.sub("", clean_text)
                clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()
        elif thinking_text and not structured_calls:
            # Qwen3 sometimes embeds the entire tool call inside <think> blocks
            # (raw response is literally "<think><tool_call>...</tool_call></think>").
            # Since _strip_thinking moved it to thinking_text and clean_text is
            # empty, the normal clean_text path misses it.  Recover from thinking.
            recovered = _try_xml_tool_recovery(thinking_text)
            if not recovered:
                recovered = _try_inline_recovery(thinking_text)
            if recovered:
                tool_calls_list = _normalize_recovered_calls(recovered)
                logger.warning(
                    "[GEN] iter=%d: recovered %d tool call(s) from <think> block "
                    "(model put tool call inside thinking — check context health)",
                    iteration, len(tool_calls_list),
                )

        # Qwen3.5 best practice: "No Thinking Content in History —
        # historical model output should only include the final output
        # part and does not need to include the thinking content."
        # Thinking is used for reasoning continuation via _reasoning_trajectory
        # but must NOT go into the context as historical assistant content.
        message: dict[str, Any] = {
            "role": "assistant",
            "content": clean_text or None,
        }
        if tool_calls_list:
            message["tool_calls"] = tool_calls_list

        # --- DIAGNOSTIC DUMP: capture response ---
        try:
            import os, tempfile
            _dump_dir = os.path.join(
                tempfile.gettempdir(), "nls_agentic_diag", loop_id or "unknown",
            )
            _resp_path = os.path.join(_dump_dir, f"iter_{iteration}_resp.json")
            _resp_dump = {
                "iteration": iteration,
                "has_tool_calls": bool(tool_calls_list),
                "n_tool_calls": len(tool_calls_list),
                "tool_names": [
                    tc.get("function", {}).get("name", "?")
                    for tc in tool_calls_list
                ],
                "text_len": len(clean_text),
                "thinking_len": len(thinking_text),
                "raw_text_len": len(response_text),
                "text_preview": clean_text[:500],
                "thinking_preview": thinking_text[:500],
                "raw_preview": response_text[:500],
                "structured_calls_source": (
                    "vllm_client" if (
                        getattr(vllm_client, "last_stream_tool_calls", None)
                        and structured_calls == getattr(vllm_client, "last_stream_tool_calls", None)
                    ) else "inline_recovery" if tool_calls_list and not structured_calls else "streaming"
                ),
                "finish_reason": getattr(vllm_client, "last_stream_finish_reason", "unknown"),
            }
            with open(_resp_path, "w") as _f:
                json.dump(_resp_dump, _f, indent=2, default=str)
            logger.info("DIAG RESP: %s | tools=%d text=%d",
                        _resp_path, len(tool_calls_list), len(clean_text))
        except Exception:
            pass

        _usage = getattr(vllm_client, "last_stream_usage", {}) or {}

        return GenerationResult(
            text=clean_text,
            tool_calls=tool_calls_list,
            thinking=thinking_text,
            message=message,
            raw_text=response_text,
            prompt_tokens=_usage.get("prompt_tokens", 0),
            completion_tokens=_usage.get("completion_tokens", 0),
            total_tokens=_usage.get("total_tokens", 0),
        )

    except Exception as exc:
        exc_msg = str(exc)
        exc_type = type(exc).__name__
        err_str = f"{exc_type}: {exc_msg}" if exc_msg else f"{exc_type}: (no message)"
        logger.warning("Generation error: %s", err_str[:200], exc_info=True)

        partial_text = "".join(acc_tokens)
        if partial_text.strip():
            tombstone_msg: dict[str, Any] = {
                "role": "assistant",
                "content": partial_text,
                "_tombstoned": True,
                "_tombstone_reason": err_str,
            }
            logger.info(
                "[GEN] tombstoned partial message (%d chars) — %s",
                len(partial_text), err_str[:100],
            )
            return GenerationResult(
                error=err_str,
                message=tombstone_msg,
                raw_text=partial_text,
            )

        return GenerationResult(error=err_str)


def is_context_overflow(error: str) -> bool:
    """Check if an error indicates context length exceeded."""
    lower = error.lower()
    return any(
        t in lower
        for t in (
            "context length", "maximum context", "too long", "token limit",
            "input_tokens", "reduce the length", "requested output tokens",
        )
    )


def is_transient(error: str) -> bool:
    """Check if an error is likely transient and retryable."""
    lower = error.lower()
    return any(
        t in lower
        for t in (
            "timeout",
            "timed out",
            "read timed out",
            "readerror",
            "read error",
            "remotedisconnected",
            "remoteprotocolerror",
            "incompleteread",
            "connection",
            "connecterror",
            "event loop",
            "temporarily",
            "503",
            "502",
            "429",
            "rate limit",
            "too many requests",
            "524",
            "cloudflare",
            "engine not ready",
            "not ready",
            "overloaded",
            "try again",
            "getaddrinfo",
            "name resolution",
            "dns",
            "eof",
            "broken pipe",
            "reset by peer",
            "aborted",
            "client has been closed",
        )
    )


def sanitize_generation_error_for_user(msg: str, max_len: int = 480) -> str:
    """Truncate and lightly redact backend errors for safe UI display."""
    if not msg:
        return ""
    s = " ".join(str(msg).split())
    s = re.sub(r"ghp_[A-Za-z0-9]{20,}", "[token_redacted]", s)
    s = re.sub(r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "[token_redacted]", s)
    s = re.sub(r"\bsk-[A-Za-z0-9]{20,}", "[token_redacted]", s)
    s = re.sub(
        r"\bAIza[0-9A-Za-z_-]{20,}", "[token_redacted]", s,
    )
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s
