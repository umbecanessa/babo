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


def request_sigint_exit() -> None:
    """Raise SIGINT so uvicorn runs lifespan teardown (after record_initiator)."""
    os.kill(os.getpid(), signal.SIGINT)


def _signal_handler(signum: int, frame: Any) -> None:
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
    # Re-raise default behavior: uvicorn's handler runs after ours if chained;
    # we only log — do not sys.exit() here.


def _atexit_handler() -> None:
    source, _ = get_initiator()
    if source is None:
        logger.warning(
            "SHUTDOWN_TRACE process exiting with no initiator recorded "
            "(context=%s)",
            _process_context(),
        )


def install_shutdown_tracing() -> None:
    """Register signal handlers and atexit hook (idempotent)."""
    global _HANDLERS_INSTALLED
    with _LOCK:
        if _HANDLERS_INSTALLED:
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
    logger.info(
        "SHUTDOWN_TRACE tracing enabled %s",
        " ".join(f"{k}={v}" for k, v in _process_context().items()),
    )
