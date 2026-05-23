"""Micro-inference — lightweight single-LLM-call responses.

The micro slot runs a single vLLM generate call with compact context
(Working Memory snapshot + orchestration state + recent history).
No tools, no agentic lock.  Designed for:

  - Channel status queries while orchestration is active
  - Quick acknowledgments and proceed confirmations
  - Greetings and casual chat during busy periods

Latency target: <2 seconds including vLLM round-trip.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_MICRO_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. You are currently busy with a task "
    "but responding briefly to a message from another channel.\n\n"
    "RULES:\n"
    "- Be concise (1-3 sentences max)\n"
    "- If asked about status/progress, summarize from the context below\n"
    "- If asked to do something new, acknowledge it and say you'll get to it "
    "after the current work\n"
    "- Be warm and natural, not robotic\n"
    "- Do NOT use tool calls or markdown code blocks\n"
)


def _build_micro_context(
    runtime: Any,
    team_manager: Any | None = None,
    user_input: str = "",
    history: list[dict] | None = None,
    max_context_tokens: int = 1500,
) -> list[dict]:
    """Build a compact message list for micro-inference.

    Gathers:
      1. System prompt with orchestration state
      2. Last 4 turns of history (truncated)
      3. User message
    """
    parts: list[str] = [_MICRO_SYSTEM_PROMPT]

    # Orchestration context from TeamManager
    if team_manager is not None:
        try:
            orch_ctx = team_manager.get_orchestration_context(compact=True)
            if orch_ctx:
                parts.append("CURRENT WORK STATUS:\n" + orch_ctx)
        except Exception as exc:
            logger.debug("micro: orchestration context failed: %s", exc)

    # Working memory summary
    wm = getattr(runtime, "working_memory", None)
    if wm is None:
        dual = getattr(runtime, "dual_wm", None)
        if dual is not None:
            wm = getattr(dual, "active", None)
    if wm is not None:
        try:
            board = wm.get_todo_board()
            if board:
                parts.append("TODO BOARD:\n" + board[:500])
        except Exception:
            pass

    system_content = "\n\n".join(parts)

    msgs: list[dict] = [{"role": "system", "content": system_content}]

    # Recent history (last 4 turns, truncated)
    if history:
        for turn in history[-4:]:
            role = turn.get("role", "user")
            content = turn.get("content") or ""
            if role in ("user", "assistant") and content:
                msgs.append({
                    "role": role,
                    "content": content[:400],
                })

    msgs.append({"role": "user", "content": user_input})
    return msgs


async def micro_respond(
    runtime: Any,
    vllm_client: Any,
    user_input: str,
    *,
    team_manager: Any | None = None,
    history: list[dict] | None = None,
    reply_channel: Any | None = None,
) -> str:
    """Generate a micro-inference response (single LLM call, no tools).

    Parameters
    ----------
    runtime : AgentRuntime
        For accessing working memory and agent identity.
    vllm_client : vLLM HTTP client
        For the generate call.
    user_input : str
        The user's message.
    team_manager : TeamManager or None
        For orchestration context.
    history : list of dicts or None
        Recent conversation history.
    reply_channel : callable or None
        ``async def reply(text) -> None`` to send the response.

    Returns
    -------
    str
        The generated response text.
    """
    t0 = time.perf_counter()

    msgs = _build_micro_context(
        runtime=runtime,
        team_manager=team_manager,
        user_input=user_input,
        history=history,
    )

    try:
        result = await vllm_client.generate(
            adapter_name=None,
            messages=msgs,
            max_tokens=200,
            temperature=0.6,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        text = (
            result.text if hasattr(result, "text") else str(result or "")
        ).strip()

        # Strip any thinking tags that might leak through
        if "<think>" in text:
            import re
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    except Exception as exc:
        logger.warning("micro_respond failed: %s", exc)
        text = "I'm currently busy with a task, but I'll get back to you shortly."

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "micro_respond: %.0fms, %d chars, input=%.80s",
        elapsed_ms, len(text), user_input,
    )

    if reply_channel is not None:
        try:
            await reply_channel(text)
        except Exception as exc:
            logger.debug("micro reply_channel failed: %s", exc)

    # Feed to ANS for memory/learning (lightweight, non-blocking)
    ans = getattr(runtime, "ans", None)
    if ans is not None:
        try:
            _hypo = getattr(runtime, "hypothalamus", None)
            ans.on_response(user_input, text, _hypo, is_agentic=False)
        except Exception:
            pass

    return text
