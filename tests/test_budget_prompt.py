"""Tests for orchestrator budget extension prompts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nls.agentic.budget_prompt import (
    HINT_EXPLORE_PARALLEL_READS,
    boost_explore_read_hints,
    classify_budget_response,
    clamp_extension,
    parse_budget_decision,
    should_prompt_user_for_budget,
)


def _cfg(**overrides):
    base = dict(
        prompt_user_on_budget_exhaust=True,
        enable_delegation=True,
        escalate_on_limit=False,
        max_iterations=40,
        max_total_iterations=300,
        max_user_budget_prompts=3,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _state(**overrides):
    base = dict(
        user_budget_prompts=0,
        dispatch_source="user",
        iteration=40,
        wait_only_iterations=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_classify_budget_extend_numeric():
    d = classify_budget_response("20", [10, 20, 40])
    assert d is not None
    assert d.action == "extend"
    assert d.extra_iterations == 20


def test_classify_budget_stop():
    d = classify_budget_response("stop", [10, 20, 40])
    assert d is not None
    assert d.action == "terminate"


def test_classify_budget_affirmative_defaults_to_smallest():
    d = classify_budget_response("yes", [10, 20, 40])
    assert d is not None
    assert d.action == "extend"
    assert d.extra_iterations == 10


def test_classify_budget_yes_please_is_steering_not_extend():
    assert classify_budget_response("yes please", [10, 20, 40]) is None


def test_should_not_prompt_at_total_ceiling():
    assert not should_prompt_user_for_budget(
        "max_iterations",
        _cfg(max_iterations=300, max_total_iterations=300),
        _state(),
        has_active_team=False,
        copilot_queue=object(),
    )


def test_parse_budget_decision_dict():
    d = parse_budget_decision({"action": "extend", "extra_iterations": 40}, [10, 20, 40])
    assert d is not None
    assert d.extra_iterations == 40


def test_should_prompt_user_for_budget_orchestrator():
    assert should_prompt_user_for_budget(
        "max_iterations",
        _cfg(),
        _state(),
        has_active_team=False,
        copilot_queue=object(),
    )


def test_should_not_prompt_sub_agent():
    assert not should_prompt_user_for_budget(
        "max_iterations",
        _cfg(enable_delegation=False),
        _state(),
        has_active_team=False,
        copilot_queue=object(),
    )


def test_should_not_prompt_delegate_escalation_path():
    assert not should_prompt_user_for_budget(
        "max_iterations",
        _cfg(escalate_on_limit=True),
        _state(),
        has_active_team=False,
        copilot_queue=object(),
    )


def test_clamp_extension_respects_total_ceiling():
    cfg = _cfg(max_iterations=290, max_total_iterations=300)
    assert clamp_extension(cfg, 40) == 10


def test_boost_explore_read_hints():
    hints: list[str] = []
    boost_explore_read_hints("Please study the Stadia repo codebase", hints)
    assert HINT_EXPLORE_PARALLEL_READS in hints
