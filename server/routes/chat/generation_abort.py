"""Concurrent WebSocket receive for aborting in-flight chat generation."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


@asynccontextmanager
async def listen_for_generation_abort(
    websocket: WebSocket,
    agent_id: str,
    abort: asyncio.Event,
):
    """Listen for ``abort`` commands while a chat stream is running."""
    abort.clear()
    websocket.state.generation_running = True

    async def _listen() -> None:
        try:
            while not abort.is_set():
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(), timeout=0.5,
                    )
                except asyncio.TimeoutError:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif (
                    msg.get("type") == "command"
                    and msg.get("command") == "abort"
                ):
                    abort.set()
                    try:
                        await websocket.send_json({
                            "type": "status",
                            "content": "Stopping response...",
                        })
                    except Exception:
                        pass
                    logger.info(
                        "Agent %s: generation abort requested", agent_id,
                    )
        except asyncio.CancelledError:
            raise
        except WebSocketDisconnect:
            abort.set()
            raise

    task = asyncio.create_task(_listen())
    try:
        yield abort
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception:
            pass
        websocket.state.generation_running = False
