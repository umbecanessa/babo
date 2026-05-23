"""WebSocket Chat endpoint — single-path agent streaming.

Provides real-time bidirectional chat via WebSocket at
``/ws/chat/{agent_id}``.

Protocol::

    Client -> Server:
        {"type": "message", "content": "Hello!"}
        {"type": "command", "command": "abort"}     # cancel agentic loop

    Server -> Client (single-turn mode):
        {"type": "token", "content": "Hi"}          # streaming token
        {"type": "response_end", "response": "Hi there!", "nls": {...}}

    Server -> Client (agentic mode):
        {"type": "agentic_start", "max_steps": 15}
        {"type": "agentic_iteration", "step": 1, ...}
        {"type": "agentic_complete", "total_steps": 3, "final_response": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from nls.tools.tool_call_normalizer import has_tool_calls

from .agentic import _run_agentic_with_receive
from .commands import _handle_command
from .helpers import (
    _CHAT_TOOLCALL_NUDGE,
    _INLINE_JSON_TOOLCALL_RE,
    _TOOLCALL_BLOCK_RE,
    _augment_with_attachments,
    _build_nls_metadata,
    _dedup_signal_tags,
)
from .history import (
    _salvage_agentic_context,
    _save_agentic_history_v2,
    _strip_internal_blocks,
)

logger = logging.getLogger(__name__)


async def websocket_chat(websocket: WebSocket, agent_id: str):
    """WebSocket chat endpoint for real-time conversation."""
    await websocket.accept()

    app = websocket.app
    agent_manager = app.state.agent_manager
    model_manager = app.state.model_manager
    connection_manager = app.state.connection_manager
    consciousness_scheduler = getattr(
        app.state, "consciousness_scheduler", None,
    )

    connection_manager.register(agent_id, websocket)

    agentic_abort = asyncio.Event()
    websocket.state.agentic_abort = agentic_abort
    websocket.state.agentic_running = False

    copilot_queue: asyncio.Queue = asyncio.Queue()
    websocket.state.copilot_queue = copilot_queue

    # Ensure agent is loaded
    try:
        if agent_id not in agent_manager._runtimes:
            await agent_manager.load_agent(agent_id)
    except FileNotFoundError:
        connection_manager.unregister(agent_id, websocket)
        await websocket.send_json({
            "type": "error",
            "message": f"Agent '{agent_id}' not found",
        })
        await websocket.close(code=4004)
        return
    except Exception as load_exc:
        logger.error("Failed to load agent %s: %s", agent_id, load_exc, exc_info=True)
        connection_manager.unregister(agent_id, websocket)
        await websocket.send_json({
            "type": "error",
            "message": f"Failed to load agent: {load_exc}",
        })
        await websocket.close(code=4004)
        return

    runtime = agent_manager.get_runtime(agent_id)
    if runtime is None:
        connection_manager.unregister(agent_id, websocket)
        await websocket.send_json({
            "type": "error",
            "message": "Agent runtime not available",
        })
        await websocket.close(code=4004)
        return

    history = runtime.load_conversation_history()

    fact_context = runtime.build_fact_memory_context()
    if fact_context:
        history.insert(0, {"role": "system", "content": fact_context})

    try:
        # Send initial status
        status = runtime.get_status()
        await websocket.send_json({
            "type": "status",
            "agent_status": "alive",
            "agent_name": status.get("name") or None,
            "facts_in_memory": status.get("facts_in_memory", 0),
            "turn_count": status.get("turn_count", 0),
            "sleep_count": status.get("sleep_count", 0),
            "hormones": status.get("hormones", {}),
            "ans": status.get("ans", {}),
            "heartbeat": status.get("heartbeat", {}),
            "working_memory": status.get("working_memory", {}),
            "narrative": status.get("narrative", {}),
            "theory_of_mind": status.get("theory_of_mind", {}),
            "predictive_processing": status.get("predictive_processing", {}),
            "network_dynamics": status.get("network_dynamics", {}),
        })

        chat_history = [
            m for m in history if m.get("role") in ("user", "assistant")
        ]
        if chat_history:
            await websocket.send_json({
                "type": "history",
                "messages": chat_history,
            })

        logger.info("WebSocket connected: agent %s", agent_id)

        # Check for recently-approved skill reviews
        try:
            _data_dir = getattr(
                websocket.app.state, "settings", None,
            )
            if _data_dir:
                _reviews_path = _data_dir.data_dir / "skill_reviews"
                if _reviews_path.is_dir():
                    _recent_skills: list[str] = []
                    _now = time.time()
                    for _rf in sorted(_reviews_path.glob("*.json"), reverse=True):
                        try:
                            _rev = json.loads(_rf.read_text(encoding="utf-8"))
                            if (
                                _rev.get("status") == "approved"
                                and _now - _rev.get("approved_at", 0) < 120
                            ):
                                for _sk in _rev.get("skills", []):
                                    _recent_skills.append(_sk.get("name", "?"))
                        except Exception:
                            pass
                    if _recent_skills:
                        _skill_list = ", ".join(_recent_skills)
                        await websocket.send_json({
                            "type": "status",
                            "content": (
                                f"Server restarted successfully. "
                                f"Skill(s) loaded: {_skill_list}. "
                                f"You can continue chatting."
                            ),
                        })
        except Exception:
            pass

        runtime._active_sessions += 1

        # ─── Birth greeting (first-ever connection) ───
        _birth_flag = Path(runtime.agent_dir) / ".birth_greeted"
        _is_first_ever = not chat_history and not _birth_flag.exists()

        if _is_first_ever:
            wake_prompt = runtime.get_wake_prompt()
            if wake_prompt:
                logger.info("Agent %s: generating first message", agent_id)

                _WAKE_MAX_RETRIES = 3
                _WAKE_RETRY_DELAY = 5.0

                for _wake_attempt in range(1, _WAKE_MAX_RETRIES + 1):
                  try:
                    t0_wake = time.perf_counter()

                    wake_response = ""
                    _wake_signal_buf = ""
                    _wake_in_signal = False
                    _WAKE_SIG_STARTS = ("```tool_call", "<tool_call>")

                    async for token in runtime.process_message_stream_async(
                        wake_prompt, history=history,
                        force_thinking=False,
                    ):
                        if isinstance(token, tuple):
                            _kind, _text = token
                            if _kind == "thinking":
                                await websocket.send_json({
                                    "type": "reasoning_token",
                                    "content": _text,
                                })
                            elif _kind == "thinking_end":
                                await websocket.send_json({
                                    "type": "reasoning_end",
                                })
                            continue

                        wake_response += token
                        if _wake_in_signal:
                            _wake_signal_buf += token
                            continue
                        _wake_signal_buf += token
                        for _marker in _WAKE_SIG_STARTS:
                            if _marker in _wake_signal_buf:
                                _pre = _wake_signal_buf[:_wake_signal_buf.index(_marker)]
                                if _pre:
                                    await websocket.send_json({
                                        "type": "token",
                                        "content": _pre,
                                    })
                                _wake_in_signal = True
                                _wake_signal_buf = _wake_signal_buf[_wake_signal_buf.index(_marker):]
                                break
                        else:
                            _safe = max(0, len(_wake_signal_buf) - 20)
                            if _safe > 0:
                                await websocket.send_json({
                                    "type": "token",
                                    "content": _wake_signal_buf[:_safe],
                                })
                                _wake_signal_buf = _wake_signal_buf[_safe:]
                    if not _wake_in_signal and _wake_signal_buf:
                        await websocket.send_json({
                            "type": "token",
                            "content": _wake_signal_buf,
                        })

                    _turn_wake_res = runtime.last_stream_turn_result
                    final_wake = _turn_wake_res.response if _turn_wake_res else wake_response
                    # Strip code artifacts and orphan think tags from the greeting
                    final_wake = re.sub(r"</?tool_code>", "", final_wake).strip()
                    final_wake = final_wake.replace("</think>", "").replace("<think>", "").strip()
                    _wake_reasoning = getattr(runtime, "_last_stream_thinking", "") or ""
                    _wake_signals = [
                        {"type": getattr(s, "signal_type", ""),
                         "domain": getattr(s, "domain_path", ""),
                         "content": (getattr(s, "content", "") or "")[:200]}
                        for s in (_turn_wake_res.signals if _turn_wake_res else [])
                    ]
                    _wake_meta = _turn_wake_res.meta_weight if _turn_wake_res else 0.0

                    wake_latency = (time.perf_counter() - t0_wake) * 1000
                    wake_status = runtime.get_status()
                    await websocket.send_json({
                        "type": "response_end",
                        "response": final_wake,
                        "reasoning": _wake_reasoning,
                        "latency_ms": round(wake_latency, 1),
                        "nls": _build_nls_metadata(
                            wake_status,
                            signals=_wake_signals,
                            meta_weight=_wake_meta,
                        ),
                    })

                    _wake_asst: dict = {
                        "role": "assistant",
                        "content": final_wake,
                    }
                    if _wake_reasoning:
                        _wake_asst["reasoning"] = _wake_reasoning
                    history.append(_wake_asst)
                    runtime.save_conversation_history(history)

                    try:
                        _birth_flag.write_text(
                            f"greeted_at={time.time()}\n",
                            encoding="utf-8",
                        )
                    except OSError:
                        pass

                    logger.info(
                        "Agent %s: first message sent (%.0fms)",
                        agent_id, wake_latency,
                    )
                    break
                  except WebSocketDisconnect:
                    raise
                  except Exception as exc:
                    if _wake_attempt < _WAKE_MAX_RETRIES:
                        logger.warning(
                            "Agent %s: first message attempt %d/%d failed "
                            "(retrying in %.0fs): %s",
                            agent_id, _wake_attempt, _WAKE_MAX_RETRIES,
                            _WAKE_RETRY_DELAY, exc,
                        )
                        await asyncio.sleep(_WAKE_RETRY_DELAY)
                    else:
                        logger.error(
                            "Agent %s: first message failed after %d attempts: %s",
                            agent_id, _WAKE_MAX_RETRIES, exc, exc_info=True,
                        )

        # ─── Main message loop ───
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "message", "content": raw}

            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg.get("type") == "command":
                command = msg.get("command", "")
                await _handle_command(
                    command, websocket, runtime, agent_id, app,
                    history, data=msg,
                )
                continue

            _ch_type = msg.get("channel_type", "")
            if _ch_type:
                runtime._channel_type = _ch_type

            user_input = msg.get("content", "").strip()
            if not user_input:
                continue

            # Session / thread routing
            session_key = msg.get("session_key", "websocket:main")
            prev_sk = getattr(websocket.state, "session_key", "websocket:main")
            if session_key != prev_sk:
                if session_key == "websocket:main":
                    history = runtime.load_conversation_history()
                    fact_ctx = runtime.build_fact_memory_context()
                    if fact_ctx:
                        history.insert(0, {"role": "system", "content": fact_ctx})
                else:
                    history = runtime.load_session_history(
                        session_key=session_key, max_turns=40,
                    )
            websocket.state.session_key = session_key

            # File attachments
            attachments = msg.get("attachments") or []
            if attachments:
                _ws = (
                    str(runtime.agent_dir / "workspace")
                    if hasattr(runtime, "agent_dir") else ""
                )
                user_input = _augment_with_attachments(
                    user_input, attachments, workspace_dir=_ws,
                )

            # Skill onboarding context injection
            _skill_setup = msg.get("skill_setup", "")
            if _skill_setup:
                _skill_loader = getattr(app.state, "skill_loader", None)
                if _skill_loader is not None:
                    _sk = _skill_loader.skills.get(_skill_setup)
                    _onboard = (
                        _sk.meta.onboarding
                        if _sk and _sk.meta and hasattr(_sk.meta, "onboarding")
                        else None
                    )
                    if _onboard and _onboard.setup_prompt:
                        user_input = (
                            f"{user_input}\n\n"
                            f"[SKILL ONBOARDING \u2014 {_skill_setup}]\n"
                            f"{_onboard.setup_prompt}\n"
                            f"[/SKILL ONBOARDING]"
                        )
                        logger.info(
                            "Agent %s: injected skill onboarding for '%s' "
                            "(%d chars setup_prompt)",
                            agent_id, _skill_setup,
                            len(_onboard.setup_prompt),
                        )

                    if _skill_setup not in (runtime.get_enabled_skills() or []):
                        try:
                            runtime.enable_skill(_skill_setup)
                            logger.info(
                                "Agent %s: auto-enabled skill '%s' "
                                "for onboarding",
                                agent_id, _skill_setup,
                            )
                        except Exception as _esk_exc:
                            logger.warning(
                                "Agent %s: failed to auto-enable "
                                "skill '%s': %s",
                                agent_id, _skill_setup, _esk_exc,
                            )

                    if _onboard and _onboard.setup_prompt:
                        _rt_tools = getattr(runtime, "_agent_tools", None) or []
                        for _t in _rt_tools:
                            if (getattr(_t, "name", "") == "plan"
                                    and hasattr(_t, "_onboarding_context")):
                                _t._onboarding_context = _onboard.setup_prompt
                                break

            logger.info(
                "Agent %s: received message (%d chars): %.80s",
                agent_id, len(user_input), user_input,
            )
            t0 = time.perf_counter()

            if consciousness_scheduler is not None:
                await consciousness_scheduler.on_user_message(agent_id)

            # Wait for agent to be fully ready
            _ans_sleeping = (
                runtime.ans is not None and runtime.ans.is_sleeping
            )
            _cs_not_ready = (
                consciousness_scheduler is not None
                and not consciousness_scheduler.is_agent_ready(agent_id)
            )

            if _ans_sleeping or _cs_not_ready:
                _dequeued = (
                    consciousness_scheduler is not None
                    and consciousness_scheduler.is_agent_ready(agent_id)
                )
                if _dequeued:
                    logger.info(
                        "Agent %s: dequeued from sleep queue, "
                        "processing immediately", agent_id,
                    )
                else:
                    _sleep_msg = "Agent is sleeping, message queued"
                    _wake_time_str = None
                    _is_training = False

                    sleep_scheduler = getattr(
                        app.state, "sleep_scheduler", None,
                    )
                    if sleep_scheduler is not None:
                        _is_training = sleep_scheduler.is_training(
                            agent_id,
                        )

                    if _ans_sleeping and runtime.ans is not None:
                        circ = getattr(runtime.ans, "circadian", None)
                        if circ and circ.enabled:
                            try:
                                wt = circ.next_wake_time()
                                _wake_time_str = wt.strftime("%H:%M %Z")
                            except Exception:
                                pass

                            if _is_training:
                                if circ.is_bedtime():
                                    _sleep_msg = (
                                        f"Agent is sleeping (bedtime). "
                                        f"Finishing current cycle... "
                                        f"Wake time: {_wake_time_str}"
                                    )
                                else:
                                    _sleep_msg = (
                                        "Agent is finishing a nap "
                                        "(\u223c1-2 min), your message "
                                        "is queued"
                                    )
                            else:
                                _sleep_msg = (
                                    "Agent is waking up (loading "
                                    "adapters), almost ready..."
                                )

                    await websocket.send_json({
                        "type": "status",
                        "agent_status": "sleeping" if _is_training else "waking_up",
                        "sleep_reason": _sleep_msg,
                        **({"wake_time": _wake_time_str}
                           if _wake_time_str else {}),
                    })
                    logger.info(
                        "Agent %s: message arrived while sleeping "
                        "(training=%s), waiting...",
                        agent_id, _is_training,
                    )
                    for _wait in range(150):
                        await asyncio.sleep(2.0)
                        _still_sleeping = (
                            runtime.ans is not None
                            and runtime.ans.is_sleeping
                        )
                        _still_not_ready = (
                            consciousness_scheduler is not None
                            and not consciousness_scheduler.is_agent_ready(
                                agent_id,
                            )
                        )
                        if not _still_sleeping and not _still_not_ready:
                            logger.info(
                                "Agent %s: now ready, processing "
                                "queued message", agent_id,
                            )
                            break
                    else:
                        logger.warning(
                            "Agent %s: still not ready after 300s, "
                            "proceeding anyway", agent_id,
                        )

            # ═══════════════════════════════════════════════════
            # GENERATE-FIRST-THEN-DECIDE PATH (non-agentic streaming)
            # ═══════════════════════════════════════════════════
            agentic_enabled = runtime.is_agentic_enabled()
            first_response_has_tools = False
            # Thinking must stay True for agentic tool calling.
            # The RLHF safety spiral was caused by _AGENTIC_SYSTEM_SUPPLEMENT
            # (now removed), not by thinking itself.  With the supplement
            # gone and v3's preamble in place, thinking=True produced
            # the first successful tool call.  thinking=False + /no_think
            # removes the model's planning ability and it reverts to chatbot.
            needs_thinking = True

            if agentic_enabled:
                full_response = ""
                _chat_no_tools = False

                logger.info(
                    "Agent %s: [AGENTIC] enabled — extracting goals "
                    "from user_input (len=%d)",
                    agent_id, len(user_input),
                )

                # Pre-extract task goals and hints
                _pre_goals: list[str] = []
                _pre_hints: list[str] = []
                try:
                    _pre_goals, _pre_hints = await runtime.extract_task_goals(
                        user_input,
                    )
                    logger.info(
                        "Agent %s: [AGENTIC] goals=%s hints=%s",
                        agent_id, _pre_goals, _pre_hints,
                    )
                except Exception:
                    logger.warning(
                        "Agent %s: pre-goal extraction failed",
                        agent_id, exc_info=True,
                    )

                _gen_input = user_input
                if _pre_goals:
                    _goals_block = "\n".join(
                        f"  {i+1}. {g}" for i, g in enumerate(_pre_goals)
                    )
                    _gen_input = (
                        f"{user_input}\n\n"
                        f"[Task goals identified \u2014 use tools to complete "
                        f"each one]:\n{_goals_block}"
                    )

                # Async streaming — always run once so (a) the model can
                # answer from context without entering agentic when pre-goals
                # were informational, and (b) last_stream_tool_calls matches
                # this message before any agentic handoff.

                _signal_buf = ""
                _in_signal = False
                _initial_thinking = ""
                _SIGNAL_STARTS = ("```tool_call", "<tool_call>")
                try:
                    async for token in runtime.process_message_stream_async(
                        _gen_input, history=history,
                    ):
                        if isinstance(token, tuple):
                            _kind, _text = token
                            if _kind == "thinking":
                                await websocket.send_json({
                                    "type": "reasoning_token",
                                    "content": _text,
                                })
                            elif _kind == "thinking_end":
                                await websocket.send_json({
                                    "type": "reasoning_end",
                                })
                            continue

                        full_response += token
                        if _in_signal:
                            _signal_buf += token
                            continue
                        _signal_buf += token
                        for _marker in _SIGNAL_STARTS:
                            if _marker in _signal_buf:
                                _pre = _signal_buf[:_signal_buf.index(_marker)]
                                if _pre:
                                    await websocket.send_json({
                                        "type": "token",
                                        "content": _pre,
                                    })
                                _in_signal = True
                                _signal_buf = _signal_buf[_signal_buf.index(_marker):]
                                break
                        else:
                            _safe = max(0, len(_signal_buf) - 20)
                            if _safe > 0:
                                await websocket.send_json({
                                    "type": "token",
                                    "content": _signal_buf[:_safe],
                                })
                                _signal_buf = _signal_buf[_safe:]
                    if not _in_signal and _signal_buf:
                        await websocket.send_json({
                            "type": "token",
                            "content": _signal_buf,
                        })
                except Exception as gen_exc:
                    if isinstance(gen_exc, WebSocketDisconnect):
                        raise
                    logger.error(
                        "Agent %s: stream failed: %s",
                        agent_id, gen_exc,
                    )
                    try:
                        await websocket.send_json({
                            "type": "response_end",
                            "response": (
                                "I'm having trouble generating a "
                                "response right now. Please try again."
                            ),
                            "reasoning": "",
                            "latency_ms": round(
                                (time.perf_counter() - t0) * 1000, 1,
                            ),
                            "nls": {},
                        })
                    except Exception:
                        pass
                    continue

                # Post-process: strip tool-call artifacts
                _raw_had_toolcall = bool(
                    _TOOLCALL_BLOCK_RE.search(full_response)
                    or _INLINE_JSON_TOOLCALL_RE.search(full_response)
                )

                _tmatch = re.search(
                    r"<think>([\s\S]*?)</think>", full_response,
                )
                if _tmatch:
                    _initial_thinking = _tmatch.group(1).strip()
                elif "<think>" in full_response:
                    _ti = full_response.index("<think>") + len("<think>")
                    _initial_thinking = (
                        full_response[_ti:]
                        .replace("</think>", "")
                        .strip()
                    )

                _visible = re.sub(
                    r"<think>.*?</think>", "", full_response,
                    flags=re.DOTALL,
                )
                _visible = (
                    _visible
                    .replace("<think>", "")
                    .replace("</think>", "")
                    .strip()
                )
                _visible = _TOOLCALL_BLOCK_RE.sub("", _visible).strip()
                _visible = _INLINE_JSON_TOOLCALL_RE.sub("", _visible).strip()
                full_response = _dedup_signal_tags(_visible)

                stream_tool_calls = getattr(
                    runtime.vllm_client, "last_stream_tool_calls", None,
                ) if hasattr(runtime, "vllm_client") and runtime.vllm_client else None
                first_response_has_tools = (
                    has_tool_calls(full_response) or bool(stream_tool_calls)
                )

                # Safety net: hallucinated tool call as text
                _hallucinated_tc = (
                    _raw_had_toolcall
                    and not full_response
                    and not first_response_has_tools
                )
                if _hallucinated_tc:
                    logger.warning(
                        "Agent %s: chat response was a hallucinated "
                        "tool call -- nudging re-generation",
                        agent_id,
                    )
                    _nudged_input = f"{user_input}\n\n{_CHAT_TOOLCALL_NUDGE}"

                    result = await runtime.process_message_async(
                        _nudged_input, history=history,
                    )
                    regen_response = result.response or ""
                    regen_response = _TOOLCALL_BLOCK_RE.sub(
                        "", regen_response,
                    ).strip()
                    regen_response = _INLINE_JSON_TOOLCALL_RE.sub(
                        "", regen_response,
                    ).strip()

                    if regen_response:
                        await websocket.send_json({
                            "type": "token",
                            "content": "</think>" + regen_response,
                        })

                    latency_ms = (time.perf_counter() - t0) * 1000
                    fresh_status = runtime.get_status()

                    _regen_reasoning = getattr(runtime, "_last_stream_thinking", "") or ""
                    await websocket.send_json({
                        "type": "response_end",
                        "response": regen_response,
                        "reasoning": _regen_reasoning,
                        "latency_ms": round(latency_ms, 1),
                        "nls": _build_nls_metadata(fresh_status),
                    })

                    history.append({"role": "user", "content": user_input})
                    _regen_asst: dict = {
                        "role": "assistant",
                        "content": regen_response,
                    }
                    if _regen_reasoning:
                        _regen_asst["reasoning"] = _regen_reasoning
                    history.append(_regen_asst)
                    if len(history) > 40:
                        history = history[-40:]
                    runtime.save_conversation_history(history)

                    if consciousness_scheduler is not None:
                        consciousness_scheduler.on_user_message_complete(
                            agent_id,
                        )
                    continue

                # Force agentic only when pre-goals exist but the model
                # produced no visible answer (cannot satisfy as plain chat).
                if (
                    not first_response_has_tools
                    and _pre_goals
                    and not (full_response or "").strip()
                ):
                    logger.info(
                        "Agent %s: model didn't call tools but %d task "
                        "goals detected and no visible text — forcing "
                        "agentic loop",
                        agent_id, len(_pre_goals),
                    )
                    first_response_has_tools = True
                    full_response = None
                    try:
                        await websocket.send_json({
                            "type": "response_replace",
                            "response": "",
                        })
                    except Exception:
                        pass
                    if _initial_thinking:
                        try:
                            await websocket.send_json({
                                "type": "turn_thinking",
                                "thinking": _initial_thinking[:2000],
                                "iteration": 0,
                            })
                        except Exception:
                            pass

                # Guard: empty visible response with no tool calls
                if not full_response and not first_response_has_tools:
                    logger.warning(
                        "Agent %s: streamed response was empty "
                        "(likely all <think>), re-generating",
                        agent_id,
                    )

                    result = await runtime.process_message_async(
                        user_input, history=history,
                    )
                    regen_response = result.response or ""
                    regen_response = (
                        regen_response
                        .replace("</think>", "")
                        .strip()
                    )
                    regen_response = _TOOLCALL_BLOCK_RE.sub(
                        "", regen_response,
                    ).strip()

                    if regen_response:
                        await websocket.send_json({
                            "type": "token",
                            "content": "</think>" + regen_response,
                        })

                    latency_ms = (time.perf_counter() - t0) * 1000
                    fresh_status = runtime.get_status()

                    _regen_thinking = getattr(runtime, "_last_stream_thinking", "") or ""
                    await websocket.send_json({
                        "type": "response_end",
                        "response": regen_response,
                        "reasoning": (
                            _initial_thinking or _regen_thinking
                        ),
                        "latency_ms": round(latency_ms, 1),
                        "nls": _build_nls_metadata(fresh_status),
                    })

                    history.append({"role": "user", "content": user_input})
                    _regen2_asst: dict = {
                        "role": "assistant",
                        "content": regen_response,
                    }
                    _regen2_thinking = (
                        _initial_thinking or _regen_thinking
                    )
                    if _regen2_thinking:
                        _regen2_asst["reasoning"] = _regen2_thinking
                    history.append(_regen2_asst)
                    if len(history) > 40:
                        history = history[-40:]
                    runtime.save_conversation_history(history)

                    if consciousness_scheduler is not None:
                        consciousness_scheduler.on_user_message_complete(
                            agent_id,
                        )
                    continue

                if first_response_has_tools:
                    # ═══════════════════════════════════
                    # AGENTIC PATH — tools detected
                    # ═══════════════════════════════════
                    _first_tc = getattr(
                        runtime.vllm_client, "last_stream_tool_calls", None,
                    ) if hasattr(runtime, "vllm_client") and runtime.vllm_client else None
                    # Forced agentic with no visible streamed text: never replay
                    # tool calls from an unrelated prior turn.
                    if full_response is None:
                        _first_tc = None

                    logger.info(
                        "Agent %s: [AGENTIC] entering agentic loop — "
                        "first_resp=%s first_tc_present=%s "
                        "thinking=%s goals=%s history_len=%d",
                        agent_id,
                        "None" if full_response is None else f"{len(full_response)}ch",
                        bool(_first_tc),
                        needs_thinking,
                        _pre_goals,
                        len(history),
                    )
                    agentic_config_v2 = runtime.get_agentic_config_v2()

                    agentic_abort.clear()
                    websocket.state.agentic_running = True

                    browser_pending: dict[str, asyncio.Future] = {}

                    async def _browser_emit_and_wait(
                        command: dict,
                    ) -> dict:
                        request_id = command["request_id"]
                        logger.info(
                            "Agent %s: SENDING browser_command reqId=%s action=%s",
                            agent_id, request_id, command.get("action"),
                        )
                        loop = asyncio.get_running_loop()
                        fut: asyncio.Future[dict] = loop.create_future()
                        browser_pending[request_id] = fut
                        await websocket.send_json(command)
                        return await fut

                    async def _request_browser_auth(
                        url: str, request_id: str,
                    ) -> dict:
                        loop = asyncio.get_running_loop()
                        fut: asyncio.Future[dict] = loop.create_future()
                        browser_pending[request_id] = fut
                        await websocket.send_json({
                            "type": "browser_auth_request",
                            "url": url,
                            "request_id": request_id,
                        })
                        logger.info(
                            "Agent %s: browser_auth_request SENT reqId=%s url=%s",
                            agent_id, request_id, url[:80],
                        )
                        return await fut

                    await websocket.send_json({
                        "type": "agentic_start",
                        "max_steps": agentic_config_v2.max_iterations,
                        "version": 2,
                    })

                    _eager_events: list[dict] = []

                    async def _on_event(event):
                        """Stream agentic events to the frontend."""
                        try:
                            await _dispatch_agentic_event(
                                event, websocket, runtime, agent_id,
                                agentic_config_v2, _eager_events,
                                history, user_input,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Agent %s: event send failed: %s",
                                agent_id, exc,
                            )

                    async def _bash_output(chunk: str):
                        try:
                            await websocket.send_json({
                                "type": "tool_output_chunk",
                                "chunk": chunk,
                                "tool_name": "bash",
                            })
                        except Exception:
                            pass

                    _shared_ctx: list[dict] = []

                    def _checkpoint(
                        ctx_snapshot, plan_steps, plan_done, iteration,
                    ):
                        logger.info(
                            "Agent %s: checkpoint at iteration %d "
                            "(%d ctx msgs)",
                            agent_id, iteration, len(ctx_snapshot),
                        )

                    try:
                        async def _on_browser_nav(action, url, title):
                            await websocket.send_json({
                                "type": "browser_navigation",
                                "url": url or "",
                                "title": title or "",
                                "action": action or "",
                            })

                        async def _emit_set_cookies(cookies: list) -> dict:
                            import uuid as _uuid
                            req_id = f"cookies-{_uuid.uuid4().hex[:8]}"
                            loop = asyncio.get_running_loop()
                            fut: asyncio.Future[dict] = loop.create_future()
                            browser_pending[req_id] = fut
                            await websocket.send_json({
                                "type": "browser_set_cookies",
                                "cookies": cookies,
                                "request_id": req_id,
                            })
                            return await asyncio.wait_for(fut, timeout=10.0)

                        # Wire DelegateManager callbacks for batch
                        # completion (WS broadcast + copilot_queue injection)
                        _dm = getattr(runtime, "delegate_manager", None)
                        if _dm is not None:
                            _cm = getattr(app.state, "connection_manager", None)

                            async def _on_batch_complete(batch_id, results):
                                _summaries = []
                                for r in results:
                                    status = "done" if r.success else r.exit_reason
                                    _summaries.append(
                                        f"#{r.delegate_number} [{status}]: "
                                        f"{r.summary[:200]}"
                                    )
                                _payload = {
                                    "type": "delegate_batch_complete",
                                    "batch_id": batch_id,
                                    "count": len(results),
                                    "results_summary": "\n".join(_summaries),
                                }
                                if _cm is not None:
                                    await _cm.broadcast(agent_id, _payload)

                                # Cancel the periodic check-back job now that
                                # the batch is complete — no more check-ins needed.
                                _sched_mgr = getattr(
                                    app.state, "scheduler_manager", None,
                                )
                                if _sched_mgr is not None:
                                    _job_name = f"delegate_checkback_{batch_id}"
                                    if _sched_mgr.remove_job(_job_name):
                                        logger.info(
                                            "Agent %s: removed delegate check-back "
                                            "job '%s' (batch complete)",
                                            agent_id, _job_name,
                                        )
                                _compile_prompt = (
                                    f"[DELEGATE_RESULTS] All {len(results)} "
                                    f"sub-agents completed (batch {batch_id})."
                                    f"\n\nResults:\n"
                                    + "\n".join(_summaries)
                                    + "\n\nCompile these results into a full "
                                    "report and DELIVER to the user via their "
                                    "preferred channel (see [CHANNEL ROUTING] "
                                    "if present). Do NOT just post in chat if "
                                    "the user requested delivery elsewhere."
                                )
                                # Also inject into copilot_queue as fallback
                                # (picked up if an agentic loop is running)
                                copilot_queue.put_nowait(_compile_prompt)

                                # Proactively trigger autonomous dispatch via
                                # inner loop so the orchestrator compiles and
                                # delivers results without waiting for the user.
                                _cs = getattr(
                                    app.state, "consciousness_scheduler", None,
                                )
                                _entry = (
                                    _cs._agents.get(agent_id)
                                    if _cs is not None else None
                                )
                                _il = (
                                    getattr(_entry, "inner_loop", None)
                                    if _entry is not None else None
                                )
                                if _il is not None:
                                    _il.enqueue_autonomous_dispatch(
                                        _compile_prompt,
                                        source="delegate_batch_complete",
                                    )
                                    logger.info(
                                        "Agent %s: delegate batch %s — "
                                        "autonomous dispatch enqueued",
                                        agent_id, batch_id,
                                    )
                                else:
                                    logger.info(
                                        "Agent %s: delegate batch %s complete "
                                        "— %d results in copilot_queue "
                                        "(no inner loop for auto-dispatch)",
                                        agent_id, batch_id, len(results),
                                    )

                            _tm_for_progress = getattr(runtime, "_team_manager", None)

                            async def _on_progress(status):
                                if _cm is not None:
                                    await _cm.broadcast(agent_id, {
                                        "type": "delegate_progress",
                                        "delegate_number": status.delegate_number,
                                        "state": status.state,
                                        "iteration": status.iteration,
                                        "max_iterations": status.max_iterations,
                                        "elapsed_seconds": status.elapsed_seconds,
                                        "task": status.task[:100],
                                    })
                                if _tm_for_progress is not None:
                                    try:
                                        await _tm_for_progress.on_delegate_progress(
                                            status.delegate_number, status,
                                        )
                                    except Exception:
                                        pass

                            _dm._on_batch_complete = _on_batch_complete
                            _dm._on_delegate_progress = _on_progress

                        # Wire copilot_queue into TeamManager so escalation
                        # messages from team members reach the orchestrator loop.
                        _tm = getattr(runtime, "_team_manager", None)
                        if _tm is not None:
                            _tm._copilot_queue = copilot_queue

                        # Phase 0: push USER_MESSAGE event into the event queue.
                        # We capture the event_id so we can surgically remove
                        # THIS specific event after the foreground path handles
                        # it — without touching channel or other queued events.
                        _cs_obj = getattr(app.state, "consciousness_scheduler", None)
                        _il = _cs_obj.get_inner_loop(agent_id) if _cs_obj is not None else None
                        _phase0_event_id: str | None = None
                        if _il is not None:
                            # Abort any running background autonomous dispatch
                            # immediately so vLLM is freed for the foreground turn.
                            _bg_abort = getattr(_il, "_autonomous_abort", None)
                            if _bg_abort is not None and not _bg_abort.is_set():
                                _bg_abort.set()
                                logger.info(
                                    "Agent %s: background dispatch aborted "
                                    "— foreground user message preempts it",
                                    agent_id,
                                )
                            from nls.engine.events import AgentEvent, EventType
                            _phase0_event = AgentEvent(
                                type=EventType.USER_MESSAGE,
                                source="ws",
                                payload={
                                    "user_input": user_input[:500],
                                    "has_attachments": bool(getattr(
                                        websocket.state, "_attachments", None,
                                    )),
                                },
                            )
                            _phase0_event_id = _phase0_event.event_id
                            _il.push_event(_phase0_event)

                        _agentic_coro = runtime.process_message_agentic_async(
                            user_input=user_input,
                            history=history,
                            on_event=_on_event,
                            abort_signal=agentic_abort,
                            first_response=full_response,
                            first_tool_calls=_first_tc,
                            on_bash_output=_bash_output,
                            copilot_queue=copilot_queue,
                            on_browser_navigation=_on_browser_nav,
                            on_browser_auth_request=_request_browser_auth,
                            emit_set_cookies=_emit_set_cookies,
                            enable_thinking=needs_thinking,
                            shared_context=_shared_ctx,
                            checkpoint_callback=_checkpoint,
                            pre_extracted_goals=_pre_goals,
                            pre_extracted_hints=_pre_hints,
                        )
                        agentic_result = await _run_agentic_with_receive(
                            websocket,
                            _agentic_coro,
                            agentic_abort,
                            copilot_queue,
                            agent_id,
                            browser_pending=browser_pending,
                        )

                        websocket.state.agentic_running = False
                        latency_ms = (time.perf_counter() - t0) * 1000

                        # Remove the exact USER_MESSAGE event we pushed at
                        # Phase 0 — the foreground path handled it.  We use
                        # the captured event_id so we never accidentally
                        # remove channel events, scheduler events, or any
                        # other queued work.
                        if _il is not None:
                            if _phase0_event_id:
                                try:
                                    _removed = _il.event_queue.remove_by_id(
                                        _phase0_event_id,
                                    )
                                    if _removed:
                                        logger.debug(
                                            "Agent %s: removed Phase-0 "
                                            "USER_MESSAGE %s after "
                                            "foreground agentic",
                                            agent_id, _phase0_event_id,
                                        )
                                except Exception:
                                    pass
                            _il._last_foreground_completion_ts = time.time()

                        if agentic_result.aborted:
                            runtime._last_agentic_abort_ts = time.time()

                        logger.info(
                            "Agent %s: agentic COMPLETED -- saving "
                            "history (history_len=%d, iterations=%d, "
                            "ctx_msgs=%d, final_resp_len=%d)",
                            agent_id, len(history),
                            agentic_result.iterations,
                            len(getattr(agentic_result, "context_messages", None) or []),
                            len(agentic_result.final_response or ""),
                        )
                        _agentic_user_entry: dict = {
                            "role": "user", "content": _strip_internal_blocks(user_input),
                        }
                        if _initial_thinking:
                            _agentic_user_entry["pre_agentic_reasoning"] = _initial_thinking
                        history.append(_agentic_user_entry)
                        _save_agentic_history_v2(
                            history, agentic_result,
                        )
                        if len(history) > 40:
                            history = history[-40:]
                        runtime.save_conversation_history(history)

                        try:
                            _agentic_final = agentic_result.final_response or ""
                            _agentic_final = _TOOLCALL_BLOCK_RE.sub("", _agentic_final)
                            _agentic_final = _INLINE_JSON_TOOLCALL_RE.sub("", _agentic_final).strip()
                            _live_hb = {}
                            if hasattr(runtime, "self_state") and runtime.self_state:
                                ss = runtime.self_state
                                _live_hb = {
                                    "bpm": round(getattr(ss, "bpm", 0.0), 2),
                                    "energy": round(getattr(ss, "energy", 1.0), 3),
                                    "mood_label": getattr(ss, "mood_label", "neutral"),
                                    "engagement": round(getattr(ss, "engagement", 0.0), 3),
                                    "bonding": round(getattr(ss, "bonding", 0.0), 3),
                                }
                            _live_hormones = agentic_result.hormones
                            if not _live_hormones and runtime.hypothalamus:
                                _live_hormones = {
                                    n: round(h.level, 3)
                                    for n, h in runtime.hypothalamus.hormones.items()
                                }
                            logger.info(
                                "Agent %s: [AGENTIC] COMPLETE — "
                                "steps=%d tc=%d aborted=%s "
                                "exit=%s duration=%.0fms "
                                "hormones=%s final_len=%d",
                                agent_id,
                                agentic_result.iterations,
                                agentic_result.total_tool_calls,
                                agentic_result.aborted,
                                agentic_result.abort_reason,
                                agentic_result.total_duration_ms,
                                _live_hormones,
                                len(_agentic_final or ""),
                            )
                            _wm_final = None
                            if runtime.working_memory is not None:
                                try:
                                    _wm_final = runtime.working_memory.get_summary()
                                except Exception:
                                    pass
                            await websocket.send_json({
                                "type": "agentic_complete",
                                "total_steps": agentic_result.iterations,
                                "total_tool_calls": agentic_result.total_tool_calls,
                                "aborted": agentic_result.aborted,
                                "abort_reason": agentic_result.abort_reason,
                                "duration_ms": round(agentic_result.total_duration_ms, 1),
                                "hormones": _live_hormones,
                                "working_memory": _wm_final,
                                "final_response": _agentic_final,
                                "nls": {
                                    "hormones": _live_hormones,
                                    "heartbeat": _live_hb,
                                    "facts_in_memory": (
                                        runtime.domain_db.fact_count()
                                        if runtime.domain_db else 0
                                    ),
                                },
                            })

                            await websocket.send_json({
                                "type": "activity_status", "text": "",
                            })

                            if agentic_result.name_update:
                                await websocket.send_json({
                                    "type": "name_update",
                                    "name": agentic_result.name_update,
                                    "agent_id": agent_id,
                                })
                                agent_manager.update_agent_name(
                                    agent_id, agentic_result.name_update,
                                )

                            # Execute deferred post-completion actions
                            # (e.g. "send me on WhatsApp when done")
                            _deferred = getattr(agentic_result, "deferred_actions", [])
                            _already_sent = getattr(agentic_result, "channels_sent", set())
                            if _deferred and _agentic_final and runtime._agent_tools:
                                for _da in _deferred:
                                    _ch = _da.get("channel", "")
                                    _instr = _da.get("instruction", "")

                                    if _ch in _already_sent:
                                        logger.info(
                                            "Deferred %s: skipped — agent already sent via %s during the loop",
                                            _ch, _ch,
                                        )
                                        continue

                                    _tool_name = {
                                        "whatsapp": "whatsapp_send",
                                        "telegram": "telegram_send",
                                        "email": "email_send",
                                    }.get(_ch)
                                    if not _tool_name:
                                        continue
                                    _tool = next(
                                        (t for t in runtime._agent_tools
                                         if getattr(t, "name", "") == _tool_name),
                                        None,
                                    )
                                    if _tool is None:
                                        logger.warning(
                                            "Deferred action: tool %s not found",
                                            _tool_name,
                                        )
                                        continue

                                    # Resolve recipient from channel config
                                    _da_params: dict = {}
                                    if _ch == "whatsapp":
                                        _wa_adapter = getattr(_tool, "_adapter", None)
                                        if _wa_adapter:
                                            _wa_cfg = _wa_adapter._agent_cfg(agent_id)
                                            _owner_phone = _wa_cfg.get("owner_identity", "")
                                            if _owner_phone:
                                                _da_params["phone"] = _owner_phone
                                    elif _ch == "telegram":
                                        _tg_adapter = getattr(_tool, "_adapter", None)
                                        if _tg_adapter:
                                            _tg_cfg = _tg_adapter._agent_configs.get(agent_id, {})
                                            _owner_chat_id = _tg_cfg.get("owner_identity", "")
                                            if _owner_chat_id:
                                                _da_params["chat_id"] = str(_owner_chat_id)
                                    elif _ch == "email":
                                        _em_adapter = getattr(_tool, "_adapter", None)
                                        if _em_adapter:
                                            _em_cfg = _em_adapter._agent_configs.get(agent_id, {})
                                            _owner_email = _em_cfg.get("owner_identity", [])
                                            if isinstance(_owner_email, str):
                                                _owner_email = [_owner_email] if _owner_email else []
                                            if _owner_email:
                                                _da_params["to"] = _owner_email[0]
                                                _da_params["subject"] = "Update from your agent"

                                    _msg = _agentic_final[:1500]
                                    _da_params["text"] = _msg
                                    try:
                                        _da_result = await _tool.execute(_da_params)
                                        logger.info(
                                            "Deferred %s sent: %s",
                                            _ch,
                                            getattr(_da_result, "content", "")[:80],
                                        )
                                    except Exception as _da_exc:
                                        logger.warning(
                                            "Deferred %s failed: %s",
                                            _ch, _da_exc,
                                        )
                        except Exception:
                            logger.info(
                                "Agent %s: WS closed during agentic "
                                "complete send", agent_id,
                            )
                            break

                    except Exception as exc:
                        websocket.state.agentic_running = False
                        if isinstance(exc, WebSocketDisconnect):
                            _salvage_agentic_context(
                                history, _shared_ctx,
                                user_input, runtime, agent_id,
                            )
                            raise
                        logger.error(
                            "Agent %s: agentic loop failed: %s",
                            agent_id, exc, exc_info=True,
                        )
                        _salvage_agentic_context(
                            history, _shared_ctx,
                            user_input, runtime, agent_id,
                        )
                        error_hormones = {}
                        if runtime.hypothalamus is not None:
                            error_hormones = {
                                n: round(h.level, 3)
                                for n, h in runtime.hypothalamus.hormones.items()
                            }
                        try:
                            await websocket.send_json({
                                "type": "agentic_complete",
                                "total_steps": 0,
                                "total_tool_calls": 0,
                                "aborted": True,
                                "abort_reason": f"Internal error: {exc}",
                                "duration_ms": round(
                                    (time.perf_counter() - t0) * 1000, 1,
                                ),
                                "hormones": error_hormones,
                            })
                        except Exception:
                            logger.info(
                                "Agent %s: WS closed, cannot send "
                                "agentic error", agent_id,
                            )
                            break

                    if consciousness_scheduler is not None:
                        consciousness_scheduler.on_user_message_complete(
                            agent_id,
                        )
                    continue

                # ═══════════════════════════════════════
                # SINGLE-TURN — no tools detected
                # ═══════════════════════════════════════
                _turn_res = runtime.last_stream_turn_result
                if _turn_res is not None:
                    result_dict = {
                        "response": _turn_res.response,
                        "signals": [
                            {"type": getattr(s, "signal_type", ""),
                             "domain": getattr(s, "domain_path", ""),
                             "content": (getattr(s, "content", "") or "")[:200]}
                            for s in _turn_res.signals
                        ],
                        "agency_actions": [],
                        "name_update": getattr(runtime, "_last_name_update", None),
                    }
                else:
                    result = await runtime.process_message_async(
                        user_input, history=history,
                    )
                    result_dict = {
                        "response": result.response,
                        "signals": [],
                        "agency_actions": [],
                        "name_update": getattr(result, "name_update", None),
                    }

                latency_ms = (time.perf_counter() - t0) * 1000

                # Send tool usage events (before response_end)
                for action in result_dict.get("agency_actions", []):
                    action_type = action.get("type", "")

                    if action_type == "web_search":
                        action_result = action.get("result", "")
                        result_str = (
                            str(action_result) if action_result else ""
                        )
                        await websocket.send_json({
                            "type": "tool_use",
                            "tool": "web_search",
                            "query": action.get("query", ""),
                            "source": (
                                action_result.get("source", "web")
                                if isinstance(action_result, dict)
                                else (
                                    "wikipedia"
                                    if "Wikipedia:" in result_str
                                    else "web"
                                )
                            ),
                            "result_preview": result_str[:300],
                            "success": not result_str.startswith("[ERROR]"),
                            "reason": action.get("reason", ""),
                        })

                    elif action_type in (
                        "file_write", "file_edit", "file_read",
                        "terminal", "git",
                    ):
                        action_result = action.get("result", {})
                        result_meta = (
                            action_result.get("metadata", {})
                            if isinstance(action_result, dict)
                            else {}
                        )
                        await websocket.send_json({
                            "type": "tool_use",
                            "tool": action_type,
                            "path": result_meta.get(
                                "path", action.get("path", ""),
                            ),
                            "query": action.get(
                                "command", action.get("query", ""),
                            ),
                            "result_preview": (
                                str(action_result)[:300]
                                if action_result else ""
                            ),
                            "success": (
                                action_result.get("success", True)
                                if isinstance(action_result, dict)
                                else True
                            ),
                        })

                # Send response end with NLS metadata
                fresh_status = runtime.get_status()
                _final_resp = (result_dict.get("response", full_response) or "")
                _final_resp = _final_resp.replace("</think>", "").strip()
                _final_resp = _TOOLCALL_BLOCK_RE.sub("", _final_resp).strip()
                _final_resp = _INLINE_JSON_TOOLCALL_RE.sub("", _final_resp).strip()
                _final_resp = _dedup_signal_tags(_final_resp)
                _sk = getattr(websocket.state, "session_key", "websocket:main")

                _reasoning = getattr(runtime, "_last_stream_thinking", "") or ""
                _resp_json: dict = {
                    "type": "response_end",
                    "response": _final_resp,
                    "reasoning": _reasoning,
                    "latency_ms": round(latency_ms, 1),
                    "nls": _build_nls_metadata(
                        fresh_status,
                        signals=result_dict.get("signals", []),
                        meta_weight=result_dict.get("meta_weight", 0.0),
                        agency_actions=result_dict.get("agency_actions", []),
                    ),
                }
                if _sk and _sk != "websocket:main":
                    _resp_json["session_key"] = _sk
                await websocket.send_json(_resp_json)

                if result_dict.get("name_update"):
                    new_name = result_dict["name_update"]
                    await websocket.send_json({
                        "type": "name_update",
                        "name": new_name,
                        "agent_id": agent_id,
                    })
                    agent_manager.update_agent_name(agent_id, new_name)
                    logger.info(
                        "Agent %s: name updated to '%s'",
                        agent_id, new_name,
                    )

                history.append({"role": "user", "content": user_input})
                _asst_entry: dict = {
                    "role": "assistant",
                    "content": _final_resp,
                }
                if _reasoning:
                    _asst_entry["reasoning"] = _reasoning
                history.append(_asst_entry)
                if len(history) > 40:
                    history = history[-40:]

                if _sk and _sk != "websocket:main":
                    runtime.save_session_history(history, session_key=_sk)
                else:
                    runtime.save_conversation_history(history)

                if result_dict.get("sleep_request"):
                    await websocket.send_json({
                        "type": "status",
                        "agent_status": "sleeping",
                        "sleep_reason": result_dict["sleep_request"].get(
                            "reason", "",
                        ),
                    })

            else:
                # Agentic disabled: use async streaming + process
                full_response = ""
                try:
                    async for token in runtime.process_message_stream_async(
                        user_input, history=history,
                    ):
                        if isinstance(token, tuple):
                            _kind, _text = token
                            if _kind == "thinking":
                                await websocket.send_json({
                                    "type": "reasoning_token",
                                    "content": _text,
                                })
                            elif _kind == "thinking_end":
                                await websocket.send_json({
                                    "type": "reasoning_end",
                                })
                            continue
                        full_response += token
                        await websocket.send_json({
                            "type": "token",
                            "content": token,
                        })
                except Exception as gen_exc:
                    if isinstance(gen_exc, WebSocketDisconnect):
                        raise
                    logger.error(
                        "Agent %s: simple stream failed: %s",
                        agent_id, gen_exc,
                    )
                    continue

                _vis_simple = re.sub(
                    r"<think>.*?</think>", "", full_response,
                    flags=re.DOTALL,
                )
                _vis_simple = (
                    _vis_simple
                    .replace("<think>", "")
                    .replace("</think>", "")
                    .strip()
                )
                _vis_simple = _TOOLCALL_BLOCK_RE.sub("", _vis_simple).strip()
                _vis_simple = _dedup_signal_tags(_vis_simple)

                _turn_res = runtime.last_stream_turn_result
                if _turn_res is not None:
                    result_dict = {
                        "response": _turn_res.response,
                        "signals": [],
                    }
                else:
                    result_dict = {"response": _vis_simple, "signals": []}

                latency_ms = (time.perf_counter() - t0) * 1000
                fresh_status = runtime.get_status()
                _reasoning = getattr(runtime, "_last_stream_thinking", "") or ""
                await websocket.send_json({
                    "type": "response_end",
                    "response": result_dict.get("response", _vis_simple),
                    "reasoning": _reasoning,
                    "latency_ms": round(latency_ms, 1),
                    "nls": _build_nls_metadata(
                        fresh_status,
                        signals=result_dict.get("signals", []),
                    ),
                })

                if result_dict.get("name_update"):
                    new_name = result_dict["name_update"]
                    await websocket.send_json({
                        "type": "name_update",
                        "name": new_name,
                        "agent_id": agent_id,
                    })
                    agent_manager.update_agent_name(agent_id, new_name)

                history.append({"role": "user", "content": user_input})
                _simple_asst: dict = {
                    "role": "assistant",
                    "content": result_dict.get("response", full_response),
                }
                if _reasoning:
                    _simple_asst["reasoning"] = _reasoning
                history.append(_simple_asst)
                if len(history) > 40:
                    history = history[-40:]
                runtime.save_conversation_history(history)

                # Reset post-completion drive cooldown after any chat turn
                # (not just full agentic loops) so the DMN doesn't fire
                # immediately after the agent responds.
                try:
                    _il_st = getattr(runtime, "_inner_loop", None)
                    if _il_st is not None:
                        _il_st._last_foreground_completion_ts = time.time()
                except Exception:
                    pass

            if consciousness_scheduler is not None:
                consciousness_scheduler.on_user_message_complete(agent_id)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: agent %s", agent_id)
    except Exception as exc:
        logger.error(
            "WebSocket error for agent %s: %s", agent_id, exc,
            exc_info=True,
        )
    finally:
        if runtime is not None:
            runtime._active_sessions = max(0, runtime._active_sessions - 1)

        connection_manager.unregister(agent_id, websocket)

        if runtime is not None:
            try:
                logger.info(
                    "Agent %s: finally-block save (history_len=%d)",
                    agent_id, len(history),
                )
                runtime.save_conversation_history(history)
                runtime.save_state()
            except (OSError, FileNotFoundError):
                logger.info(
                    "Agent %s: data directory gone (agent deleted?), "
                    "skipping state save", agent_id,
                )


# ─── Agentic event dispatcher ───────────────────────────────────

async def _dispatch_agentic_event(
    event,
    websocket: WebSocket,
    runtime: Any,
    agent_id: str,
    agentic_config,
    eager_events: list[dict],
    history: list[dict],
    user_input: str,
) -> None:
    """Dispatch a single agentic loop event to the WebSocket client.

    Extracted to avoid duplicating the ~250-line event handler inside
    the websocket_chat closure.
    """
    data = event.to_dict()
    etype = data.get("type", "")

    # Detailed logging for all non-token events (tokens are too noisy)
    if etype != "agentic_token" and etype != "tool_call_delta":
        _iter = data.get("iteration", "?")
        _extra = ""
        if etype == "turn_end":
            _extra = (
                f" has_tc={data.get('has_tool_calls')} "
                f"tool_calls={data.get('tool_calls')}"
            )
        elif etype == "tool_execution_start":
            _extra = f" tool={data.get('tool_name')} args={str(data.get('arguments',''))[:80]}"
        elif etype == "tool_execution_end":
            _extra = (
                f" tool={data.get('tool_name')} "
                f"error={data.get('is_error')} "
                f"preview={str(data.get('result_preview',''))[:80]}"
            )
        logger.info(
            "Agent %s: [EVENT] %s iter=%s%s",
            agent_id, etype, _iter, _extra,
        )

    _sa_tag = {}
    if data.get("sub_agent"):
        _sa_tag = {
            "sub_agent": True,
            "delegate_number": data.get("delegate_number", 0),
        }

    if etype == "agentic_token":
        _tok = data.get("token", "")
        _tok_iter = data.get("iteration", 0)
        _is_thinking = bool(data.get("thinking"))

        # Suppress tokens when the model regurgitates internal context markers.
        # Uses a small prefix buffer per iteration to detect [WORKING MEMORY
        # even when the string arrives across multiple streaming chunks.
        _suppress_iter = getattr(websocket.state, "_wm_suppress_iter", -1)
        if _tok_iter == _suppress_iter:
            return
        if _tok_iter != _suppress_iter and _suppress_iter >= 0:
            websocket.state._wm_suppress_iter = -1

        _WM_MARKER = "[WORKING MEMORY"
        _wm_buf: str = getattr(websocket.state, "_wm_prefix_buf", "")
        _wm_buf_iter: int = getattr(websocket.state, "_wm_buf_iter", -1)
        if _tok_iter != _wm_buf_iter:
            _wm_buf = ""
            websocket.state._wm_buf_iter = _tok_iter

        if not _is_thinking and len(_wm_buf) < len(_WM_MARKER):
            _wm_buf += _tok
            websocket.state._wm_prefix_buf = _wm_buf
            if _WM_MARKER.startswith(_wm_buf):
                return
            if _WM_MARKER in _wm_buf:
                websocket.state._wm_suppress_iter = _tok_iter
                return
            for _tok_chunk in (_wm_buf,):
                await websocket.send_json({
                    "type": "agentic_token",
                    "token": _tok_chunk,
                    "iteration": _tok_iter,
                    **_sa_tag,
                })
            websocket.state._wm_prefix_buf = _WM_MARKER
            return

        _token_msg: dict = {
            "type": "agentic_token",
            "token": _tok,
            "iteration": _tok_iter,
            **_sa_tag,
        }
        if _is_thinking:
            _token_msg["thinking"] = True
        await websocket.send_json(_token_msg)

    elif etype == "tool_call_delta":
        await websocket.send_json({
            "type": "tool_call_delta",
            "index": data.get("index", 0),
            "function_name": data.get("function_name", ""),
            "arguments_delta": data.get("arguments_delta", ""),
            "iteration": data.get("iteration", 0),
            **_sa_tag,
        })

    elif etype == "agentic_plan":
        await websocket.send_json({
            "type": "agentic_plan",
            "steps": data.get("steps", []),
            "iteration": data.get("iteration", 0),
            "plan_id": data.get("plan_id", ""),
            "title": data.get("title", ""),
            "todo_id": data.get("todo_id", ""),
            **_sa_tag,
        })

    elif etype == "plan_step_update":
        await websocket.send_json({
            "type": "plan_step_update",
            "step_index": data.get("step_index", -1),
            "status": data.get("status", "done"),
            "label": data.get("label", ""),
            "plan_id": data.get("plan_id", ""),
            "todo_id": data.get("todo_id", ""),
            "iteration": data.get("iteration", 0),
            **_sa_tag,
        })

    elif etype == "tool_execution_start":
        await websocket.send_json({
            "type": "tool_execution_start",
            "tool_name": data.get("tool_name", ""),
            "call_id": data.get("call_id", ""),
            "arguments": data.get("arguments", {}),
            "iteration": data.get("iteration", 0),
            **_sa_tag,
        })
        if not _sa_tag:
            await websocket.send_json({
                "type": "activity_status",
                "text": f"Running: {data.get('tool_name', '')}",
            })

    elif etype == "tool_execution_end":
        _tool_end_msg: dict = {
            "type": "tool_execution_end",
            "tool_name": data.get("tool_name", ""),
            "call_id": data.get("call_id", ""),
            "is_error": data.get("is_error", False),
            "result_preview": data.get("result_preview", ""),
            "iteration": data.get("iteration", 0),
            **_sa_tag,
        }
        _details = data.get("details")
        if _details:
            _tool_end_msg["details"] = _details
        await websocket.send_json(_tool_end_msg)

    elif etype == "activity_status":
        await websocket.send_json({
            "type": "activity_status",
            "text": data.get("message", ""),
            "status": data.get("status", ""),
            "elapsed_ms": data.get("elapsed_ms", 0),
        })

    elif etype == "tool_output_chunk":
        await websocket.send_json({
            "type": "tool_output_chunk",
            "chunk": data.get("chunk", ""),
            "tool_name": data.get("tool_name", ""),
            **_sa_tag,
        })

    elif etype == "turn_thinking":
        await websocket.send_json({
            "type": "turn_thinking",
            "thinking": data.get("thinking") or data.get("content", ""),
            "iteration": data.get("iteration", 0),
            **_sa_tag,
        })

    elif etype == "delegate_spawn":
        _spawn_payload: dict = {
            "type": "delegate_start",
            "delegate_number": data.get("delegate_number", 0),
            "delegate_task": data.get("delegate_task", ""),
            "max_steps": data.get("max_steps", 8),
            "iteration": data.get("iteration", 0),
        }
        if data.get("team_id"):
            _spawn_payload["team_id"] = data["team_id"]
        await websocket.send_json(_spawn_payload)

    elif etype == "delegate_start":
        await websocket.send_json({
            "type": "delegate_start",
            "delegate_number": data.get("delegate_number", 0),
            "delegate_task": data.get("delegate_task", ""),
            "max_steps": data.get("max_steps", 8),
            "iteration": data.get("iteration", 0),
        })

    elif etype == "delegate_complete":
        await websocket.send_json({
            "type": "delegate_end",
            "delegate_number": data.get("delegate_number", 0),
            "delegate_task": data.get("delegate_task", ""),
            "iterations": data.get("iterations", 0),
            "tool_calls": data.get("tool_calls", 0),
            "aborted": data.get("aborted", False),
            "summary": data.get("summary", ""),
            "iteration": data.get("iteration", 0),
        })

    elif etype == "delegate_failed":
        await websocket.send_json({
            "type": "delegate_end",
            "delegate_number": data.get("delegate_number", 0),
            "delegate_task": data.get("delegate_task", ""),
            "iterations": data.get("iterations", 0),
            "tool_calls": data.get("tool_calls", 0),
            "aborted": True,
            "summary": data.get("summary", data.get("error", "")),
            "iteration": data.get("iteration", 0),
        })

    elif etype == "delegate_end":
        await websocket.send_json({
            "type": "delegate_end",
            "delegate_number": data.get("delegate_number", 0),
            "delegate_task": data.get("delegate_task", ""),
            "iterations": data.get("iterations", 0),
            "tool_calls": data.get("tool_calls", 0),
            "aborted": data.get("aborted", False),
            "summary": data.get("summary", ""),
            "iteration": data.get("iteration", 0),
        })

    elif etype == "browser_navigation":
        await websocket.send_json({
            "type": "browser_navigation",
            "url": data.get("url", ""),
            "title": data.get("title", ""),
            "action": data.get("action", ""),
        })

    elif etype == "ask_user":
        await websocket.send_json({
            "type": "ask_user",
            "question": data.get("question", ""),
            "request_id": data.get("request_id", ""),
            "iteration": data.get("iteration", 0),
        })

    elif etype == "communicate":
        await websocket.send_json({
            "type": "communicate",
            "message": data.get("message", ""),
            "iteration": data.get("iteration", 0),
            **_sa_tag,
        })

    elif etype == "probe_signal":
        await websocket.send_json({
            "type": "probe_signal",
            "signals": data.get("signals", {}),
            "fired": data.get("fired", []),
            "iteration": data.get("iteration", 0),
            "mid_generation": data.get("mid_generation", False),
        })

    elif etype == "user_answer":
        await websocket.send_json({
            "type": "user_answer",
            "content": data.get("answer", ""),
        })

    elif etype == "turn_end":
        live_hormones = {}
        if runtime.hypothalamus is not None:
            live_hormones = {
                n: round(h.level, 3)
                for n, h in runtime.hypothalamus.hormones.items()
            }
        _wm_snap = None
        if runtime.working_memory is not None:
            try:
                _wm_snap = runtime.working_memory.get_summary()
            except Exception:
                pass
        await websocket.send_json({
            "type": "agentic_iteration",
            "step": data.get("iteration", 0),
            "max_steps": agentic_config.max_iterations,
            "tool_calls": data.get("tool_calls", []),
            "tool_results": data.get("tool_results", []),
            "hormones": live_hormones,
            "working_memory": _wm_snap,
            "duration_ms": round(data.get("duration_ms", 0), 1),
            "signals_count": 0,
            **_sa_tag,
        })
        _resp_text = data.get("response_text", "").strip()
        if _resp_text and _sa_tag:
            await websocket.send_json({
                "type": "communicate",
                "message": _resp_text,
                "iteration": data.get("iteration", 0),
                "mid_loop": True,
                **_sa_tag,
            })
        if not _sa_tag:
            eager_events.append({
                "step": data.get("iteration", 0),
                "tool_calls": data.get("tool_calls", []),
                "tool_results": data.get("tool_results", []),
                "duration_ms": round(data.get("duration_ms", 0), 1),
            })

    elif etype == "agent_start" and _sa_tag:
        await websocket.send_json({
            "type": "agentic_start",
            "max_steps": data.get("max_iterations", 15),
            **_sa_tag,
        })

    elif etype == "agent_end" and _sa_tag:
        await websocket.send_json({
            "type": "agentic_complete",
            "total_steps": data.get("iterations", 0),
            "total_tool_calls": data.get("total_tool_calls", 0),
            "duration_ms": data.get("duration_ms", 0),
            "aborted": data.get("aborted", False),
            "abort_reason": data.get("abort_reason", ""),
            **_sa_tag,
        })

    elif etype == "agent_end" and not _sa_tag:
        try:
            _eager_hist = list(history)
            _eager_hist.append({
                "role": "user",
                "content": user_input,
            })
            _eager_meta = {
                "agentic": True,
                "iterations": data.get("iterations", 0),
                "tool_calls": data.get("total_tool_calls", 0),
                "aborted": data.get("aborted", False),
                "abort_reason": data.get("abort_reason", ""),
                "events": list(eager_events),
            }
            _eager_hist.append({
                "role": "assistant",
                "content": data.get("final_response", ""),
                "metadata": _eager_meta,
            })
            runtime.save_conversation_history(_eager_hist)
            logger.info(
                "Agent %s: eager history save on "
                "agent_end (len=%d, events=%d)",
                agent_id, len(_eager_hist),
                len(eager_events),
            )
        except Exception as _es:
            logger.warning(
                "Agent %s: eager save failed: %s",
                agent_id, _es,
            )
