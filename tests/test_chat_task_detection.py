"""Tests for chat task detection and pseudo tool-call recovery."""

from __future__ import annotations

from server.routes.chat.helpers import (
    _is_task_message,
    _message_implies_agentic_work,
    response_has_pseudo_tool_call,
)


def test_compound_greeting_and_task_is_task_message():
    msg = (
        "Hi! Your name is Babo - so I just created a discord server that we "
        "will use to manage our upcoming community and would love to give you "
        "access as admin so you can help me set it up. how can we do it ?"
    )
    assert _is_task_message(msg) is True
    assert _message_implies_agentic_work(msg) is True


def test_pure_greeting_is_not_task_message():
    assert _is_task_message("Hi! Your name is Babo.") is False
    assert _message_implies_agentic_work("Hey, how are you?") is False


def test_response_has_pseudo_tool_call_detects_clawhub():
    text = (
        "I'll search for a Discord skill to automate this for us.\n\n"
        "clawhub(action='search', query='Discord')"
    )
    assert response_has_pseudo_tool_call(text) is True


def test_response_has_pseudo_tool_call_ignores_normal_prose():
    assert response_has_pseudo_tool_call("I'll search ClawHub for a skill.") is False
