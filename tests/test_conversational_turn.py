"""Tests for conversational-turn detection (stall / completion)."""

from nls.agentic.orchestration_policy import is_conversational_user_turn


def test_name_assignment_is_conversational():
    assert is_conversational_user_turn("Your name is Babo")


def test_build_brief_is_not_conversational():
    text = (
        "create a repo on GitHub and build the platform end-to-end "
        "with Assembly AI and deploy on Railway"
    )
    assert not is_conversational_user_turn(text)
