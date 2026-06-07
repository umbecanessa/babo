"""Team launch breadcrumb + stall guards after team(create)."""

from nls.agentic.breadcrumbs import BreadcrumbContext, BreadcrumbEngine
from nls.agentic.evaluator import detect_stall
from nls.agentic.orchestration_profile_spec import behavioral_domain_visible_for_profile
from nls.agentic.types import LoopConfig, LoopState


def test_duplicate_team_create_breadcrumb():
    engine = BreadcrumbEngine()
    ctx = BreadcrumbContext(
        tool_name="team",
        action="create",
        is_error=True,
        result_details={
            "action": "create",
            "duplicate_team": True,
            "team_id": "team_5a78e945",
        },
        unlocked_tools=frozenset({"team", "plan"}),
        orchestration_profile="orchestrated",
        is_coordinator=True,
    )
    hint = engine.evaluate(ctx)
    assert hint is not None
    assert "team_5a78e945" in hint
    assert "launch" in hint.lower()
    assert "do not team(create)" in hint.lower()


def test_stall_nudge_when_pending_launch_team():
    state = LoopState(
        orchestration_profile="orchestrated",
        pending_launch_team_id="team_5a78e945",
        tool_call_signatures=["todo:list", "todo:list", "todo:list"],
    )
    msg = detect_stall(state, LoopConfig())
    assert msg is not None
    assert "team_5a78e945" in msg
    assert "launch" in msg.lower()


def test_solo_plan_workflow_hidden_for_orchestrated():
    assert not behavioral_domain_visible_for_profile(
        "solo_plan_workflow", "orchestrated",
    )
    assert behavioral_domain_visible_for_profile(
        "solo_plan_workflow", "solo_structured",
    )
