"""Tests for profile-aware guard strictness."""

from __future__ import annotations

from nls.agentic.breadcrumbs import BreadcrumbContext, BreadcrumbEngine
from nls.agentic.coordinator_guard import (
    must_delegate_before_impl,
    pre_delegate_block_message,
    pre_delegate_reason,
)
from nls.agentic.goals import TurnTriage
from nls.agentic.profile_guard_policy import (
    apply_structured_hint_caps,
    em_pre_delegate_blocks_enabled,
    enrich_instruction_skill_hints,
    enrich_native_skill_hints,
    inject_prompt_structured_hints,
    normalize_goals_for_profile,
    normalize_profile,
    tools_denied_by_hints,
)
from nls.agentic.skill_discovery_boost import trigger_skill_discovery_boost
from nls.agentic.types import AgentMode, LoopConfig, LoopState


def test_normalize_profile_defaults_solo():
    assert normalize_profile(None) == "solo_structured"
    assert normalize_profile("bogus") == "solo_structured"


def test_em_pre_delegate_off_for_solo_structured():
    assert not em_pre_delegate_blocks_enabled(
        "solo_structured",
        plan_requires_team_delegation=False,
    )


def test_em_pre_delegate_on_for_orchestrated_team_plan():
    assert em_pre_delegate_blocks_enabled(
        "orchestrated",
        plan_requires_team_delegation=True,
    )


def test_normalize_goals_merges_for_solo_preserves_language():
    goals = ["crear repositorio", "inicializar git", "commit"]
    merged = normalize_goals_for_profile(goals, "solo_structured")
    assert merged == ["crear repositorio"]


def test_normalize_goals_unchanged_for_orchestrated():
    goals = ["a", "b", "c"]
    assert normalize_goals_for_profile(goals, "orchestrated") == goals


def test_structured_hint_forbid_team_caps_profile():
    assert apply_structured_hint_caps(
        "orchestrated", ["forbid:team"],
    ) == "solo_structured"


def test_turn_triage_cap_from_structured_hints():
    triage = TurnTriage(
        profile="orchestrated",
        hints=["forbid:team"],
    )
    triage.cap_profile_from_hints()
    assert triage.profile == "solo_structured"


def test_solo_profile_skips_tactical_goals_block():
    state = LoopState(user_input="git smoke")
    state.orchestration_profile = "solo_structured"
    state.goals = ["create repo", "init git", "commit"]
    reason = pre_delegate_reason(
        state,
        LoopConfig(enable_delegation=True),
        plan_requires_team_delegation=False,
        has_active_plan=False,
        has_running_delegates=False,
        has_non_terminal_team=False,
        is_delegate_loop=False,
    )
    assert reason is None


def test_orchestrated_profile_keeps_tactical_goals_block():
    state = LoopState(user_input="build")
    state.orchestration_profile = "orchestrated"
    state.goals = ["a", "b", "c"]
    reason = pre_delegate_reason(
        state,
        LoopConfig(enable_delegation=True),
        plan_requires_team_delegation=False,
        has_active_plan=False,
        has_running_delegates=False,
        has_non_terminal_team=False,
        is_delegate_loop=False,
    )
    assert reason == "tactical_goals"


def test_solo_profile_no_team_plan_fallback_block():
    msg = pre_delegate_block_message(
        "bash",
        {"command": "git init"},
        active_mode=AgentMode.EXECUTING,
        block_reason=None,
        orchestration_profile="solo_structured",
    )
    assert msg is None


def test_orchestrated_profile_blocks_git_bash():
    msg = pre_delegate_block_message(
        "bash",
        {"command": "gh repo create foo --public"},
        active_mode=AgentMode.EXECUTING,
        block_reason="tactical_goals",
        orchestration_profile="orchestrated",
    )
    assert msg is not None


def test_must_delegate_false_for_solo_multi_goal():
    state = LoopState(user_input="git")
    state.orchestration_profile = "solo_structured"
    state.goals = ["create repo", "init git", "commit"]
    assert not must_delegate_before_impl(
        state,
        LoopConfig(enable_delegation=True),
        plan_requires_team_delegation=False,
        has_active_plan=False,
        has_running_delegates=False,
        has_non_terminal_team=False,
        is_delegate_loop=False,
    )


def test_solo_todo_breadcrumb_without_plan():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="todo",
        action="add",
        unlocked_tools=frozenset({"todo"}),
        orchestration_profile="solo_structured",
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "todo(action='list'" in hint


def test_solo_todo_no_breadcrumb_when_plan_unlocked():
    """Solo profile should not nudge todo→plan even if plan tool exists."""
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="todo",
        action="add",
        unlocked_tools=frozenset({"plan", "todo"}),
        orchestration_profile="solo_structured",
    )
    assert engine.evaluate(ctx) is None


