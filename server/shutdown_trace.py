"""Record who initiated runtime shutdown (signals, HTTP, agent restart).

Every path logs a line prefixed with ``SHUTDOWN_TRACE`` so incidents in
runtime.log can be correlated with Electron-side initiators.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import sys
import threading
import time
import traceback
from typing import Any

logger = logging.getLogger("babo.shutdown_trace")

_LOCK = threading.Lock()
_INITIATOR: str | None = None
_DETAIL: dict[str, Any] = {}
_SIGNAL_LOGGED: set[int] = set()
_HANDLERS_INSTALLED = False
_UVICORN_PATCHED = False
_ALLOW_SIGINT = False

_INTENTIONAL_INITIATORS = frozenset({
    "http:admin_shutdown",
    "agent:request_restart_approved",
})


def _process_context() -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "pid": os.getpid(),
        "platform": sys.platform,
    }
    if sys.platform == "win32":
        try:
            import subprocess

            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-CimInstance Win32_Process -Filter "
                    f"'ProcessId={os.getpid()}').ParentProcessId",
                ],
                stderr=subprocess.DEVNULL,
                timeout=3,
                text=True,
            ).strip()
            if out.isdigit():
                ctx["ppid"] = int(out)
        except Exception:
            pass
    else:
        try:
            ctx["ppid"] = os.getppid()
        except Exception:
            pass
    try:
        ctx["argv"] = " ".join(sys.argv[:8])
    except Exception:
        pass
    return ctx


def _format_stack(frame: Any, *, max_frames: int = 6) -> str:
    if frame is None:
        return ""
    lines = traceback.format_stack(frame, limit=max_frames)
    compact = " | ".join(ln.strip() for ln in lines if ln.strip())
    return compact[:1200]


def agentic_loops_active() -> int:
    """Best-effort count of in-flight agentic loops (disk recovery markers)."""
    try:
        from server.config import get_settings
        from nls.agentic.active_loop_marker import count_active_agentic_loops

        return count_active_agentic_loops(get_settings().agents_dir)
    except Exception:
        return 0


def record_initiator(source: str, **detail: Any) -> None:
    """Log and remember the shutdown initiator (first call wins)."""
    global _INITIATOR, _DETAIL
    with _LOCK:
        first = _INITIATOR is None
        if first:
            _INITIATOR = source
            _DETAIL = {
                **detail,
                "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                **_process_context(),
            }
        payload = detail if not first else _DETAIL
    extra = " ".join(f"{k}={v!r}" for k, v in payload.items() if v is not None)
    if first:
        logger.warning(
            "SHUTDOWN_TRACE initiator=%s %s",
            source,
            extra,
        )
    else:
        logger.warning(
            "SHUTDOWN_TRACE duplicate initiator=%s (kept=%s) %s",
            source,
            _INITIATOR,
            extra,
        )


def get_initiator() -> tuple[str | None, dict[str, Any]]:
    with _LOCK:
        return _INITIATOR, dict(_DETAIL)


def format_initiator_summary() -> str:
    source, detail = get_initiator()
    if not source:
        return "initiator=unknown (no SHUTDOWN_TRACE recorded before teardown)"
    parts = [f"initiator={source}"]
    for key in ("client", "user_agent", "review_id", "signal", "ppid", "pid"):
        if key in detail:
            parts.append(f"{key}={detail[key]}")
    return " ".join(parts)


def allow_sigint_shutdown() -> None:
    """Next SIGINT is intentional (admin shutdown / approved restart)."""
    global _ALLOW_SIGINT
    _ALLOW_SIGINT = True


def request_sigint_exit() -> None:
    """Raise SIGINT so uvicorn runs lifespan teardown (after record_initiator)."""
    allow_sigint_shutdown()
    os.kill(os.getpid(), signal.SIGINT)


def _should_suppress_sigint(signum: int) -> bool:
    if signum not in (signal.SIGINT, getattr(signal, "SIGBREAK", -1)):
        return False
    with _LOCK:
        if _ALLOW_SIGINT:
            return False
        source = _INITIATOR
    if source in _INTENTIONAL_INITIATORS:
        return False
    active = agentic_loops_active()
    if active <= 0:
        return False
    record_initiator(
        "signal:SIGINT_suppressed",
        signal=signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum),
        agentic_loops=active,
    )
    logger.warning(
        "SHUTDOWN_TRACE ignoring external SIGINT during agentic work "
        "(active_loops=%s initiator=%s)",
        active,
        source or "none",
    )
    return True


def _record_signal(signum: int, frame: Any) -> None:
    try:
        sig_name = signal.Signals(signum).name
    except (ValueError, AttributeError):
        sig_name = str(signum)
    stack = _format_stack(frame)
    with _LOCK:
        already = signum in _SIGNAL_LOGGED
        _SIGNAL_LOGGED.add(signum)
    if not already:
        record_initiator(
            f"signal:{sig_name}",
            signal=sig_name,
            stack=stack or None,
        )


def _signal_handler(signum: int, frame: Any) -> None:
    if _should_suppress_sigint(signum):
        return
    _record_signal(signum, frame)


def _atexit_handler() -> None:
    source, _ = get_initiator()
    if source is None:
        logger.warning(
            "SHUTDOWN_TRACE process exiting with no initiator recorded "
            "(context=%s)",
            _process_context(),
        )


def _patch_uvicorn_signal_handler() -> None:
    """Ensure SIGINT is traced before uvicorn begins shutdown."""
    global _UVICORN_PATCHED
    with _LOCK:
        if _UVICORN_PATCHED:
            return
        _UVICORN_PATCHED = True

    try:
        from uvicorn.server import Server
    except ImportError:
        return

    if getattr(Server, "_babo_shutdown_trace_patched", False):
        return

    _orig_handle_exit = Server.handle_exit

    def handle_exit(self, sig: int, frame: Any) -> None:  # type: ignore[no-untyped-def]
        if _should_suppress_sigint(sig):
            return
        _record_signal(sig, frame)
        global _ALLOW_SIGINT
        with _LOCK:
            if _ALLOW_SIGINT:
                _ALLOW_SIGINT = False
        return _orig_handle_exit(self, sig, frame)

    Server.handle_exit = handle_exit  # type: ignore[method-assign]
    Server._babo_shutdown_trace_patched = True
    logger.info("SHUTDOWN_TRACE patched uvicorn Server.handle_exit")


def install_shutdown_tracing() -> None:
    """Register signal handlers and atexit hook (idempotent)."""
    global _HANDLERS_INSTALLED
    with _LOCK:
        if _HANDLERS_INSTALLED:
            _patch_uvicorn_signal_handler()
            return
        _HANDLERS_INSTALLED = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError) as exc:
            logger.debug("SHUTDOWN_TRACE: could not install handler for %s: %s", sig, exc)

    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _signal_handler)
        except (ValueError, OSError):
            pass

    atexit.register(_atexit_handler)
    _patch_uvicorn_signal_handler()
    logger.info(
        "SHUTDOWN_TRACE tracing enabled %s",
        " ".join(f"{k}={v}" for k, v in _process_context().items()),
    )
