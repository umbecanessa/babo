"""Slash command handler for the chat WebSocket."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


def _get_inner_loop(app: Any, agent_id: str):
    """Retrieve the active InnerLoop for an agent, if one exists."""
    scheduler = getattr(app.state, "consciousness_scheduler", None)
    if scheduler is None:
        return None
    entry = scheduler._agents.get(agent_id)
    if entry is None:
        return None
    return entry.inner_loop


async def _handle_command(
    command: str,
    websocket: WebSocket,
    runtime: Any,
    agent_id: str,
    app: Any,
    history: list,
    data: dict | None = None,
) -> None:
    """Handle a slash command from the client."""
    data = data or {}

    if command == "sleep":
        try:
            inner_loop = _get_inner_loop(app, agent_id)
            if inner_loop is not None:
                inner_loop.stop(reason="sleep:user_requested")

            sleep_scheduler = app.state.sleep_scheduler
            from nls.models import SleepRequest

            hormones: dict[str, float] = {}
            if runtime.hypothalamus is not None:
                hormones = {
                    name: round(h.level, 3)
                    for name, h in runtime.hypothalamus.hormones.items()
                }

            signal_count = 0
            if runtime.ans is not None:
                summary = runtime.ans.get_buffer_summary()
                signal_count = summary.get("learnable_signals", 0)

            sleep_request = SleepRequest(
                agent_id=agent_id,
                reason="user_requested",
                signal_count=signal_count,
                hormones=hormones,
            )
            await sleep_scheduler.enqueue(sleep_request)

            await websocket.send_json({
                "type": "status",
                "agent_status": "sleeping",
                "sleep_reason": "User requested sleep cycle",
            })
            logger.info("Agent %s: user-triggered sleep", agent_id)
        except Exception as exc:
            logger.error("Sleep command failed for %s: %s", agent_id, exc)
            await websocket.send_json({
                "type": "status",
                "agent_status": "error",
                "content": f"Sleep failed: {exc}",
            })

    elif command == "sleep_confirm":
        from server.routes.chat.sleep_negotiation import apply_sleep_confirm

        await apply_sleep_confirm(
            app, agent_id, websocket, source="command",
        )

    elif command == "sleep_deny":
        from server.routes.chat.sleep_negotiation import apply_sleep_deny

        await apply_sleep_deny(
            app, agent_id, websocket, source="command",
        )

    elif command in ("budget_extend", "budget_stop"):
        copilot_queue = getattr(websocket.state, "copilot_queue", None)
        if copilot_queue is None:
            await websocket.send_json({
                "type": "budget_command_result",
                "ok": False,
                "action": command,
                "content": "No active task is waiting for a budget decision.",
            })
            return
        try:
            if command == "budget_stop":
                copilot_queue.put_nowait({"action": "terminate"})
                extra = 0
            else:
                extra = int(data.get("extra_iterations", 0) or 0)
                if extra <= 0:
                    extra = 10
                copilot_queue.put_nowait({
                    "action": "extend",
                    "extra_iterations": extra,
                })
            await websocket.send_json({
                "type": "budget_command_result",
                "ok": True,
                "action": command,
                "extra_iterations": extra,
            })
            logger.info(
                "Agent %s: budget command %s (extra=%s)",
                agent_id, command, extra,
            )
        except Exception as exc:
            await websocket.send_json({
                "type": "budget_command_result",
                "ok": False,
                "action": command,
                "content": str(exc),
            })

    elif command == "dream_config":
        dmn = getattr(runtime, "dmn", None)
        if dmn is None:
            await websocket.send_json({
                "type": "dream_config",
                "error": "DMN not initialized for this agent.",
            })
        else:
            action = data.get("action", "get")

            if action == "get":
                await websocket.send_json({
                    "type": "dream_config",
                    **dmn.active_dream_config,
                })

            elif action == "set":
                if "enabled" in data:
                    dmn.active_enabled = bool(data["enabled"])
                if "probability" in data:
                    prob = float(data["probability"])
                    dmn._active_probability = max(0.0, min(1.0, prob))
                await websocket.send_json({
                    "type": "dream_config",
                    "updated": True,
                    **dmn.active_dream_config,
                })
                logger.info(
                    "Agent %s: dream config updated from UI "
                    "(enabled=%s, prob=%.2f)",
                    agent_id,
                    dmn.active_enabled,
                    dmn._active_probability,
                )

            elif action == "trigger":
                inner_loop = _get_inner_loop(app, agent_id)
                if inner_loop is None or not inner_loop.is_running:
                    await websocket.send_json({
                        "type": "dream_config",
                        "error": "Inner loop not running -- "
                                 "agent must be conscious.",
                    })
                elif inner_loop._active_dream_task is not None:
                    await websocket.send_json({
                        "type": "dream_config",
                        "error": "Active dream already in progress.",
                    })
                else:
                    await inner_loop._start_active_dream(dmn)
                    await websocket.send_json({
                        "type": "dream_config",
                        "triggered": True,
                        "message": "Active dream started.",
                    })
                    logger.info(
                        "Agent %s: active dream triggered from UI",
                        agent_id,
                    )

    elif command == "dream_findings":
        findings = runtime.pop_dream_findings(max_count=5)
        findings_data = []
        for f in findings:
            if hasattr(f, "to_broadcast"):
                findings_data.append(f.to_broadcast())
            else:
                findings_data.append({
                    "summary": str(getattr(f, "summary", "")),
                    "relevance": getattr(f, "relevance_score", 0.0),
                })
        await websocket.send_json({
            "type": "dream_findings",
            "findings": findings_data,
            "count": len(findings_data),
        })

    elif command == "abort":
        abort_signal = getattr(websocket.state, "agentic_abort", None)
        is_running = getattr(websocket.state, "agentic_running", False)
        if abort_signal and is_running:
            abort_signal.set()
            await websocket.send_json({
                "type": "status",
                "content": (
                    "Abort signal sent. Orchestrator will stop after the "
                    "current step. Running delegates are unchanged — stop "
                    "them from Project overview or ask the agent to terminate them."
                ),
            })
            logger.info("Agent %s: agentic abort requested (orchestrator only)", agent_id)
        else:
            await websocket.send_json({
                "type": "status",
                "content": "No agentic task running to abort.",
            })

    elif command == "status":
        sections_raw = data.get("sections")
        sections = set(sections_raw) if isinstance(sections_raw, list) else None
        status = runtime.get_status(sections=sections)
        _status_msg: dict = {
            "type": "status",
            "agent_status": "alive",
            "facts_in_memory": status.get("facts_in_memory", 0),
            "turn_count": status.get("turn_count", 0),
            "sleep_count": status.get("sleep_count", 0),
            "hormones": status.get("hormones", {}),
            "ans": status.get("ans", {}),
            "heartbeat": status.get("heartbeat", {}),
        }
        for _fb_key in (
            "working_memory", "narrative", "theory_of_mind",
            "predictive_processing", "network_dynamics",
        ):
            _fb_val = status.get(_fb_key)
            if _fb_val:
                _status_msg[_fb_key] = _fb_val
        await websocket.send_json(_status_msg)

    else:
        await websocket.send_json({
            "type": "status",
            "content": f"Unknown command: {command}",
        })
