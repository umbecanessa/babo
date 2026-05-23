"""Agentic loop runner with concurrent WebSocket receive."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def _run_agentic_with_receive(
    websocket: WebSocket,
    agentic_coro,
    agentic_abort: asyncio.Event,
    copilot_queue: asyncio.Queue,
    agent_id: str,
    browser_pending: dict[str, asyncio.Future] | None = None,
):
    """Run an agentic coroutine concurrently with WebSocket receive.

    Launches the agentic coroutine as a task, then enters a receive
    loop that handles abort commands, co-pilot messages, and browser
    responses until the task completes or the WebSocket disconnects.

    Returns the agentic result (or raises on disconnect).
    """
    task = asyncio.create_task(agentic_coro)
    _browser_pending = browser_pending if browser_pending is not None else {}

    try:
        while not task.done():
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                raise

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "message", "content": raw}

            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg.get("type") == "browser_response":
                request_id = msg.get("request_id", "")
                logger.info(
                    "Agent %s: GOT browser_response reqId=%s status=%s pending_keys=%s",
                    agent_id, request_id, msg.get("status"), list(_browser_pending.keys()),
                )
                fut = _browser_pending.pop(request_id, None)
                if fut and not fut.done():
                    fut.set_result(msg)
                    logger.info(
                        "Agent %s: browser_response RESOLVED for %s",
                        agent_id, request_id,
                    )
                else:
                    logger.warning(
                        "Agent %s: browser_response NO MATCHING FUTURE for %s (fut=%s)",
                        agent_id, request_id, fut,
                    )

            elif msg.get("type") == "browser_auth_response":
                request_id = msg.get("request_id", "")
                logger.info(
                    "Agent %s: GOT browser_auth_response reqId=%s success=%s",
                    agent_id, request_id, msg.get("success"),
                )
                fut = _browser_pending.pop(request_id, None)
                if fut and not fut.done():
                    fut.set_result(msg)
                else:
                    logger.warning(
                        "Agent %s: browser_auth_response NO MATCHING FUTURE for %s",
                        agent_id, request_id,
                    )

            elif msg.get("type") == "browser_set_cookies_response":
                request_id = msg.get("request_id", "")
                logger.info(
                    "Agent %s: GOT browser_set_cookies_response reqId=%s ok=%s fail=%s",
                    agent_id, request_id, msg.get("ok"), msg.get("fail"),
                )
                fut = _browser_pending.pop(request_id, None)
                if fut and not fut.done():
                    fut.set_result(msg)

            elif msg.get("type") == "command":
                cmd = msg.get("command", "")
                if cmd == "abort":
                    agentic_abort.set()
                    try:
                        await websocket.send_json({
                            "type": "status",
                            "content": "Stopping...",
                        })
                    except Exception:
                        pass
                    logger.info(
                        "Agent %s: abort received via concurrent receive",
                        agent_id,
                    )
            elif msg.get("type") == "user_answer":
                content = msg.get("content", "").strip()
                if content:
                    copilot_queue.put_nowait(content)
                    logger.info(
                        "Agent %s: user_answer received: %.80s",
                        agent_id, content,
                    )

            else:
                content = msg.get("content", "").strip()
                if content:
                    copilot_queue.put_nowait(content)
                    logger.info(
                        "Agent %s: co-pilot message queued: %.80s",
                        agent_id, content,
                    )
                    try:
                        await websocket.send_json({
                            "type": "copilot_ack",
                            "content": content,
                            "message": "Message received.",
                        })
                    except Exception:
                        pass

        return task.result()

    except BaseException:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        raise
