"""Surface session key detection — built-in and registered custom channels."""

from __future__ import annotations

from typing import Any

_BUILTIN_SURFACE_CHANNELS = frozenset({"discord", "telegram", "whatsapp", "slack", "email"})


def registered_channel_names(runtime: Any | None) -> set[str]:
    registry = getattr(runtime, "channel_registry", None)
    if registry is None:
        return set()
    try:
        names = registry.channel_names()
        return {str(n).strip().lower() for n in names if str(n).strip()}
    except Exception:
        return set()


def is_routable_surface_session_key(
    session_key: str | None,
    runtime: Any | None = None,
) -> bool:
    if not session_key or session_key == "websocket:main":
        return False
    if str(session_key).startswith("websocket:"):
        return False
    parts = str(session_key).split(":")
    if len(parts) < 2:
        return False
    head = parts[0].strip().lower()
    if head in _BUILTIN_SURFACE_CHANNELS:
        return True
    if runtime is not None and head in registered_channel_names(runtime):
        return True
    return False


def is_home_session_key(session_key: str | None) -> bool:
    sk = (session_key or "").strip()
    return sk in ("", "websocket:main") or sk.startswith("websocket:thread:")
