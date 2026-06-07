"""Team launch breadcrumb + guards after team(create)."""

from nls.agentic.breadcrumbs import BreadcrumbContext, BreadcrumbEngine
from nls.agentic.orchestration_policy import (
    maybe_pending_launch_wrong_tool_nudge,
    pending_launch_wrong_tool_message,
)
from nls.agentic.orchestration_profile_spec import behavioral_domain_visible_for_profile


def test_duplicate_team_create_breadcrumb():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="team",
        action="create",
        is_error=True,
        result_details={
            "action": "create",
            "duplicate_team": True,
            "team_id": "team_88370db2",
        },
        unlocked_tools=frozenset({"team", "plan"}),
        orchestration_profile="orchestrated",
        is_coordinator=True,
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "team_88370db2" in hint
    assert "launch" in hint.lower()


def test_todo_duplicate_skips_plan_breadcrumb_when_team_pending():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="todo",
        action="add",
        is_error=False,
        result_details={
            "action": "add",
            "todo_id": "1cc36940",
            "skipped_duplicate": True,
        },
        unlocked_tools=frozenset({"team", "plan"}),
        orchestration_profile="orchestrated",
        active_plan_id="plan_7db076e0",
        pending_launch_team_id="team_88370db2",
    )
    assert engine.evaluate(ctx) is None


def test_plan_create_blocked_points_to_launch():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="plan",
        action="create",
        is_error=True,
        result_details={
            "action": "create",
            "already_existed": True,
            "plan_id": "plan_7db076e0",
        },
        unlocked_tools=frozenset({"team", "plan"}),
        orchestration_profile="orchestrated",
        pending_launch_team_id="team_88370db2",
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "team_88370db2" in hint
    assert "launch" in hint.lower()
    assert "solo" not in hint.lower()


def test_plan_create_blocked_solo_no_team():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="plan",
        action="create",
        is_error=True,
        result_details={
            "action": "create",
            "already_existed": True,
            "plan_id": "plan_abc",
        },
        unlocked_tools=frozenset({"plan"}),
        orchestration_profile="solo_structured",
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "plan_abc" in hint
    assert "team(action" not in hint
    assert "solo_structured" in hint


def test_pending_launch_wrong_tool_nudge():
    msg = pending_launch_wrong_tool_message(
        "todo", "add", pending_team_id="team_88370db2",
    )
    assert msg is not None
    assert "launch" in msg.lower()
    assert pending_launch_wrong_tool_message(
        "team", "launch", pending_team_id="team_88370db2",
    ) is None


def test_create_skipped_wave_breadcrumb():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="team",
        action="create",
        is_error=True,
        result_details={
            "action": "create",
            "skipped_pending_wave": True,
            "plan_id": "plan_icf",
            "recommended_wave": 2,
        },
        unlocked_tools=frozenset({"team", "plan"}),
        orchestration_profile="orchestrated",
        is_coordinator=True,
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "wave=2" in hint
    assert "deploy" in hint.lower()


def test_create_duplicate_recreate_breadcrumb():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="team",
        action="create",
        is_error=True,
        result_details={
            "action": "create",
            "duplicate_wave_recreate": True,
            "plan_id": "plan_icf",
            "recommended_wave": 2,
        },
        unlocked_tools=frozenset({"team", "plan"}),
        orchestration_profile="orchestrated",
        is_coordinator=True,
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "disband" in hint.lower() or "recreat" in hint.lower()


def test_create_deploy_blocked_breadcrumb():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="team",
        action="create",
        is_error=True,
        result_details={
            "action": "create",
            "deploy_blocked": True,
            "plan_id": "plan_icf",
            "recommended_wave": 2,
        },
        unlocked_tools=frozenset({"team", "plan"}),
        orchestration_profile="orchestrated",
        is_coordinator=True,
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "wave=2" in hint
    assert "deploy" in hint.lower()


def test_pending_launch_nudge_orchestrated_only():
    assert maybe_pending_launch_wrong_tool_nudge(
        orchestration_profile="solo_structured",
        tool_name="todo",
        action="add",
        pending_team_id="team_88370db2",
        is_delegate_loop=False,
    ) is None
    assert maybe_pending_launch_wrong_tool_nudge(
        orchestration_profile="orchestrated",
        tool_name="todo",
        action="add",
        pending_team_id="team_88370db2",
        is_delegate_loop=False,
    ) is not None


def test_solo_plan_workflow_hidden_for_orchestrated():
    assert not behavioral_domain_visible_for_profile(
        "solo_plan_workflow", "orchestrated",
    )
    assert behavioral_domain_visible_for_profile(
        "solo_plan_workflow", "solo_structured",
    )
