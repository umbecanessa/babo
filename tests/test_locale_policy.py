"""Tests for locale / Cryptex environment language policy."""

from __future__ import annotations

from nls.runtime.locale_policy import (
    env_language_from_environ,
    environment_language_slot_content,
    mimic_user_language_rule,
    normalize_language,
    seed_environment_language,
)


def test_normalize_language_primary_and_fallback():
    assert normalize_language("it-IT") == "it"
    assert normalize_language("fr_FR") == "fr"
    assert normalize_language("es") == "es"
    assert normalize_language("de-DE") == "de"
    assert normalize_language("en-US") == "en"
    assert normalize_language("ja") == "en"
    assert normalize_language("") == "en"
    assert normalize_language(None) == "en"


def test_env_language_from_environ_prefers_nls_env_language():
    assert (
        env_language_from_environ(
            {"NLS_ENV_LANGUAGE": "it", "NLS_UI_LOCALE": "en"}
        )
        == "it"
    )
    assert env_language_from_environ({"NLS_UI_LOCALE": "fr-CA"}) == "fr"
    assert env_language_from_environ({}) == "en"


def test_environment_slot_stresses_mimic_user():
    text = environment_language_slot_content("it")
    assert "Italian" in text
    assert "(it)" in text
    assert "latest message" in text.lower() or "user's latest" in text.lower()


def test_mimic_user_language_rule_mentions_ui_vs_chat():
    rule = mimic_user_language_rule()
    assert "Italian" in rule
    assert "English" in rule
    assert "Cryptex" in rule or "environment" in rule.lower()


class _FakeCryptex:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def upsert_environment(self, domain, content, source="system", salience=0.9):
        self.calls.append((domain, content, source, salience))


def test_seed_environment_language_writes_language_slot():
    cx = _FakeCryptex()
    assert seed_environment_language(cx, "it") is True
    assert len(cx.calls) == 1
    domain, content, source, salience = cx.calls[0]
    assert domain == "language"
    assert "Italian" in content
    assert source == "genesis"
    assert salience >= 0.9


def test_personality_genesis_includes_mimic_user_language():
    from nls.brain.identity_renderer import DOMAIN_PERSONALITY, get_identity_slot_definitions

    personality = next(
        d["content"] for d in get_identity_slot_definitions() if d["domain"] == DOMAIN_PERSONALITY
    )
    assert "Match the user's language" in personality
    assert "latest message" in personality
    assert "Cryptex" in personality or "environment language" in personality.lower()
