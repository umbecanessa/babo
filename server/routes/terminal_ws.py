"""Terminal WebSocket endpoint for the NLS IDE.

Provides a persistent shell session over WebSocket so the Angular
frontend can embed a real terminal (via xterm.js) without Electron.

Protocol::

    Client -> Server (JSON):
        {"type": "input", "data": "ls -la\\r"}
        {"type": "resize", "cols": 120, "rows": 30}
        {"type": "cwd", "path": "/home/user/project"}

    Server -> Client (JSON):
        {"type": "ready"}
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
import shlex
from typing import Any, Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["terminal"])

# Track active terminal sessions
_terminals: dict[str, dict[str, Any]] = {}


class _TerminalSession(Protocol):
    async def start(self) -> None: ...
    async def write(self, data: str) -> None: ...
    async def resize(self, cols: int, rows: int) -> None: ...
    async def close(self) -> int: ...


class _UnixPtySession:
    """Interactive shell via POSIX pseudo-terminal."""

    def __init__(self, cwd: str, cols: int, rows: int) -> None:
        self.cwd = cwd
        self.cols = max(cols, 2)
        self.rows = max(rows, 2)
        self._master_fd: int | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._websocket: WebSocket | None = None

    async def bind(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def start(self) -> None:
        import pty
        import termios
        import struct
        import fcntl

        shell = os.environ.get("SHELL", "/bin/bash")
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd

        try:
            winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

        env = {
            **os.environ,
            "TERM": "xterm-256color",
            "PYTHONIOENCODING": "utf-8",
        }

        self._process = await asyncio.create_subprocess_exec(
            shell,
            "-i",
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self.cwd,
            env=env,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)

        self._read_task = asyncio.create_task(self._read_loop())

    async def write(self, data: str) -> None:
        if self._master_fd is None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.write, self._master_fd, data.encode("utf-8"))

    async def resize(self, cols: int, rows: int) -> None:
        if self._master_fd is None or cols <= 0 or rows <= 0:
            return
        import fcntl
        import struct
        import termios

        self.cols = cols
        self.rows = rows
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize),
            )
        except OSError:
            pass

    async def close(self) -> int:
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        proc = self._process
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        return proc.returncode if proc else 0

    async def _read_loop(self) -> None:
        assert self._master_fd is not None
        loop = asyncio.get_running_loop()
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, self._master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            if self._websocket:
                try:
                    await self._websocket.send_json(
                        {
                            "type": "output",
                            "data": data.decode("utf-8", errors="replace"),
                        }
                    )
                except Exception:
                    break


class _WindowsPtySession:
    """Interactive shell via ConPTY (pywinpty)."""

    def __init__(self, cwd: str, cols: int, rows: int) -> None:
        self.cwd = cwd
        self.cols = max(cols, 2)
        self.rows = max(rows, 2)
        self._proc: Any = None
        self._read_task: asyncio.Task[None] | None = None
        self._websocket: WebSocket | None = None

    async def bind(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def start(self) -> None:
        try:
            from winpty import PtyProcess
        except ImportError as exc:
            raise RuntimeError(
                "Interactive shell on Windows requires the pywinpty package. "
                "Re-run desktop setup or: pip install pywinpty"
            ) from exc

        shell = os.environ.get("COMSPEC", "cmd.exe")
        env = {
            **os.environ,
            "TERM": "xterm-256color",
            "PYTHONIOENCODING": "utf-8",
        }
        loop = asyncio.get_running_loop()
        self._proc = await loop.run_in_executor(
            None,
            lambda: PtyProcess.spawn(
                shell,
                cwd=self.cwd,
                env=env,
                dimensions=(self.rows, self.cols),
            ),
        )
        self._read_task = asyncio.create_task(self._read_loop())

    async def write(self, data: str) -> None:
        if not self._proc:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._proc.write, data)

    async def resize(self, cols: int, rows: int) -> None:
        if not self._proc or cols <= 0 or rows <= 0:
            return
        self.cols = cols
        self.rows = rows
        loop = asyncio.get_running_loop()
        if hasattr(self._proc, "setwinsize"):
            await loop.run_in_executor(None, self._proc.setwinsize, rows, cols)
        elif hasattr(self._proc, "set_size"):
            await loop.run_in_executor(None, self._proc.set_size, cols, rows)

    async def close(self) -> int:
        if self._read_task:
            self._read_task.cancel()
            try:
                await asyncio.wait_for(self._read_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        proc = self._proc
        self._proc = None
        if not proc:
            return 0
        loop = asyncio.get_running_loop()
        if proc.isalive():
            await loop.run_in_executor(None, proc.terminate, True)
        return 0

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            proc = self._proc
            if not proc or not proc.isalive():
                break
            try:
                data = await loop.run_in_executor(None, proc.read, 4096)
            except EOFError:
                break
            except Exception as exc:
                logger.debug("Windows PTY read ended: %s", exc)
                break
            if not data:
                await asyncio.sleep(0.02)
                continue
            if self._websocket:
                try:
                    await self._websocket.send_json({"type": "output", "data": data})
                except Exception:
                    break


def _create_session(cwd: str, cols: int = 120, rows: int = 30) -> _TerminalSession:
    if platform.system() == "Windows":
        return _WindowsPtySession(cwd, cols, rows)
    return _UnixPtySession(cwd, cols, rows)


def _cd_command(path: str) -> str:
    if platform.system() == "Windows":
        return f'cd /d {shlex.quote(path)}\r'
    return f'cd {shlex.quote(path)}\n'


@router.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    """WebSocket terminal endpoint -- persistent shell session."""
    await websocket.accept()

    session_id = str(id(websocket))
    session: _TerminalSession | None = None
    exit_code = 0

    try:
        cwd = os.getcwd()
        session = _create_session(cwd)
        await session.bind(websocket)
        await session.start()
        _terminals[session_id] = {"session": session, "cwd": cwd}
        logger.info("Terminal session started: %s", session_id)
        await websocket.send_json({"type": "ready"})

        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "input", "data": raw}

            msg_type = msg.get("type", "input")

            if msg_type == "input":
                await session.write(msg.get("data", ""))

            elif msg_type == "resize":
                cols = int(msg.get("cols") or 0)
                rows = int(msg.get("rows") or 0)
                await session.resize(cols, rows)

            elif msg_type == "cwd":
                new_cwd = msg.get("path", "")
                if new_cwd:
                    await session.write(_cd_command(new_cwd))

    except WebSocketDisconnect:
        logger.info("Terminal WebSocket disconnected: %s", session_id)
    except Exception as exc:
        logger.error("Terminal error (%s): %s", session_id, exc, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if session_id in _terminals:
            del _terminals[session_id]

        if session:
            try:
                exit_code = await session.close()
            except Exception as exc:
                logger.debug("Terminal close error: %s", exc)

        try:
            await websocket.send_json({"type": "exit", "code": exit_code})
        except Exception:
            pass

        logger.info("Terminal session ended: %s (exit %s)", session_id, exit_code)
