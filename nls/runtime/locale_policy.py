"""Environment / UI language helpers for Cryptex seeding and prompts."""

from __future__ import annotations

import os
from typing import Any

SUPPORTED_LANGUAGES = ("en", "it", "fr", "es", "de")

_LANGUAGE_NAMES = {
    "en": "English",
    "it": "Italian",
    "fr": "French",
    "es": "Spanish",
    "de": "German",
}


def normalize_language(raw: str | None) -> str:
    s = (raw or "").strip().lower().replace("_", "-")
    if not s:
        return "en"
    primary = s.split("-", 1)[0] or s
    if primary in SUPPORTED_LANGUAGES:
        return primary
    return "en"


def language_display_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(normalize_language(code), "English")


def env_language_from_environ(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    raw = (
        (env.get("NLS_ENV_LANGUAGE") or "").strip()
        or (env.get("NLS_UI_LOCALE") or "").strip()
    )
    return normalize_language(raw)


def environment_language_slot_content(language: str) -> str:
    code = normalize_language(language)
    name = language_display_name(code)
    return (
        f"Environment language: {name} ({code}).\n"
        "Use this language for greetings and when the user has not written anything yet "
        "(new agent / empty thread).\n"
        "CRITICAL: Always reply in the language of the user's latest message. "
        "If they write Italian, reply in Italian — even if the app UI or this "
        "environment default is English (or any other language)."
    )


def mimic_user_language_rule() -> str:
    return (
        "- Match the user's language. Reply in the language of their latest message. "
        "If they write Italian, reply in Italian — even if the app UI is English.\n"
        "- The environment language in Cryptex is only the default for greetings and "
        "new threads with no user text yet. Mid-conversation, follow the user."
    )


def seed_environment_language(cryptex: Any, language: str | None = None) -> bool:
    """Upsert RING_ENVIRONMENT language slot. Returns True if written."""
    if cryptex is None or not hasattr(cryptex, "upsert_environment"):
        return False
    code = normalize_language(language if language is not None else env_language_from_environ())
    cryptex.upsert_environment(
        "language",
        environment_language_slot_content(code),
        source="genesis",
        salience=0.95,
    )
    return True
