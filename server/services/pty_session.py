"""Pseudo-terminal sessions for agent shell commands and UI terminals.

Agent ``bash()`` runs commands in a persistent PTY shell (ConPTY on Windows,
POSIX PTY elsewhere) so dev servers and installs live in an isolated process
tree — the same model as Cursor/VS Code integrated terminals.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

MARKER_EXIT = "__NLS_EXIT__"
MARKER_CWD = "__NLS_CWD__"
_MAX_BUFFER_CHARS = 256_000
_SUBSCRIBER_REPLAY_CHARS = 32_000
_SYNC_PROBE_TIMEOUT = 5.0
_POST_INTERRUPT_TIMEOUT = 4.0

_ENV_SYNC_KEYS = (
    "PATH",
    "VIRTUAL_ENV",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GIT_CONFIG_GLOBAL",
    "GH_CONFIG_DIR",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\].*?(?:\x07|\x1b\\)")
_CWD_SENTINEL_RE = re.compile(
    rf"{re.escape(MARKER_CWD)}([^{re.escape(MARKER_CWD)}]+){re.escape(MARKER_CWD)}",
)
_EXIT_SENTINEL_RE = re.compile(rf"{re.escape(MARKER_EXIT)}(\d+)", re.MULTILINE)


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def wrap_command_for_pty(command: str, *, windows: bool | None = None) -> str:
    """Append exit-code and CWD sentinels to a command block."""
    win = platform.system() == "Windows" if windows is None else windows
    if win:
        from nls.platform_shell import _PS_UTF8_PREAMBLE

        body = command.rstrip()
        return (
            f"{_PS_UTF8_PREAMBLE}{body}\n"
            # Prefer $LASTEXITCODE; fall back to $? so native cmdlets still yield a digit.
            f'$__nls_code = if ($null -ne $LASTEXITCODE) {{ $LASTEXITCODE }} '
            f'elseif ($?) {{ 0 }} else {{ 1 }}; '
            f'Write-Output "{MARKER_EXIT}$__nls_code"\n'
            f'Write-Output "{MARKER_CWD}$(Get-Location){MARKER_CWD}"'
        )
    body = command.rstrip()
    return (
        f"{body}\n"
        f'echo "{MARKER_EXIT}$?"\n'
        f'echo "{MARKER_CWD}$(pwd){MARKER_CWD}"'
    )


def _cd_command(path: str) -> str:
    if platform.system() == "Windows":
        return f"Set-Location -LiteralPath {shlex.quote(path)}"
    return f"cd {shlex.quote(path)}"


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_cwd_from_output(text: str) -> str:
    match = _CWD_SENTINEL_RE.search(strip_ansi(text))
    if not match:
        return ""
    return match.group(1).strip()


def build_env_export_script(pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return ""
    if platform.system() == "Windows":
        lines = [f"$env:{key} = {_ps_quote(value)}" for key, value in pairs]
        return "\n".join(lines)
    lines = [f"export {key}={shlex.quote(value)}" for key, value in pairs]
    return "\n".join(lines)


@dataclass
class PtyCommandResult:
    output: str
    exit_code: int | None
    timed_out: bool = False
    aborted: bool = False
    early_reason: str | None = None
    shell_pid: int | None = None


@dataclass
class PtySession:
    """One persistent interactive shell bound to a working directory."""

    cwd: str
    env: dict[str, str]
    cols: int = 120
    rows: int = 30
    session_key: str = ""
    _output_buffer: str = field(default="", init=False, repr=False)
    _output_event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _subscribers: list[asyncio.Queue[str]] = field(default_factory=list, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)
    _command_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _impl: Any = field(default=None, init=False, repr=False)
    _read_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def start(self) -> None:
        if self._started:
            return
        if platform.system() == "Windows":
            self._impl = await _WindowsPtyBackend.create(
                self.cwd, self.env, self.cols, self.rows,
            )
        else:
            self._impl = await _UnixPtyBackend.create(
                self.cwd, self.env, self.cols, self.rows,
            )
        self._read_task = asyncio.create_task(self._read_loop())
        self._started = True
        logger.info(
            "PTY session started key=%s cwd=%s pid=%s",
            self.session_key or "?",
            self.cwd,
            self.shell_pid,
        )

    @property
    def shell_pid(self) -> int | None:
        impl = self._impl
        if impl is None:
            return None
        return getattr(impl, "pid", None)

    async def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=512)
        self._subscribers.append(queue)
        if self._output_buffer:
            replay = self._output_buffer[-_SUBSCRIBER_REPLAY_CHARS:]
            try:
                queue.put_nowait(replay)
            except asyncio.QueueFull:
                pass
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    async def write(self, data: str) -> None:
        if not self._impl:
            raise RuntimeError("PTY session not started")
        await self._impl.write(data)

    async def resize(self, cols: int, rows: int) -> None:
        if not self._impl or cols <= 0 or rows <= 0:
            return
        self.cols = cols
        self.rows = rows
        await self._impl.resize(cols, rows)

    async def set_cwd(self, path: str) -> bool:
        """Change directory and verify via CWD sentinel."""
        if not path:
            return False
        result = await self.run_command(_cd_command(path), timeout=15.0)
        parsed = parse_cwd_from_output(result.output)
        if parsed:
            self.cwd = parsed
            return True
        self.cwd = path
        return result.exit_code == 0

    async def sync_env(self, new_env: dict[str, str]) -> None:
        """Export changed env vars into the live PTY shell."""
        previous = dict(self.env)
        self.env.update(new_env)
        changed: list[tuple[str, str]] = []
        for key in _ENV_SYNC_KEYS:
            old_val = previous.get(key)
            new_val = self.env.get(key)
            if new_val is not None and new_val != old_val:
                changed.append((key, new_val))
        if not changed:
            return
        script = build_env_export_script(changed)
        if not script:
            return
        await self.run_command(script, timeout=10.0)

    async def restart_shell(self) -> None:
        """Recreate the PTY shell after an unrecoverable timeout."""
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        if self._impl:
            try:
                await self._impl.close()
            except Exception as exc:
                logger.debug("PTY restart close (%s): %s", self.session_key, exc)
            self._impl = None
        self._started = False
        self._output_buffer = ""
        await self.start()

    async def close(self) -> int:
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        code = 0
        if self._impl:
            code = await self._impl.close()
            self._impl = None
        self._started = False
        return code

    async def run_command(
        self,
        command: str,
        *,
        timeout: float | None,
        abort_event: asyncio.Event | None = None,
        on_chunk: Callable[[str], Awaitable[str | None] | str | None] | None = None,
        completion_re: re.Pattern[str] | None = None,
    ) -> PtyCommandResult:
        """Run *command* in the PTY shell and wait for exit/CWD sentinels."""
        async with self._command_lock:
            if not self._started:
                await self.start()

            marker_re = completion_re or re.compile(
                rf"{re.escape(MARKER_EXIT)}(\d+)",
                re.MULTILINE,
            )
            baseline_len = len(self._output_buffer)
            wrapped = wrap_command_for_pty(command)
            suffix = "\r\n" if platform.system() == "Windows" else "\n"
            await self.write(wrapped + suffix)

            deadline = time.monotonic() + timeout if timeout else None
            early_reason: str | None = None
            while True:
                chunk = self._output_buffer[baseline_len:]
                plain = strip_ansi(chunk)
                if marker_re.search(plain):
                    break
                if abort_event and abort_event.is_set():
                    self._trim_output_buffer()
                    return PtyCommandResult(
                        output=plain,
                        exit_code=None,
                        aborted=True,
                        shell_pid=self.shell_pid,
                    )
                if deadline is not None and time.monotonic() >= deadline:
                    synced = await self._interrupt_and_sync_shell(baseline_len)
                    if not synced:
                        logger.warning(
                            "PTY shell still busy after timeout; restarting key=%s",
                            self.session_key or "?",
                        )
                        await self.restart_shell()
                    plain = strip_ansi(self._output_buffer[baseline_len:])
                    self._trim_output_buffer()
                    return PtyCommandResult(
                        output=plain,
                        exit_code=None,
                        timed_out=True,
                        shell_pid=self.shell_pid,
                    )
                if on_chunk and plain:
                    try:
                        result = on_chunk(plain)
                        if asyncio.iscoroutine(result):
                            result = await result
                        if result in ("daemon", "interactive"):
                            early_reason = result
                            break
                    except Exception:
                        pass
                try:
                    wait_for = 0.25
                    if deadline is not None:
                        wait_for = min(0.25, max(0.01, deadline - time.monotonic()))
                    await asyncio.wait_for(
                        self._wait_for_output(baseline_len),
                        timeout=wait_for,
                    )
                except asyncio.TimeoutError:
                    pass

            full = strip_ansi(self._output_buffer[baseline_len:])
            exit_code: int | None = None
            match = marker_re.search(full)
            if match:
                try:
                    exit_code = int(match.group(1))
                except ValueError:
                    exit_code = None
            parsed_cwd = parse_cwd_from_output(full)
            if parsed_cwd:
                self.cwd = parsed_cwd
            self._trim_output_buffer()
            return PtyCommandResult(
                output=full,
                exit_code=exit_code,
                early_reason=early_reason,
                shell_pid=self.shell_pid,
            )

    async def _interrupt_and_sync_shell(self, baseline_len: int) -> bool:
        """Send Ctrl+C and synchronize with a no-op sentinel command."""
        for _ in range(2):
            try:
                await self.write("\x03")
            except Exception:
                pass
            await asyncio.sleep(0.35)
            if _EXIT_SENTINEL_RE.search(
                strip_ansi(self._output_buffer[baseline_len:]),
            ):
                self._trim_output_buffer()
                return True
        probe = "Set-Location -LiteralPath ." if platform.system() == "Windows" else "cd ."
        probe_baseline = len(self._output_buffer)
        wrapped = wrap_command_for_pty(probe)
        suffix = "\r\n" if platform.system() == "Windows" else "\n"
        try:
            await self.write(wrapped + suffix)
        except Exception:
            return False
        deadline = time.monotonic() + _POST_INTERRUPT_TIMEOUT
        while time.monotonic() < deadline:
            chunk = strip_ansi(self._output_buffer[probe_baseline:])
            if _EXIT_SENTINEL_RE.search(chunk):
                self._trim_output_buffer()
                return True
            await asyncio.sleep(0.05)
        return False

    def _trim_output_buffer(self) -> None:
        """Prevent unbounded growth across long agent sessions."""
        if len(self._output_buffer) > _MAX_BUFFER_CHARS:
            self._output_buffer = self._output_buffer[-_MAX_BUFFER_CHARS:]

    async def _wait_for_output(self, baseline_len: int) -> None:
        if len(self._output_buffer) > baseline_len:
            return
        self._output_event.clear()
        await self._output_event.wait()

    async def _read_loop(self) -> None:
        while self._impl:
            try:
                data = await self._impl.read(4096)
            except Exception as exc:
                logger.debug("PTY read ended (%s): %s", self.session_key, exc)
                break
            if not data:
                await asyncio.sleep(0.02)
                continue
            self._output_buffer += data
            self._output_event.set()
            dead: list[asyncio.Queue[str]] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    dead.append(queue)
            for queue in dead:
                self.unsubscribe(queue)


class _UnixPtyBackend:
    def __init__(
        self,
        master_fd: int,
        process: asyncio.subprocess.Process,
        pgid: int,
    ) -> None:
        self._master_fd = master_fd
        self._process = process
        self._pgid = pgid

    @property
    def pid(self) -> int | None:
        return self._process.pid

    @classmethod
    async def create(
        cls, cwd: str, env: dict[str, str], cols: int, rows: int,
    ) -> _UnixPtyBackend:
        import fcntl
        import pty
        import struct
        import termios

        shell = env.get("SHELL") or os.environ.get("SHELL", "/bin/bash")
        master_fd, slave_fd = pty.openpty()
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass
        merged_env = {**os.environ, **env, "TERM": "xterm-256color", "PYTHONIOENCODING": "utf-8"}
        process = await asyncio.create_subprocess_exec(
            shell,
            "-i",
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=merged_env,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        pgid = process.pid or 0
        return cls(master_fd, process, pgid)

    async def write(self, data: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, os.write, self._master_fd, data.encode("utf-8"),
        )

    async def resize(self, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios

        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize),
        )

    async def read(self, size: int) -> str:
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, os.read, self._master_fd, size)
        except OSError:
            return ""
        if not data:
            return ""
        return data.decode("utf-8", errors="replace")

    async def close(self) -> int:
        import signal

        try:
            os.close(self._master_fd)
        except OSError:
            pass
        proc = self._process
        if self._pgid:
            try:
                os.killpg(self._pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
        if proc and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                if self._pgid:
                    try:
                        os.killpg(self._pgid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                proc.kill()
                await proc.wait()
        return proc.returncode if proc else 0


class _WindowsPtyBackend:
    def __init__(self, proc: Any) -> None:
        self._proc = proc

    @property
    def pid(self) -> int | None:
        pid = getattr(self._proc, "pid", None)
        return int(pid) if pid else None

    @classmethod
    async def create(
        cls, cwd: str, env: dict[str, str], cols: int, rows: int,
    ) -> _WindowsPtyBackend:
        try:
            from winpty import PtyProcess
        except ImportError as exc:
            raise RuntimeError(
                "Windows agent shell requires pywinpty. Re-run desktop setup "
                "or: pip install pywinpty"
            ) from exc

        from nls.platform_shell import resolve_powershell_executable

        shell = resolve_powershell_executable()
        merged_env = {**os.environ, **env, "TERM": "xterm-256color", "PYTHONIOENCODING": "utf-8"}
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: PtyProcess.spawn(
                [shell, "-NoLogo", "-ExecutionPolicy", "Bypass"],
                cwd=cwd,
                env=merged_env,
                dimensions=(rows, cols),
            ),
        )
        return cls(proc)

    async def write(self, data: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._proc.write, data)

    async def resize(self, cols: int, rows: int) -> None:
        loop = asyncio.get_running_loop()
        if hasattr(self._proc, "setwinsize"):
            await loop.run_in_executor(None, self._proc.setwinsize, rows, cols)
        elif hasattr(self._proc, "set_size"):
            await loop.run_in_executor(None, self._proc.set_size, cols, rows)

    async def read(self, size: int) -> str:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._proc.read, size)
        except EOFError:
            return ""

    async def close(self) -> int:
        proc = self._proc
        if proc and proc.isalive():
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, proc.terminate, True)
        return 0
