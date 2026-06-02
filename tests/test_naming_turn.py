"""Naming turn — no duplicate birth greeting after 'your name is X'."""

from __future__ import annotations

from nls.identity.agent_identity import (
    detect_name_from_user_input,
    detect_name_from_signals,
    naming_turn_user_prefix,
)


def test_detect_name_from_user_input():
    assert detect_name_from_user_input("Your name is Babo") == "Babo"
    assert detect_name_from_user_input("your name is babo") == "babo"
    assert detect_name_from_user_input("I'll call you Babo") == "Babo"
    assert detect_name_from_user_input("Hello there!") is None


def test_detect_name_from_signals_uses_user_input():
    name = detect_name_from_signals(
        [],
        "Your name is Babo",
        "Thanks!",
        agent_id="test",
    )
    assert name == "Babo"


def test_naming_turn_prefix_forbids_re_greet():
    prefix = naming_turn_user_prefix("Babo")
    assert "Babo" in prefix
    assert "Do NOT repeat" in prefix
    assert "no name" in prefix.lower()
