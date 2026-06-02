"""Product-mode helpers for the open-source runtime (BYO inference, no training)."""

from __future__ import annotations

import os

from server.config import ServerSettings


def env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_product_mode(settings: ServerSettings | None = None) -> bool:
    if settings is not None and getattr(settings, "product_mode", None) is not None:
        return bool(settings.product_mode)
    return env_flag("NLS_PRODUCT_MODE", default=True)


def apply_product_defaults(settings: ServerSettings) -> ServerSettings:
    """Force OSS-friendly defaults when product mode is active."""
    if not settings.product_mode:
        return settings
    if settings.default_genesis in ("", "moe-v1", "32b-v5"):
        settings.default_genesis = "standard-v1"
    return settings
