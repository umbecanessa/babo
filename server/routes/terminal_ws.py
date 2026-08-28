"""Terminal WebSocket endpoint for the NLS IDE.

Provides a persistent shell session over WebSocket so the Angular
frontend can embed a real terminal (via xterm.js).

When ``agent_id`` and ``workspace`` query params are supplied, the UI
mirrors the same PTY session the agent ``bash()`` tool uses (read-only).
The agent session is created on first ``bash()`` — never pre-created
with the wrong host environment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from server.services.agent_pty_pool import get_agent_pty_pool
from server.services.pty_workspace import normalize_pty_workspace
from server.services.pty_session import PtySession

logger = logging.getLogger(__name__)


def _agents_dir():
    try:
        from server.config import get_settings

        return get_settings().agents_dir
    except Exception:
        return None

router = APIRouter(tags=["terminal"])

_AGENT_ATTACH_POLL_SEC = 1.0
_AGENT_ATTACH_WAIT_SEC = 120.0

_standalone_sessions: dict[str, PtySession] = {}


async def _forward_output(
    websocket: WebSocket,
    queue: asyncio.Queue[str],
    *,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            data = await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        try:
            await websocket.send_json({"type": "output", "data": data})
        except Exception:
            break


async def _wait_for_agent_session(
    agent_id: str,
    workspace: str,
    *,
    stop: asyncio.Event,
) -> PtySession | None:
    """Poll until bash() creates the agent PTY (or stop is set)."""
    pool = get_agent_pty_pool()
    deadline = asyncio.get_running_loop().time() + _AGENT_ATTACH_WAIT_SEC
    while not stop.is_set():
        session = pool.get_existing(agent_id, workspace)
        if session is not None:
            return session
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(_AGENT_ATTACH_POLL_SEC)
    return None


@router.websocket("/ws/terminal")
async def websocket_terminal(
    websocket: WebSocket,
    agent_id: str = Query(default=""),
    workspace: str = Query(default=""),
):
    """WebSocket terminal — standalone shell or read-only mirror of agent PTY."""
    await websocket.accept()

    session_id = str(id(websocket))
    session: PtySession | None = None
    output_queue: asyncio.Queue[str] | None = None
    stop = asyncio.Event()
    forward_task: asyncio.Task[None] | None = None
    attach_task: asyncio.Task[PtySession | None] | None = None
    exit_code = 0
    attached_agent = (agent_id or "").strip()
    mirror_mode = bool(attached_agent and workspace)
    norm_workspace = (
        normalize_pty_workspace(attached_agent, workspace, agents_dir=_agents_dir())
        if mirror_mode else ""
    )

    try:
        if mirror_mode:
            pool = get_agent_pty_pool()
            session = pool.get_existing(attached_agent, norm_workspace)
            await websocket.send_json({
                "type": "ready",
                "mode": "mirror" if session else "waiting",
                "message": (
                    "Mirroring agent shell."
                    if session
                    else "Waiting for agent shell (starts on first bash command)…"
                ),
            })
            if session is None:
                attach_task = asyncio.create_task(
                    _wait_for_agent_session(
                        attached_agent, norm_workspace, stop=stop,
                    ),
                )
                session = await attach_task
                attach_task = None
                if session is None:
                    try:
                        await websocket.send_json({
                            "type": "error",
                            "message": (
                                "Agent shell not started within "
                                f"{int(_AGENT_ATTACH_WAIT_SEC)}s"
                            ),
                        })
                    except Exception:
                        pass
                    return
                await websocket.send_json({"type": "mode", "mode": "mirror"})
            output_queue = await session.subscribe()
            forward_task = asyncio.create_task(
                _forward_output(websocket, output_queue, stop=stop),
            )
            logger.info(
                "Terminal mirroring agent PTY agent=%s workspace=%s",
                attached_agent,
                norm_workspace,
            )
        else:
            cwd = os.getcwd()
            session = PtySession(cwd=cwd, env=dict(os.environ))
            await session.start()
            _standalone_sessions[session_id] = session
            output_queue = await session.subscribe()
            forward_task = asyncio.create_task(
                _forward_output(websocket, output_queue, stop=stop),
            )
            await websocket.send_json({"type": "ready", "mode": "standalone"})
            logger.info("Terminal standalone session started: %s", session_id)

        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "input", "data": raw}

            msg_type = msg.get("type", "input")
            if not session:
                continue

            if msg_type == "input":
                if mirror_mode:
                    continue
                await session.write(msg.get("data", ""))

            elif msg_type == "resize":
                if mirror_mode:
                    continue
                cols = int(msg.get("cols") or 0)
                rows = int(msg.get("rows") or 0)
                await session.resize(cols, rows)

            elif msg_type == "cwd":
                if mirror_mode:
                    continue
                new_cwd = msg.get("path", "")
                if new_cwd:
                    await session.set_cwd(new_cwd)

    except WebSocketDisconnect:
        logger.info("Terminal WebSocket disconnected: %s", session_id)
    except Exception as exc:
        logger.error("Terminal error (%s): %s", session_id, exc, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        stop.set()
        if attach_task and not attach_task.done():
            attach_task.cancel()
            try:
                await attach_task
            except asyncio.CancelledError:
                pass
        if forward_task:
            forward_task.cancel()
            try:
                await forward_task
            except asyncio.CancelledError:
                pass
        if session and output_queue is not None:
            session.unsubscribe(output_queue)
        if session_id in _standalone_sessions:
            standalone = _standalone_sessions.pop(session_id)
            try:
                exit_code = await standalone.close()
            except Exception as exc:
                logger.debug("Terminal close error: %s", exc)
        try:
            await websocket.send_json({"type": "exit", "code": exit_code})
        except Exception:
            pass
        logger.info("Terminal session ended: %s (exit %s)", session_id, exit_code)
