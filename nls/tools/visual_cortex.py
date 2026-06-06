"""NLS Visual Cortex -- Continuous desktop visual awareness.

The agent's "eyes."  Captures the screen continuously, processes frames
through a tiered VLM backend, and maintains a ring buffer of structured
visual events.  Two channels run concurrently:

    Agent Workspace  -- Monitors the result of the agent's own actions
                        (browser, shell, MCP tools).  No privacy filter.
                        Higher FPS.  Events enrich tool results.
    User Desktop     -- Ambient awareness of the user's screen.
                        Privacy-filtered, thalamus-gated, lower FPS.
                        Events feed into working memory.

Architecture analogy:
    Screen capture  = Retina
    VLM backend     = Primary visual cortex (V1/V2)
    VisualBuffer    = Iconic / sensory register (pre-conscious)
    Thalamus        = Relevance gating (what reaches conscious attention)
    Working Memory  = Conscious awareness (limited slots)

All pixel data stays local.  Only structured text descriptions ever
leave the visual cortex.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _visual_cortex_strategy() -> str:
    return os.environ.get("NLS_VISUAL_CORTEX_STRATEGY", "").strip()


def _use_local_vlm_worker(strategy: str | None = None) -> bool:
    """True when ambient vision should load Moondream/SmolVLM on this machine."""
    strat = strategy if strategy is not None else _visual_cortex_strategy()
    if strat == "dedicated_vlm_lan":
        return False
    if strat == "dedicated_vlm_local":
        return True
    # Legacy/auto: local subprocess unless a LAN worker is the only backend.
    return True


# ---------------------------------------------------------------------------
# FocusTarget -- agent-controlled capture focus
# ---------------------------------------------------------------------------

class FocusKind:
    BROWSER = "browser"
    DESKTOP = "desktop"
    WINDOW  = "window"
    MONITOR = "monitor"
    OFF     = "off"


@dataclass
class FocusTarget:
    """Describes what the VisualCortex should capture."""

    kind: str = FocusKind.DESKTOP
    window_pattern: str = ""   # used when kind == WINDOW
    monitor_index: int = 0     # used when kind == MONITOR

    @classmethod
    def desktop(cls) -> "FocusTarget":
        return cls(kind=FocusKind.DESKTOP)

    @classmethod
    def browser(cls) -> "FocusTarget":
        return cls(kind=FocusKind.BROWSER)

    @classmethod
    def off(cls) -> "FocusTarget":
        return cls(kind=FocusKind.OFF)

    @classmethod
    def window(cls, pattern: str) -> "FocusTarget":
        return cls(kind=FocusKind.WINDOW, window_pattern=pattern)

    @classmethod
    def monitor(cls, index: int) -> "FocusTarget":
        return cls(kind=FocusKind.MONITOR, monitor_index=index)

    @classmethod
    def parse(cls, spec: str) -> "FocusTarget":
        """Parse a spec string like 'browser', 'desktop', 'window:WhatsApp', 'monitor:1'."""
        spec = spec.strip().lower()
        if spec == "browser":
            return cls.browser()
        if spec == "desktop":
            return cls.desktop()
        if spec == "off":
            return cls.off()
        if spec.startswith("window:"):
            return cls.window(spec[7:].strip())
        if spec.startswith("monitor:"):
            part = spec[8:].strip()
            idx = {"left": 0, "right": 1, "primary": 0, "secondary": 1}.get(part, None)
            if idx is None:
                try:
                    idx = int(part)
                except ValueError:
                    idx = 0
            return cls.monitor(idx)
        return cls.desktop()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class VisualCortexConfig:
    """Configuration loaded from visual_cortex.json."""

    enabled: bool = False
    fps: float = 1.0
    min_fps: float = 0.2
    max_fps: float = 3.0
    model_preference: str = "auto"  # "auto", "2b", "0.5b"

    blocked_apps: list[str] = field(default_factory=list)
    blocked_window_titles: list[str] = field(default_factory=list)

    frame_diff_threshold: float = 0.05
    stability_ms: int = 500
    max_events_per_minute: int = 10
    attention_level: str = "ambient"  # "passive", "ambient", "active"
    buffer_size: int = 60

    # Per-channel FPS overrides (computed dynamically by adaptive FPS)
    agent_fps: float = 2.0
    user_fps: float = 0.5

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualCortexConfig:
        privacy = data.get("privacy", {})
        cfg = cls()
        for k, v in data.items():
            if k == "privacy":
                continue
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        if privacy:
            cfg.blocked_apps = privacy.get("blocked_apps", cfg.blocked_apps)
            cfg.blocked_window_titles = privacy.get(
                "blocked_window_titles", cfg.blocked_window_titles,
            )
        return cfg

    @classmethod
    def load(cls, path: str | Path) -> VisualCortexConfig:
        p = Path(path)
        if p.exists():
            return cls.from_dict(json.loads(p.read_text()))
        return cls()


# ---------------------------------------------------------------------------
# OCR post-processing
# ---------------------------------------------------------------------------

import re as _re


def _clean_ocr(text: str, max_len: int = 400) -> str:
    """Remove repetition loops and cap length of VLM OCR output."""
    if not text:
        return text
    lines = text.split("\n")
    seen: dict[str, int] = {}
    clean: list[str] = []
    for line in lines:
        key = line.strip()
        if not key:
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= 2:
            clean.append(line)
    result = "\n".join(clean).strip()
    result = _re.sub(r"(\|[\s|]*){5,}", "| … |", result)
    result = _re.sub(r"(.{3,}?)\1{3,}", r"\1 …", result)
    if len(result) > max_len:
        result = result[:max_len].rsplit(" ", 1)[0] + "…"
    return result


# ---------------------------------------------------------------------------
# VisualEvent -- the core data structure
# ---------------------------------------------------------------------------

@dataclass
class VisualEvent:
    """A single processed visual observation."""

    timestamp: float
    channel: str              # "agent" or "user"
    app_name: str
    window_title: str
    description: str          # Moondream scene description
    ocr_text: str = ""        # Moondream OCR output
    change_summary: str = ""  # delta from previous frame
    confidence: float = 1.0
    agent_tool: str = ""      # for agent channel: which tool owns this window

    def to_context_line(self) -> str:
        """Render as a compact string for LLM context injection."""
        parts = [f"[VISUAL|{self.channel}]"]
        if self.app_name:
            parts.append(f"App: {self.app_name}")
        if self.window_title:
            parts.append(f"Title: {self.window_title}")
        if self.description:
            parts.append(self.description[:200])
        if self.ocr_text:
            parts.append(f"OCR: {self.ocr_text[:200]}")
        if self.change_summary:
            parts.append(f"Changed: {self.change_summary}")
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# VisualBuffer -- ring buffer (the "iconic memory / sensory register")
# ---------------------------------------------------------------------------

class VisualBuffer:
    """Thread-safe ring buffer of recent VisualEvents.

    The buffer is always being written to (by the capture loop) and can
    be read from at any time (by the agentic loop, thalamus, etc.).
    """

    def __init__(self, max_size: int = 60) -> None:
        self._buffer: deque[VisualEvent] = deque(maxlen=max_size)

    def push(self, event: VisualEvent) -> None:
        self._buffer.append(event)

    @property
    def latest(self) -> VisualEvent | None:
        return self._buffer[-1] if self._buffer else None

    def get_since(self, since: float) -> list[VisualEvent]:
        """Return all events with timestamp >= since."""
        return [e for e in self._buffer if e.timestamp >= since]

    def get_changes(
        self, since: float, until: float | None = None, channel: str | None = None,
    ) -> list[VisualEvent]:
        """Return events in a time window, optionally filtered by channel."""
        results = []
        for e in self._buffer:
            if e.timestamp < since:
                continue
            if until is not None and e.timestamp > until:
                continue
            if channel is not None and e.channel != channel:
                continue
            if e.change_summary:
                results.append(e)
        return results

    def get_latest_change(
        self, channel: str | None = None, since: float | None = None,
    ) -> VisualEvent | None:
        """Return the most recent event with a change, optionally filtered."""
        for e in reversed(self._buffer):
            if since is not None and e.timestamp < since:
                break
            if channel is not None and e.channel != channel:
                continue
            if e.change_summary or e.description:
                return e
        return None

    def get_all(self) -> list[VisualEvent]:
        """Return all events in the buffer (oldest first)."""
        return list(self._buffer)

    def __len__(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()


# ---------------------------------------------------------------------------
# ScreenCapture -- cross-platform screen grabbing
# ---------------------------------------------------------------------------

@dataclass
class WindowInfo:
    """Metadata about the currently active window."""
    app_name: str = ""
    window_title: str = ""
    pid: int = 0


class ScreenCapture:
    """Cross-platform full-desktop screenshot + active window detection."""

    @staticmethod
    def grab() -> Any | None:
        """Capture the full desktop as a PIL Image.  Returns None on failure."""
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            return img.convert("RGB")
        except Exception:
            pass

        # macOS fallback
        if sys.platform == "darwin":
            try:
                import tempfile
                tmp = tempfile.mktemp(suffix=".png")
                subprocess.run(
                    ["screencapture", "-x", "-t", "png", tmp],
                    timeout=5, check=True,
                )
                from PIL import Image
                img = Image.open(tmp).convert("RGB")
                Path(tmp).unlink(missing_ok=True)
                return img
            except Exception as exc:
                logger.debug("macOS screencapture failed: %s", exc)
                return None

        return None

    @staticmethod
    def get_active_window() -> WindowInfo:
        """Detect the currently active/foreground window."""
        info = WindowInfo()

        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                info.window_title = buf.value

                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(
                    hwnd, ctypes.byref(pid),
                )
                info.pid = pid.value

                try:
                    import psutil
                    proc = psutil.Process(pid.value)
                    info.app_name = proc.name()
                except Exception:
                    info.app_name = info.window_title.split(" - ")[-1] if info.window_title else ""

            except Exception as exc:
                logger.debug("Win32 window detection failed: %s", exc)

        elif sys.platform == "darwin":
            try:
                result = subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to get '
                     '{name, unix id, title of first window} of '
                     'first application process whose frontmost is true'],
                    capture_output=True, text=True, timeout=3,
                )
                raw = result.stdout.strip()
                parts = raw.split(", ", 2)
                if parts:
                    info.app_name = parts[0]
                if len(parts) > 1:
                    try:
                        info.pid = int(parts[1])
                    except (ValueError, IndexError):
                        pass
                if len(parts) > 2:
                    info.window_title = parts[2]
            except Exception as exc:
                logger.debug("macOS window detection failed: %s", exc)

        else:  # Linux
            try:
                wid = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True, text=True, timeout=3,
                )
                if wid.returncode == 0:
                    wname = subprocess.run(
                        ["xdotool", "getactivewindow", "getwindowname"],
                        capture_output=True, text=True, timeout=3,
                    )
                    info.window_title = wname.stdout.strip()
                    wpid = subprocess.run(
                        ["xdotool", "getactivewindow", "getwindowpid"],
                        capture_output=True, text=True, timeout=3,
                    )
                    if wpid.returncode == 0:
                        info.pid = int(wpid.stdout.strip())
                        try:
                            import psutil
                            info.app_name = psutil.Process(info.pid).name()
                        except Exception:
                            pass
            except Exception as exc:
                logger.debug("Linux window detection failed: %s", exc)

        return info

    @staticmethod
    def downscale(image: Any, max_width: int = 1024, max_height: int = 768) -> Any:
        """Downscale image to fit within max dimensions for VLM inference."""
        w, h = image.size
        if w <= max_width and h <= max_height:
            return image
        ratio = min(max_width / w, max_height / h)
        new_w, new_h = int(w * ratio), int(h * ratio)
        return image.resize((new_w, new_h))

    @staticmethod
    def grab_window(title_pattern: str) -> "tuple[Any, WindowInfo] | None":
        """Capture a specific window by title substring.

        Returns (PIL Image, WindowInfo) or None on failure.
        """
        pattern = title_pattern.lower()

        if sys.platform == "win32":
            try:
                import ctypes
                import ctypes.wintypes
                from PIL import ImageGrab

                found_hwnd = None
                found_title = ""

                def _enum_cb(hwnd: int, lParam: int) -> bool:
                    nonlocal found_hwnd, found_title
                    if not ctypes.windll.user32.IsWindowVisible(hwnd):
                        return True
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    if length == 0:
                        return True
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    if pattern in title.lower():
                        found_hwnd = hwnd
                        found_title = title
                        return False  # stop enumeration
                    return True

                EnumWindowsProc = ctypes.WINFUNCTYPE(
                    ctypes.wintypes.BOOL,
                    ctypes.wintypes.HWND,
                    ctypes.wintypes.LPARAM,
                )
                ctypes.windll.user32.EnumWindows(EnumWindowsProc(_enum_cb), 0)

                if found_hwnd is None:
                    return None

                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(found_hwnd, ctypes.byref(rect))
                bbox = (rect.left, rect.top, rect.right, rect.bottom)
                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    return None

                img = ImageGrab.grab(bbox=bbox).convert("RGB")
                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(found_hwnd, ctypes.byref(pid))
                app_name = ""
                try:
                    import psutil
                    app_name = psutil.Process(pid.value).name()
                except Exception:
                    app_name = found_title.split(" - ")[-1]

                return img, WindowInfo(app_name=app_name, window_title=found_title, pid=pid.value)

            except Exception as exc:
                logger.debug("grab_window Win32 failed: %s", exc)
                return None

        elif sys.platform == "darwin":
            try:
                import tempfile
                import Quartz  # type: ignore[import-untyped]

                win_list = Quartz.CGWindowListCopyWindowInfo(
                    Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID,
                )
                target_id = None
                target_title = ""
                for w in win_list:
                    name = (w.get("kCGWindowName") or "").lower()
                    owner = (w.get("kCGWindowOwnerName") or "").lower()
                    if pattern in name or pattern in owner:
                        target_id = w.get("kCGWindowNumber")
                        target_title = w.get("kCGWindowName") or w.get("kCGWindowOwnerName") or ""
                        break

                if target_id is None:
                    return None

                tmp = tempfile.mktemp(suffix=".png")
                subprocess.run(
                    ["screencapture", "-l", str(target_id), "-x", "-t", "png", tmp],
                    timeout=5, check=True,
                )
                from PIL import Image
                img = Image.open(tmp).convert("RGB")
                Path(tmp).unlink(missing_ok=True)
                return img, WindowInfo(window_title=target_title)

            except Exception as exc:
                logger.debug("grab_window macOS failed: %s", exc)
                return None

        else:  # Linux
            try:
                from PIL import Image

                result = subprocess.run(
                    ["xdotool", "search", "--name", title_pattern],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode != 0 or not result.stdout.strip():
                    return None
                wid = result.stdout.strip().split("\n")[0]
                # Use import (ImageMagick) to capture the window
                import tempfile
                tmp = tempfile.mktemp(suffix=".png")
                subprocess.run(
                    ["import", "-window", wid, tmp],
                    timeout=10, check=True,
                )
                img = Image.open(tmp).convert("RGB")
                Path(tmp).unlink(missing_ok=True)
                name_result = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True, text=True, timeout=3,
                )
                return img, WindowInfo(window_title=name_result.stdout.strip())

            except Exception as exc:
                logger.debug("grab_window Linux failed: %s", exc)
                return None

    @staticmethod
    def grab_monitor(monitor_index: int) -> "tuple[Any, str] | None":
        """Capture a specific monitor by index.

        Returns (PIL Image, label_str) or None on failure.
        """
        if sys.platform == "win32":
            try:
                import ctypes
                import ctypes.wintypes
                from PIL import ImageGrab

                monitors: list[tuple[int, int, int, int]] = []

                MonitorEnumProc = ctypes.WINFUNCTYPE(
                    ctypes.wintypes.BOOL,
                    ctypes.wintypes.HANDLE,    # HMONITOR
                    ctypes.wintypes.HDC,       # HDC
                    ctypes.POINTER(ctypes.wintypes.RECT),  # LPRECT
                    ctypes.wintypes.LPARAM,    # LPARAM
                )

                def _monitor_cb(hMonitor, hdcMonitor, lprcMonitor, dwData):
                    r = lprcMonitor.contents
                    monitors.append((r.left, r.top, r.right, r.bottom))
                    return True

                ctypes.windll.user32.EnumDisplayMonitors(
                    None, None, MonitorEnumProc(_monitor_cb), 0,
                )

                if monitor_index >= len(monitors):
                    monitor_index = 0
                bbox = monitors[monitor_index]
                img = ImageGrab.grab(bbox=bbox).convert("RGB")
                return img, f"monitor:{monitor_index}"

            except Exception as exc:
                logger.debug("grab_monitor Win32 failed: %s", exc)
                return None

        elif sys.platform == "darwin":
            try:
                import Quartz  # type: ignore[import-untyped]

                max_displays = 8
                _err, active_displays, display_count = Quartz.CGGetActiveDisplayList(
                    max_displays, None, None,
                )
                if monitor_index >= display_count:
                    monitor_index = 0

                import tempfile
                tmp = tempfile.mktemp(suffix=".png")
                # screencapture -D uses 1-based display index
                subprocess.run(
                    ["screencapture", "-x", "-D", str(monitor_index + 1), "-t", "png", tmp],
                    timeout=5, check=True,
                )
                from PIL import Image
                img = Image.open(tmp).convert("RGB")
                Path(tmp).unlink(missing_ok=True)
                return img, f"monitor:{monitor_index}"

            except Exception as exc:
                logger.debug("grab_monitor macOS failed: %s", exc)
                return None

        else:  # Linux fallback: full desktop
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab().convert("RGB")
                return img, f"monitor:{monitor_index}"
            except Exception as exc:
                logger.debug("grab_monitor Linux failed: %s", exc)
                return None


# ---------------------------------------------------------------------------
# PrivacyFilter -- app-level blocklist
# ---------------------------------------------------------------------------

class PrivacyFilter:
    """Drops frames from blocked applications."""

    def __init__(self, config: VisualCortexConfig) -> None:
        self._blocked_apps = {a.lower() for a in config.blocked_apps}
        self._blocked_titles = config.blocked_window_titles

    def is_blocked(self, window: WindowInfo) -> bool:
        if window.app_name.lower() in self._blocked_apps:
            return True
        title_lower = window.window_title.lower()
        for pattern in self._blocked_titles:
            if pattern.lower() in title_lower:
                return True
        return False

    def update_blocklist(
        self, apps: list[str] | None = None, titles: list[str] | None = None,
    ) -> None:
        if apps is not None:
            self._blocked_apps = {a.lower() for a in apps}
        if titles is not None:
            self._blocked_titles = titles


# ---------------------------------------------------------------------------
# FrameDiffer -- perceptual change detection
# ---------------------------------------------------------------------------

class FrameDiffer:
    """Detects whether a frame has changed enough to warrant processing."""

    def __init__(
        self, threshold: float = 0.05, stability_ms: int = 500,
    ) -> None:
        self._threshold = threshold
        self._stability_s = stability_ms / 1000.0
        self._prev_hash: str = ""
        self._prev_time: float = 0.0
        self._prev_app: str = ""
        self._stable_since: float = 0.0

    def _image_hash(self, image: Any) -> str:
        """Fast perceptual hash using downscaled grayscale average."""
        small = image.resize((16, 16)).convert("L")
        pixels = list(small.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p > avg else "0" for p in pixels)
        return hashlib.md5(bits.encode()).hexdigest()

    def has_changed(self, image: Any, window: WindowInfo) -> bool:
        """Return True if the frame differs enough from the previous one."""
        now = time.time()

        if window.app_name != self._prev_app:
            self._stable_since = now
            self._prev_app = window.app_name

        # Skip frames during rapid app switching (Alt-Tab thrash)
        if (now - self._stable_since) < self._stability_s:
            return False

        current_hash = self._image_hash(image)

        if current_hash == self._prev_hash:
            return False

        if self._prev_hash:
            old_bits = bin(int(self._prev_hash, 16))[2:].zfill(128)
            new_bits = bin(int(current_hash, 16))[2:].zfill(128)
            diff = sum(a != b for a, b in zip(old_bits, new_bits)) / 128.0
            if diff < self._threshold:
                return False

        self._prev_hash = current_hash
        self._prev_time = now
        return True

    @property
    def last_change_time(self) -> float:
        return self._prev_time

    def reset(self) -> None:
        self._prev_hash = ""
        self._prev_time = 0.0


# ---------------------------------------------------------------------------
# AgentWorkspaceTracker -- tracks agent-controlled windows
# ---------------------------------------------------------------------------

class AgentWorkspaceTracker:
    """Tracks windows that the agent controls (browser, MCP GUIs, etc.)."""

    def __init__(self) -> None:
        self._windows: dict[int, str] = {}  # pid -> tool_name
        self._title_patterns: dict[str, str] = {}  # title_substring -> tool_name

    def register(self, pid: int, tool_name: str) -> None:
        self._windows[pid] = tool_name
        logger.debug("Registered agent window: pid=%d tool=%s", pid, tool_name)

    def register_title_pattern(self, pattern: str, tool_name: str) -> None:
        self._title_patterns[pattern.lower()] = tool_name

    def unregister(self, pid: int) -> None:
        self._windows.pop(pid, None)

    def is_agent_window(self, window: WindowInfo) -> tuple[bool, str]:
        """Check if a window belongs to the agent.  Returns (is_agent, tool_name)."""
        if window.pid and window.pid in self._windows:
            return True, self._windows[window.pid]
        title_lower = window.window_title.lower()
        app_lower = window.app_name.lower()
        for pattern, tool_name in self._title_patterns.items():
            if pattern in title_lower or pattern in app_lower:
                return True, tool_name
        return False, ""

    @property
    def active_tools(self) -> list[str]:
        return list(set(self._windows.values()))


# ---------------------------------------------------------------------------
# VisualCortex -- the main orchestrator
# ---------------------------------------------------------------------------

class VisualCortex:
    """Continuous visual awareness engine.

    Runs a background asyncio loop that captures the screen, processes
    frames through Moondream, and maintains a VisualBuffer.

    Usage::

        cortex = VisualCortex(config)
        await cortex.start()

        # During agentic loop -- check what the agent sees
        changes = cortex.buffer.get_changes(since=tool_start, until=tool_end)

        # During idle -- thalamus polls for relevant events
        latest = cortex.buffer.get_latest_change(channel="user")

        await cortex.stop()
    """

    def __init__(
        self,
        config: VisualCortexConfig | None = None,
        gpu_worker_url: str = "",
        gpu_worker_secret: str = "",
    ) -> None:
        self.config = config or VisualCortexConfig()
        self.buffer = VisualBuffer(max_size=self.config.buffer_size)
        self.workspace = AgentWorkspaceTracker()
        self.workspace.register_title_pattern("babo", "self")
        self.workspace.register_title_pattern("nls", "self")
        self.workspace.register_title_pattern("electron", "self")

        self._privacy = PrivacyFilter(self.config)
        self._differ_agent = FrameDiffer(
            threshold=0.03, stability_ms=0,
        )
        self._differ_user = FrameDiffer(
            threshold=self.config.frame_diff_threshold,
            stability_ms=self.config.stability_ms,
        )
        self._capture = ScreenCapture()

        self._vlm: Any = None
        self._remote_vlm: Any = None
        self._gpu_worker_url = gpu_worker_url
        self._gpu_worker_secret = gpu_worker_secret
        self._browser_engine: Any = None
        self._hypothalamus: Any = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._callbacks: list[Callable[[VisualEvent], Any]] = []

        # Focus management (agent-controlled)
        self._focus: FocusTarget = FocusTarget.desktop()
        self._focus_stack: list[FocusTarget] = []
        self._vc_enabled: bool = True   # master on/off; separate from config.enabled

        self._agent_fps = self.config.agent_fps
        self._user_fps = self.config.user_fps
        self._agent_active = False
        self._events_this_minute = 0
        self._minute_start = time.time()

        self._remote_fail_count = 0
        self._remote_backoff_until = 0.0

        self._vlm_pref: str | None = None
        self._vlm_shared = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background capture loops.

        Local VLM (``SubprocessVLMBackend``) loads only for ``dedicated_vlm_local``
        or legacy/auto without a LAN worker. ``dedicated_vlm_lan`` uses
        ``RemoteVLMBackend`` only so PyTorch never touches the desktop GPU.
        """
        if self._running:
            return
        if not self.config.enabled:
            logger.info("Visual cortex is disabled in config — not starting")
            return

        from .visual_model import RemoteVLMBackend, SharedVLMRegistry

        loop = asyncio.get_running_loop()

        strategy = _visual_cortex_strategy()

        # 1. Wire remote backend if a vision URL is configured
        if self._gpu_worker_url:
            self._remote_vlm = RemoteVLMBackend(
                self._gpu_worker_url, self._gpu_worker_secret,
            )
            logger.info("Visual cortex: remote VLM wired (%s)", self._gpu_worker_url)

        if strategy == "dedicated_vlm_lan" and self._remote_vlm is None:
            logger.error(
                "Visual cortex: dedicated_vlm_lan requires NLS_VISION_WORKER_URL "
                "— not starting",
            )
            return

        # 2. Acquire the shared local VLM subprocess when this machine owns vision.
        #    dedicated_vlm_lan uses RemoteVLMBackend only (no local PyTorch).
        preference = self.config.model_preference  # "auto", "moondream", etc.
        self._vlm_pref = preference
        self._vlm_shared = False

        if _use_local_vlm_worker(strategy):
            logger.info("Visual cortex: acquiring shared local VLM worker...")
            try:
                self._vlm = SharedVLMRegistry.acquire(preference)
                self._vlm_shared = True
                if not self._vlm.is_loaded:
                    await loop.run_in_executor(None, self._vlm.warmup)
                logger.info(
                    "Visual cortex: local VLM ready (%s, %s)",
                    type(self._vlm).__name__,
                    self._vlm.info,
                )
            except Exception:
                logger.error("Visual cortex: local VLM load/warmup failed", exc_info=True)
                if self._vlm_shared:
                    SharedVLMRegistry.release(preference)
                    self._vlm_shared = False
                self._vlm = None
                self._vlm_pref = None
                if self._remote_vlm is None:
                    return
        elif self._remote_vlm is not None:
            logger.info(
                "Visual cortex: LAN-only mode (%s) — skipping local VLM worker",
                strategy or "dedicated_vlm_lan",
            )
        else:
            logger.error("Visual cortex: no VLM backend configured — not starting")
            return

        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Visual cortex started (fps=%.1f)", self.config.fps)

    async def stop(self) -> None:
        """Stop the capture loop and release the shared VLM worker ref."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                # Local describe can still run in the default executor after
                # cancel; wait out one frame timeout before releasing VLM.
                await asyncio.wait_for(self._task, timeout=35.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._task = None

        if self._vlm_shared and self._vlm_pref is not None:
            from .visual_model import SharedVLMRegistry

            SharedVLMRegistry.release(self._vlm_pref)
            self._vlm_shared = False
            self._vlm_pref = None
        self._vlm = None
        logger.info("Visual cortex stopped")

    # ------------------------------------------------------------------
    # Agent workspace integration
    # ------------------------------------------------------------------

    def set_agent_active(self, active: bool) -> None:
        """Signal whether the agentic loop is currently running."""
        self._agent_active = active
        if active:
            self._agent_fps = self.config.max_fps
        else:
            self._agent_fps = 0.0

    def set_browser_engine(self, browser_engine: Any) -> None:
        """Register the browser engine for direct CDP-based capture.

        When set, the agent channel uses Playwright's screenshot API
        instead of OS-level screen capture for browser windows —
        producing cleaner frames with no window chrome.
        Pass None to unregister.
        """
        self._browser_engine = browser_engine
        if browser_engine is not None:
            self.workspace.register_title_pattern("chromium", "browser")
            self.workspace.register_title_pattern("chrome", "browser")

    def set_hypothalamus(self, hypothalamus: Any) -> None:
        """Provide a hypothalamus reference for reading cortisol in adaptive FPS."""
        self._hypothalamus = hypothalamus

    # ------------------------------------------------------------------
    # Focus management (agent control)
    # ------------------------------------------------------------------

    def set_focus(self, target: "str | FocusTarget") -> None:
        """Set the active capture focus.

        Args:
            target: A FocusTarget instance or a spec string like
                    'browser', 'desktop', 'window:WhatsApp', 'monitor:1', 'off'.
        """
        if isinstance(target, str):
            target = FocusTarget.parse(target)
        self._focus = target
        logger.info("VC focus → %s (pattern=%s)", target.kind, target.window_pattern)

    def push_focus(self, target: "str | FocusTarget") -> None:
        """Push a new focus on the stack, saving the current one for later pop."""
        self._focus_stack.append(self._focus)
        self.set_focus(target)

    def pop_focus(self) -> FocusTarget:
        """Restore the previous focus from the stack."""
        if self._focus_stack:
            self._focus = self._focus_stack.pop()
            logger.debug("VC focus popped → %s", self._focus.kind)
        return self._focus

    def set_enabled(self, enabled: bool) -> None:
        """Master on/off toggle for the capture loop."""
        self._vc_enabled = enabled
        logger.info("VC enabled=%s", enabled)

    async def look_now(
        self,
        target: "str | FocusTarget | None" = None,
        question: str = "",
    ) -> str:
        """One-shot capture + VLM query.  Does NOT change persistent focus.

        Args:
            target: Optional temporary focus for this capture only.
            question: Optional question for the VLM (overrides default desc prompt).

        Returns:
            Combined description + OCR string, or empty string on failure.
        """
        loop = asyncio.get_running_loop()
        if isinstance(target, str):
            target = FocusTarget.parse(target)
        effective = target or self._focus

        image = await self._capture_for_focus(effective)
        if image is None:
            return ""

        image = self._capture.downscale(image)

        if self._vlm is None and self._remote_vlm is None:
            return ""

        # Prefer remote for on-demand look_now (higher quality)
        _remote_ok = (
            self._remote_vlm is not None
            and self._remote_fail_count < 3
            and time.time() >= self._remote_backoff_until
        )

        desc, ocr = "", ""
        if _remote_ok:
            try:
                if question:
                    coro = loop.run_in_executor(
                        None, self._remote_vlm.ask, image, question,
                    )
                    desc = await asyncio.wait_for(coro, timeout=60.0)
                else:
                    coro = loop.run_in_executor(None, self._remote_vlm.describe, image)
                    desc, ocr = await asyncio.wait_for(coro, timeout=60.0)
                self._remote_fail_count = 0
            except Exception as exc:
                self._remote_fail_count += 1
                logger.warning("look_now remote failed: %s", exc)

        if not desc and self._vlm is not None:
            try:
                coro = loop.run_in_executor(None, self._vlm.describe, image)
                desc, ocr = await asyncio.wait_for(coro, timeout=30.0)
            except Exception as exc:
                logger.warning("look_now local failed: %s", exc)

        parts = []
        if desc:
            parts.append(desc[:300])
        if ocr:
            parts.append(f"OCR: {_clean_ocr(ocr)}")
        return "\n".join(parts)

    async def _capture_for_focus(self, focus: FocusTarget) -> Any | None:
        """Return a PIL Image for the given FocusTarget, or None on failure."""
        loop = asyncio.get_running_loop()

        if focus.kind == FocusKind.BROWSER:
            if self._browser_engine is not None:
                try:
                    _cap = getattr(self._browser_engine, "_async_capture_frame", None)
                    if _cap is not None:
                        return await _cap()
                    return await loop.run_in_executor(
                        None, self._browser_engine.capture_frame,
                    )
                except Exception as exc:
                    logger.debug("_capture_for_focus browser failed: %s", exc)
            # Fall through to desktop if no browser
            return await loop.run_in_executor(None, self._capture.grab)

        if focus.kind == FocusKind.WINDOW:
            try:
                result = await loop.run_in_executor(
                    None, ScreenCapture.grab_window, focus.window_pattern,
                )
                if result is not None:
                    img, _info = result
                    return img
            except Exception as exc:
                logger.debug("_capture_for_focus window failed: %s", exc)
            # Fall through to full desktop on failure
            return await loop.run_in_executor(None, self._capture.grab)

        if focus.kind == FocusKind.MONITOR:
            try:
                result = await loop.run_in_executor(
                    None, ScreenCapture.grab_monitor, focus.monitor_index,
                )
                if result is not None:
                    img, _info = result
                    return img
            except Exception as exc:
                logger.debug("_capture_for_focus monitor failed: %s", exc)
            return await loop.run_in_executor(None, self._capture.grab)

        if focus.kind == FocusKind.OFF:
            return None

        # Default: full desktop
        return await loop.run_in_executor(None, self._capture.grab)

    def _get_cortisol(self) -> float:
        """Read current cortisol level from the hypothalamus (0.0 if unavailable)."""
        if self._hypothalamus is None:
            return 0.0
        h = self._hypothalamus.hormones.get("cortisol")
        return h.level if h is not None else 0.0

    def register_agent_window(self, pid: int, tool_name: str) -> None:
        self.workspace.register(pid, tool_name)

    def unregister_agent_window(self, pid: int) -> None:
        self.workspace.unregister(pid)

    # ------------------------------------------------------------------
    # Event callbacks
    # ------------------------------------------------------------------

    def on_event(self, callback: Callable[[VisualEvent], Any]) -> None:
        self._callbacks.append(callback)

    async def _emit(self, event: VisualEvent) -> None:
        self.buffer.push(event)
        for cb in self._callbacks:
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.debug("Visual event callback error", exc_info=True)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _rate_ok(self) -> bool:
        now = time.time()
        if now - self._minute_start > 60:
            self._events_this_minute = 0
            self._minute_start = now
        return self._events_this_minute < self.config.max_events_per_minute

    # ------------------------------------------------------------------
    # Main capture loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Main capture loop — alternates between agent and user channels."""
        last_agent_capture = 0.0
        last_user_capture = 0.0
        last_fps_update = 0.0
        last_heartbeat = 0.0
        _FPS_UPDATE_INTERVAL = 10.0
        _HEARTBEAT_INTERVAL = 60.0

        while self._running:
            try:
                now = time.time()

                if (now - last_heartbeat) >= _HEARTBEAT_INTERVAL:
                    logger.info(
                        "VC heartbeat: vlm=%s, remote=%s, browser=%s, "
                        "buffer=%d, events_min=%d, agent_active=%s",
                        type(self._vlm).__name__ if self._vlm else "None",
                        self._remote_vlm is not None,
                        self._browser_engine is not None,
                        len(self.buffer), self._events_this_minute,
                        self._agent_active,
                    )
                    last_heartbeat = now

                # Periodically recompute adaptive FPS
                if (now - last_fps_update) >= _FPS_UPDATE_INTERVAL:
                    cortisol = self._get_cortisol()
                    self.update_adaptive_fps(cortisol=cortisol)
                    last_fps_update = now

                # Agent channel
                agent_interval = (1.0 / self._agent_fps) if self._agent_fps > 0 else float("inf")
                if self._agent_active and (now - last_agent_capture) >= agent_interval:
                    await self._capture_frame(channel="agent")
                    last_agent_capture = time.time()

                # User channel
                user_interval = (1.0 / self._user_fps) if self._user_fps > 0 else float("inf")
                if (now - last_user_capture) >= user_interval:
                    await self._capture_frame(channel="user")
                    last_user_capture = time.time()

                # Sleep at the highest FPS rate to maintain responsiveness
                active_fps = max(
                    self._agent_fps if self._agent_active else 0,
                    self._user_fps,
                    0.1,  # minimum to avoid busy loop
                )
                await asyncio.sleep(1.0 / active_fps)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Visual cortex loop error", exc_info=True)
                await asyncio.sleep(2.0)

    async def _capture_frame(self, channel: str) -> None:
        """Capture and process a single frame for the given channel."""
        # Respect master enabled toggle
        if not self._vc_enabled:
            return

        loop = asyncio.get_running_loop()

        image = None
        window = WindowInfo()

        # For the agent channel, use the active focus target when set to BROWSER,
        # WINDOW, or MONITOR; otherwise fall back to the original CDP/OS logic.
        # The user channel always uses OS-level desktop capture (ambient awareness).
        if channel == "agent":
            active_focus = self._focus
            if active_focus.kind == FocusKind.OFF:
                return

            if active_focus.kind == FocusKind.BROWSER and self._browser_engine is not None:
                try:
                    _async_cap = getattr(self._browser_engine, "_async_capture_frame", None)
                    if _async_cap is not None:
                        image = await _async_cap()
                    else:
                        image = await loop.run_in_executor(
                            None, self._browser_engine.capture_frame,
                        )
                    if image is not None:
                        window = WindowInfo(app_name="Chromium", window_title="Browser")
                        logger.debug("VC [%s]: CDP capture ok (%s)", channel, image.size)
                    else:
                        logger.debug("VC [%s]: CDP capture returned None, falling back to OS grab", channel)
                except Exception as exc:
                    logger.debug("VC [%s]: CDP capture failed: %s", channel, exc)

            elif active_focus.kind == FocusKind.WINDOW and active_focus.window_pattern:
                try:
                    result = await loop.run_in_executor(
                        None, ScreenCapture.grab_window, active_focus.window_pattern,
                    )
                    if result is not None:
                        image, _wi = result
                        window = _wi
                except Exception as exc:
                    logger.debug("VC [%s]: window capture failed: %s", channel, exc)

            elif active_focus.kind == FocusKind.MONITOR:
                try:
                    result = await loop.run_in_executor(
                        None, ScreenCapture.grab_monitor, active_focus.monitor_index,
                    )
                    if result is not None:
                        image, _mi_label = result
                        window = WindowInfo(app_name=f"Monitor {active_focus.monitor_index}")
                except Exception as exc:
                    logger.debug("VC [%s]: monitor capture failed: %s", channel, exc)

            elif active_focus.kind == FocusKind.BROWSER and self._browser_engine is None:
                # Browser focus requested but engine not ready — skip
                return

        else:
            # User channel: always OS-level desktop capture (ambient awareness).
            # Do NOT call the browser engine here — the user channel is intentionally
            # blind to agent-controlled browser windows so it captures the user's desktop.
            pass

        # Fall back to OS-level screen capture
        if image is None:
            image = await loop.run_in_executor(None, self._capture.grab)
            if image is None:
                logger.debug("VC [%s]: OS grab returned None", channel)
                return
            window = await loop.run_in_executor(
                None, self._capture.get_active_window,
            )

        # Channel routing
        is_agent_win, tool_name = self.workspace.is_agent_window(window)

        if channel == "agent":
            if not is_agent_win:
                # When focus is explicitly set (non-desktop), accept any source
                if self._focus.kind not in (FocusKind.DESKTOP, FocusKind.OFF):
                    tool_name = self._focus.kind  # e.g. "browser", "window", "monitor"
                elif self._browser_engine is None:
                    return
                else:
                    tool_name = "browser"
            differ = self._differ_agent
        else:
            if is_agent_win:
                return
            if self._privacy.is_blocked(window):
                return
            differ = self._differ_user

        # Downscale for VLM
        image = self._capture.downscale(image)

        # Frame differencing
        if not differ.has_changed(image, window):
            return

        if not self._rate_ok():
            logger.debug("VC [%s]: rate limited", channel)
            return

        # VLM inference (blocking → executor)
        if self._vlm is None and self._remote_vlm is None:
            return

        _LOCAL_TIMEOUT = 30.0
        _REMOTE_TIMEOUT = 60.0
        t0 = time.time()
        desc: str = ""
        ocr: str = ""

        # Decide whether to prefer remote over local.  Skip local when
        # remote is healthy and local is a weak model (SmolVLM).  But if
        # the remote has been failing, always fall back to local.
        _local_model_id = ""
        if self._vlm is not None and self._vlm.info is not None:
            _local_model_id = self._vlm.info.model_id

        strategy = _visual_cortex_strategy()
        _local_only = strategy == "dedicated_vlm_local"
        _remote_only = strategy == "dedicated_vlm_lan"
        _remote_healthy = (
            not _local_only
            and self._remote_vlm is not None
            and self._remote_fail_count < 3
            and time.time() >= self._remote_backoff_until
        )
        _skip_local = _remote_only or (
            _remote_healthy
            and self._vlm is not None
            and "SmolVLM" in _local_model_id
        )

        # Try local VLM first (unless LAN-only or weak local + healthy remote)
        if self._vlm is not None and not _skip_local:
            try:
                coro = loop.run_in_executor(None, self._vlm.describe, image)
                desc, ocr = await asyncio.wait_for(coro, timeout=_LOCAL_TIMEOUT)
                logger.debug("VC [%s]: local inference done in %.1fs", channel, time.time() - t0)
            except asyncio.TimeoutError:
                logger.warning("VC [%s]: local inference timed out after %.0fs", channel, _LOCAL_TIMEOUT)
                desc = ""
            except Exception as exc:
                logger.warning("VC [%s]: local inference failed: %s", channel, exc)
                desc = ""

        # Use remote if local failed/skipped and remote is not in backoff
        if not desc and self._remote_vlm is not None and time.time() >= self._remote_backoff_until:
            try:
                t1 = time.time()
                coro_r = loop.run_in_executor(None, self._remote_vlm.describe, image)
                desc, ocr = await asyncio.wait_for(coro_r, timeout=_REMOTE_TIMEOUT)
                logger.info("VC [%s]: remote inference done in %.1fs", channel, time.time() - t1)
                self._remote_fail_count = 0
            except Exception as exc:
                self._remote_fail_count += 1
                _backoff = min(30.0 * (2 ** (self._remote_fail_count - 1)), 600.0)
                self._remote_backoff_until = time.time() + _backoff
                logger.warning(
                    "VC [%s]: remote inference failed (%d consecutive): %s — "
                    "backing off %.0fs",
                    channel, self._remote_fail_count, exc, _backoff,
                )

        # Last resort: if remote failed/skipped and local was skipped, try local
        if not desc and _skip_local and self._vlm is not None:
            try:
                coro_fb = loop.run_in_executor(None, self._vlm.describe, image)
                desc, ocr = await asyncio.wait_for(coro_fb, timeout=_LOCAL_TIMEOUT)
                logger.info("VC [%s]: local fallback inference done in %.1fs", channel, time.time() - t0)
            except Exception as exc:
                logger.warning("VC [%s]: local fallback also failed: %s", channel, exc)
                return

        if not desc:
            return

        # Post-process: clean OCR and cap description length
        ocr = _clean_ocr(ocr)
        if len(desc) > 300:
            desc = desc[:300].rsplit(" ", 1)[0] + "…"

        # Build event
        prev = self.buffer.get_latest_change(channel=channel)
        change_summary = ""
        if prev and prev.description != desc:
            change_summary = f"Changed from: {prev.app_name}/{prev.window_title}"

        event = VisualEvent(
            timestamp=time.time(),
            channel=channel,
            app_name=window.app_name,
            window_title=window.window_title,
            description=desc,
            ocr_text=ocr,
            change_summary=change_summary if change_summary else desc[:100],
            agent_tool=tool_name if channel == "agent" else "",
        )

        self._events_this_minute += 1
        await self._emit(event)
        logger.info(
            "VC event [%s] app=%s desc=%s (buffer=%d)",
            channel, window.app_name, desc[:80], len(self.buffer),
        )

    # ------------------------------------------------------------------
    # Public query API (for agentic loop / thalamus)
    # ------------------------------------------------------------------

    def get_history_summary(self, minutes: int = 5) -> str:
        """Summarise recent visual history as a human-readable timeline.

        Args:
            minutes: How far back to look (default 5).

        Returns:
            Timeline string (max ~500 chars), or empty string if no history.
        """
        since = time.time() - minutes * 60
        events = self.buffer.get_since(since)
        if not events:
            return ""

        # Group by app + window transitions
        lines: list[str] = []
        prev_app = ""
        for ev in events:
            ts = time.strftime("%H:%M", time.localtime(ev.timestamp))
            app = ev.app_name or ev.window_title or "unknown"
            desc = ev.description[:100] if ev.description else ""
            ocr_snippet = ""
            if ev.ocr_text:
                # Highlight error-ish text
                for token in ("error", "failed", "denied", "warning", "invalid"):
                    if token in ev.ocr_text.lower():
                        ocr_snippet = f" [{ev.ocr_text[:60]}]"
                        break
            if app != prev_app:
                lines.append(f"{ts} - {app}: {desc}{ocr_snippet}")
                prev_app = app
            elif desc:
                lines.append(f"  {ts}: {desc}{ocr_snippet}")

        summary = "\n".join(lines)
        if len(summary) > 500:
            summary = summary[:500].rsplit("\n", 1)[0] + "\n…"
        return summary

    def get_visual_context(
        self,
        since: float | None = None,
        channel: str | None = None,
    ) -> str:
        """Return a formatted visual context string for prompt injection.

        Args:
            since: Only include events after this timestamp
            channel: Filter to "agent" or "user" channel

        Returns:
            Formatted string or empty string if no relevant events
        """
        if since:
            events = self.buffer.get_changes(
                since=since, channel=channel,
            )
        else:
            event = self.buffer.get_latest_change(channel=channel)
            events = [event] if event else []

        if not events:
            return ""

        lines = [e.to_context_line() for e in events[-3:]]
        return "\n".join(lines)

    def get_tool_visual_feedback(self, tool_start: float) -> str:
        """Get visual feedback for a tool that just executed.

        Called by the agentic loop after every tool call.
        Returns visual context from the agent channel during
        the tool's execution window, or empty string if no
        visual changes were detected.

        Excludes "self" events (the agent's own UI) — the agent
        shouldn't react to screenshots of its own chat window.
        """
        changes = self.buffer.get_changes(
            since=tool_start, channel="agent",
        )
        # Filter out self-window captures (by agent_tool tag or by
        # content heuristic — on macOS the window detector may return
        # empty app/title, so the agent_tool tag alone is not reliable)
        def _is_self_ui(ev: VisualEvent) -> bool:
            if ev.agent_tool == "self":
                return True
            _desc = (ev.description + " " + ev.ocr_text).lower()
            _HAS_AGENT_UI = (
                "ai chat interface" in _desc
                or "ai interface" in _desc
                or "ai agent" in _desc
                or "chat interface" in _desc
                or "neural state metric" in _desc
                or ("main chat" in _desc and "agent" in _desc)
            )
            _HAS_AGENT_NAME = (
                "babo" in _desc or "babbo" in _desc
                or "baboo" in _desc or "babpo" in _desc
                or "bebo" in _desc
            )
            _HAS_NLS_MARKER = (
                "hormone" in _desc and ("level" in _desc or "metric" in _desc)
                or "cortisol" in _desc
                or "dopamine" in _desc and "serotonin" in _desc
                or "agentic loop" in _desc
                or "tool execution" in _desc and "iteration" in _desc
            )
            return _HAS_AGENT_UI or _HAS_AGENT_NAME or _HAS_NLS_MARKER
        changes = [c for c in changes if not _is_self_ui(c)]

        if not changes:
            all_changes = self.buffer.get_changes(since=tool_start)
            all_changes = [
                c for c in all_changes if not _is_self_ui(c)
            ]
            changes = all_changes[-1:] if all_changes else []

        if not changes:
            return ""

        latest = changes[-1]
        parts = [f"[VISUAL] {latest.description[:200]}"]
        if latest.ocr_text:
            parts.append(f"[VISUAL OCR] {latest.ocr_text[:200]}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Adaptive FPS
    # ------------------------------------------------------------------

    def update_adaptive_fps(
        self,
        cortisol: float = 0.0,
        is_on_battery: bool | None = None,
        is_screen_locked: bool | None = None,
    ) -> None:
        """Recompute FPS for both channels based on current conditions.

        Called periodically by the runtime or the agentic loop.
        """
        # Detect battery state if not provided
        if is_on_battery is None:
            is_on_battery = self._detect_battery()
        if is_screen_locked is None:
            is_screen_locked = self._detect_screen_lock()

        # Screen locked → pause everything
        if is_screen_locked:
            self._agent_fps = 0.0
            self._user_fps = 0.0
            return

        # Agent channel
        if self._agent_active:
            self._agent_fps = self.config.max_fps
        else:
            self._agent_fps = 0.0

        # User channel
        if is_on_battery:
            self._user_fps = self.config.min_fps
        elif cortisol > 0.5:
            self._user_fps = min(1.5, self.config.max_fps)
        else:
            self._user_fps = self.config.fps

        # If nothing has changed for a while, slow down
        last_change = self._differ_user.last_change_time
        if last_change and (time.time() - last_change) > 10.0:
            self._user_fps = min(self._user_fps, 0.3)

    @staticmethod
    def _detect_battery() -> bool:
        """Best-effort battery detection.  Returns True if on battery."""
        try:
            import psutil
            battery = psutil.sensors_battery()
            if battery is not None:
                return not battery.power_plugged
        except (ImportError, AttributeError):
            pass
        return False

    _screen_lock_cache: tuple[float, bool] = (0.0, False)

    @classmethod
    def _detect_screen_lock(cls) -> bool:
        """Best-effort screen lock detection (cached for 5s)."""
        now = time.time()
        if (now - cls._screen_lock_cache[0]) < 5.0:
            return cls._screen_lock_cache[1]

        locked = False
        if sys.platform == "darwin":
            try:
                import Quartz  # type: ignore[import-untyped]
                d = Quartz.CGSessionCopyCurrentDictionary()
                locked = bool(d and d.get("CGSSessionScreenIsLocked", 0))
            except ImportError:
                pass
            except Exception:
                pass
        elif sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                locked = user32.GetForegroundWindow() == 0
            except Exception:
                pass

        cls._screen_lock_cache = (now, locked)
        return locked

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "enabled": self.config.enabled,
            "agent_active": self._agent_active,
            "agent_fps": self._agent_fps,
            "user_fps": self._user_fps,
            "buffer_size": len(self.buffer),
            "model_loaded": self._vlm.is_loaded if self._vlm else False,
            "model_info": self._vlm.info.__dict__ if self._vlm and self._vlm.info else None,
            "agent_tools": self.workspace.active_tools,
            "events_this_minute": self._events_this_minute,
        }
