"""Tests for agent naming detection."""

from __future__ import annotations

from nls.identity.agent_identity import (
    detect_name_from_signals,
    detect_name_from_user_input,
)


def test_detect_name_from_user_input_rejects_yours():
    assert detect_name_from_user_input("your name is yours") is None
    assert detect_name_from_user_input("your name is Babo") == "Babo"
    assert detect_name_from_user_input("your name is babo") == "babo"


def test_detect_name_multilingual_patterns():
    assert detect_name_from_user_input("ti chiami Luna") == "Luna"
    assert detect_name_from_user_input("il tuo nome è Marco") == "Marco"
    assert detect_name_from_user_input("tu t'appelles Sophie") == "Sophie"
    assert detect_name_from_user_input("te llamas Diego") == "Diego"
    assert detect_name_from_user_input("du heißt Klaus") == "Klaus"
    assert detect_name_from_user_input("ich nenne dich Freya") == "Freya"


def test_learn_signal_does_not_rename_established_agent():
    signals = [{
        "type": "LEARN",
        "domain": "Agent.Self.Name",
        "content": "The agent name is Yours",
        "pipe_fact": "",
    }]
    assert detect_name_from_signals(
        signals,
        user_input="how are you?",
        response="fine",
        current_name="Babo",
    ) is None


def test_user_explicit_rename_still_works():
    assert detect_name_from_signals(
        [],
        user_input="your name is Kogaea",
        response="Thanks!",
        current_name="Babo",
    ) == "Kogaea"


def test_repeated_same_name_is_noop():
    assert detect_name_from_signals(
        [],
        user_input="your name is Babo",
        response="Okay",
        current_name="Babo",
    ) is None
