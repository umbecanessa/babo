"""Tests for orchestration wake coalescing."""

from __future__ import annotations

from nls.agentic.wake_coordination import (
    completion_review_source,
    is_completion_review_source,
    is_member_escalation_source,
    member_escalation_source,
    parse_completion_review_team_id,
)


def test_completion_review_source_batched():
    assert completion_review_source("team_abc") == "team_completion_review:team_abc"


def test_member_escalation_source_per_delegate():
    assert member_escalation_source("team_abc", 3) == "team_member_escalation:team_abc:3"
    assert is_member_escalation_source("team_member_escalation:team_abc:3")
    assert not is_member_escalation_source("team_completion_review:team_abc")


def test_parse_completion_review_team_id_legacy_and_batched():
    assert parse_completion_review_team_id(
        "team_completion_review:team_95c4fabe:3",
    ) == "team_95c4fabe"
    assert parse_completion_review_team_id(
        "team_completion_review:team_95c4fabe",
    ) == "team_95c4fabe"