def test_solo_plan_create_breadcrumb():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="plan",
        action="create",
        unlocked_tools=frozenset({"plan", "write", "bash", "todo"}),
        orchestration_profile="solo_structured",
        result_details={
            "plan_id": "plan_abc123",
            "steps": [{"label": "Scaffold monorepo"}],
        },
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "SOLO workflow" in hint
    assert "no team" in hint.lower()
    assert "plan_abc123" in hint
    assert "switch_mode(mode='executing')" in hint


def test_solo_plan_create_skipped_on_error():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="plan",
        action="create",
        is_error=True,
        unlocked_tools=frozenset({"plan"}),
        orchestration_profile="solo_structured",
        result_details={"plan_id": "plan_x"},
    )
    assert engine.evaluate(ctx) is None


def test_forbid_tools_hint_denies_lookup_tools():
    denied = tools_denied_by_hints(["forbid:tools"])
    assert "web_search" in denied
    assert "clawhub" in denied
    assert "communicate" not in denied


def test_cap_profile_from_hints_keeps_goals_on_forbid_tools():
    triage = TurnTriage(
        profile="solo_structured",
        goals=["Draft landlord email"],
        hints=["forbid:tools"],
    )
    triage.cap_profile_from_hints()
    assert triage.profile == "conversational"
    assert triage.goals == ["Draft landlord email"]


def test_conversational_lookup_breadcrumb():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="web_search",
        action="search",
        orchestration_profile="conversational",
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "answer in chat" in hint.lower()


def test_solo_profile_skips_team_plan_block():
    state = LoopState(user_input="build platform")
    state.orchestration_profile = "solo_structured"
    reason = pre_delegate_reason(
        state,
        LoopConfig(enable_delegation=True),
        plan_requires_team_delegation=True,
        has_active_plan=True,
        has_running_delegates=False,
        has_non_terminal_team=False,
        is_delegate_loop=False,
    )
    assert reason is None


def test_detect_stall_uses_solo_nudge_without_clawhub():
    from nls.agentic.evaluator import detect_stall

    state = LoopState(user_input="todo smoke")
    state.orchestration_profile = "solo_structured"
    state.consecutive_errors = 2
    state.tool_history = [("todo", True), ("todo", True)]
    state.last_error_preview = "duplicate todo"
    msg = detect_stall(state, LoopConfig())
    assert msg is not None
    assert "clawhub" not in msg.lower()
    assert "discover_tools" not in msg.lower()


def test_skill_discovery_boost_skipped_for_solo():
    class _Hooks:
        _loop_state_ref = {}

    trigger_skill_discovery_boost(
        _Hooks(),
        iteration=1,
        orchestration_profile="solo_structured",
    )
    assert _Hooks._loop_state_ref == {}


def test_rewrite_blocked_breadcrumb():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="write",
        action="",
        is_error=True,
        result_details={"rewrite_blocked": True, "path": "backend/main.py"},
        orchestration_profile="solo_structured",
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "delete_file" in hint
    assert "edit()" in hint


def test_forbid_plan_hint_from_delegate_only_prompt():
    hints: list[str] = []
    inject_prompt_structured_hints(
        "Delegate research using delegate. Do NOT implement code yourself — "
        "only delegate and synthesize.",
        hints,
    )
    assert "forbid:plan" in hints
    assert tools_denied_by_hints(hints) == frozenset({"plan", "todo"})


def test_enrich_instruction_skill_setup_hint():
    hints: list[str] = []
    enrich_instruction_skill_hints(
        "Here is the bot token — configure the integration",
        ["Configure bot with provided credentials"],
        hints,
    )
    assert "setup:instruction_skill" in hints
    assert any("skill_configure" in h.lower() for h in hints)


def test_enrich_instruction_skill_skips_duplicate():
    hints = ["setup:instruction_skill"]
    enrich_instruction_skill_hints("configure bot", ["Configure bot"], hints)
    assert hints.count("setup:instruction_skill") == 1


def test_enrich_native_skill_hint_for_nls_python_skill():
    hints: list[str] = []
    enrich_native_skill_hints(
        "create a dedicated nls python skill for Discord moderation",
        ["Build native Discord moderator skill"],
        hints,
    )
    assert "setup:native_skill" in hints
    assert "setup:instruction_skill" not in hints


def test_enrich_instruction_skill_skips_when_native_authoring():
    hints: list[str] = []
    enrich_instruction_skill_hints(
        "configure bot token for nls python skill build",
        ["Build native skill"],
        hints,
    )
    assert "setup:instruction_skill" not in hints


def test_enrich_native_skill_hint_for_active_discord_moderator():
    hints: list[str] = []
    enrich_native_skill_hints(
        "become an active moderator on Discord, always listening when tagged",
        ["Discord moderator bot"],
        hints,
    )
    assert "setup:native_skill" in hints
    assert any("discord-channel" in h for h in hints)
    assert any("github.com/umbecanessa/babo" in h for h in hints)


def test_enrich_instruction_skips_active_channel():
    hints: list[str] = []
    enrich_instruction_skill_hints(
        "always listening Discord moderator when tagged",
        ["Discord moderator"],
        hints,
    )
    assert "setup:instruction_skill" not in hints
