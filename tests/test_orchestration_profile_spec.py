"""Tests for ProfileOrchestrationSpec and profile-aware composition."""

from __future__ import annotations

from nls.agentic.orchestration_profile_spec import (
    apply_tool_deny,
    behavioral_domain_visible_for_profile,
    get_profile_spec,
    normalize_profile,
    should_auto_mark_delegatable,
)
from nls.agentic.profile_guard_policy import (
    em_pre_delegate_blocks_enabled,
    normalize_goals_for_profile,
)


def test_normalize_profile_defaults_solo():
    assert normalize_profile(None) == "solo_structured"
    assert normalize_profile("bogus") == "solo_structured"


def test_conversational_denies_plan_and_team():
    allowed = frozenset({"plan", "team", "communicate", "write"})
    filtered = apply_tool_deny(allowed, "conversational")
    assert filtered == frozenset({"communicate"})


def test_solo_structured_allows_plan_denies_team():
    allowed = frozenset({"plan", "team", "write", "communicate"})
    filtered = apply_tool_deny(allowed, "solo_structured")
    assert "plan" in filtered
    assert "team" not in filtered


def test_em_guards_off_for_solo():
    assert not em_pre_delegate_blocks_enabled(
        "solo_structured", plan_requires_team_delegation=False,
    )


def test_em_guards_on_for_orchestrated_team_plan():
    assert em_pre_delegate_blocks_enabled(
        "orchestrated", plan_requires_team_delegation=True,
    )


def test_solo_hides_em_behavioral_domains():
    assert not behavioral_domain_visible_for_profile(
        "team_orchestration", "solo_structured",
    )
    assert behavioral_domain_visible_for_profile(
        "solo_plan_workflow", "solo_structured",
    )


def test_conversational_shows_answer_in_prose_only():
    assert behavioral_domain_visible_for_profile(
        "answer_in_prose", "conversational",
    )
    assert not behavioral_domain_visible_for_profile(
        "team_orchestration", "conversational",
    )


def test_orchestrated_auto_delegatable_three_steps():
    assert should_auto_mark_delegatable("orchestrated", 3)
    assert not should_auto_mark_delegatable("solo_structured", 3)


def test_normalize_goals_merges_for_solo():
    goals = ["crear repositorio", "inicializar git", "commit"]
    merged = normalize_goals_for_profile(goals, "solo_structured")
    assert merged == ["crear repositorio"]


def test_conversational_ring_hides_orchestration_without_plan():
    spec = get_profile_spec("conversational")
    assert not spec.ring_visible("orchestration", has_active_plan=False)
    assert not spec.ring_visible("tactical_goals", has_active_plan=False)


def test_solo_orchestration_ring_only_with_plan():
    spec = get_profile_spec("solo_structured")
    assert not spec.ring_visible("orchestration", has_active_plan=False)
    assert spec.ring_visible("orchestration", has_active_plan=True)


def test_instruction_tech_stack_domain_gated_by_plan():
    spec = get_profile_spec("solo_structured")
    assert spec.instruction_domain_visible(
        "plan_requirements", has_active_plan=True,
    )
    assert not spec.instruction_domain_visible(
        "plan_requirements", has_active_plan=False,
    )
