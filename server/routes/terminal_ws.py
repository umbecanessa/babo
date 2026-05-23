"""Terminal WebSocket endpoint for the NLS IDE.

Provides a persistent shell session over WebSocket so the Angular
frontend can embed a real terminal (via xterm.js) without Electron.

Protocol::

    Client -> Server (JSON):
        {"type": "input", "data": "ls -la\\r"}
        {"type": "resize", "cols": 120, "rows": 30}
        {"type": "cwd", "path": "/home/user/project"}

    Server -> Client (JSON):
        {"type": "output", "data": "..."}
        {"type": "exit", "code": 0}
        {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import signal
import sys
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["terminal"])

# Track active terminal sessions
_terminals: dict[str, dict[str, Any]] = {}


@router.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    """WebSocket terminal endpoint -- persistent shell session."""
    await websocket.accept()

    session_id = str(id(websocket))
    process = None

    try:
        # Determine shell
        if platform.system() == "Windows":
            shell = os.environ.get("COMSPEC", "cmd.exe")
            shell_args: list[str] = []
        else:
            shell = os.environ.get("SHELL", "/bin/bash")
            shell_args = ["-i"]  # interactive

        cwd = os.getcwd()

        # Spawn shell process
        process = await asyncio.create_subprocess_exec(
            shell, *shell_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env={**os.environ, "TERM": "xterm-256color", "PYTHONIOENCODING": "utf-8"},
        )

        _terminals[session_id] = {"process": process, "cwd": cwd}

        logger.info("Terminal session started: %s (PID %d)", session_id, process.pid)

        # Task: read stdout and forward to WebSocket
        async def read_stdout():
            try:
                assert process.stdout is not None
                while True:
                    data = await process.stdout.read(4096)
                    if not data:
                        break
                    try:
                        text = data.decode("utf-8", errors="replace")
                        await websocket.send_json({"type": "output", "data": text})
                    except Exception:
                        break
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("stdout reader ended: %s", exc)

        stdout_task = asyncio.create_task(read_stdout())

        # Main loop: receive client input
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "input", "data": raw}

            msg_type = msg.get("type", "input")

            if msg_type == "input":
                data = msg.get("data", "")
                if process.stdin and not process.stdin.is_closing():
                    process.stdin.write(data.encode("utf-8"))
                    await process.stdin.drain()

            elif msg_type == "resize":
                # Note: resize only works with PTY (not asyncio.subprocess)
                # For now, we accept but ignore resize messages.
                # A future enhancement would use a PTY library.
                pass

            elif msg_type == "cwd":
                # Change working directory by sending cd command
                new_cwd = msg.get("path", "")
                if new_cwd and process.stdin and not process.stdin.is_closing():
                    cd_cmd = f"cd \"{new_cwd}\"\n"
                    process.stdin.write(cd_cmd.encode("utf-8"))
                    await process.stdin.drain()

    except WebSocketDisconnect:
        logger.info("Terminal WebSocket disconnected: %s", session_id)
    except Exception as exc:
        logger.error("Terminal error (%s): %s", session_id, exc, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        # Clean up
        if session_id in _terminals:
            del _terminals[session_id]

        if process and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

            exit_code = process.returncode or 0
            try:
                await websocket.send_json({"type": "exit", "code": exit_code})
            except Exception:
                pass

            logger.info("Terminal session ended: %s (exit %s)", session_id, exit_code)

        # Cancel stdout reader
        try:
            stdout_task.cancel()  # type: ignore[possibly-undefined]
        except (NameError, Exception):
            pass
