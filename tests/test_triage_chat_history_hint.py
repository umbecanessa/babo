"""Triage emits lookup:chat_history without English regex heuristics."""

from __future__ import annotations

from nls.agentic.goals import TurnTriage, _parse_triage_dict


def test_parse_triage_lookup_chat_history_hint():
    triage = _parse_triage_dict({
        "intent": "CHAT_THINK",
        "thinking": True,
        "profile": "conversational",
        "goals": [],
        "hints": ["lookup:chat_history"],
        "deferred": [],
    })
    assert "lookup:chat_history" in triage.hints
    assert triage.goals == []


def test_parse_triage_multilingual_prior_chat_example_shape():
    triage = _parse_triage_dict({
        "intent": "CHAT_THINK",
        "thinking": True,
        "profile": "conversational",
        "goals": [],
        "hints": ["lookup:chat_history"],
        "deferred": [],
    })
    assert isinstance(triage, TurnTriage)
    assert triage.profile == "conversational"
