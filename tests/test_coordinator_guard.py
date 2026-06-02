"""Tests for orchestrator pre-delegate guards."""



from __future__ import annotations



from nls.agentic.coordinator_guard import (

    block_executing_mode_escape,

    filter_stale_tactical_goals,

    monitoring_advance_block_message,

    must_delegate_before_impl,

    plan_requires_team_delegation,

    plan_suppresses_raw_delegate,

    pre_delegate_block_message,

    pre_delegate_reason,

    record_team_inspect,

    sync_goals_from_wm,

)

from nls.agentic.plan_store import Plan, PlanStep

from nls.agentic.types import AgentMode, LoopConfig, LoopState





def _plan(

    *,

    delegatable_pending: int = 0,

    delegatable_done: int = 0,

    solo_steps: int = 0,

) -> Plan:

    steps: list[PlanStep] = []

    for i in range(delegatable_pending):

        steps.append(PlanStep(

            id=f"d-p{i}",

            label=f"delegate task {i}",

            delegatable=True,

            status="pending",

        ))

    for i in range(delegatable_done):

        steps.append(PlanStep(

            id=f"d-d{i}",

            label=f"done task {i}",

            delegatable=True,

            status="done",

        ))

    for i in range(solo_steps):

        steps.append(PlanStep(

            id=f"s{i}",

            label=f"solo {i}",

            delegatable=False,

            status="pending",

        ))

    return Plan(id="plan_test", title="test", steps=steps)





def _must(**kwargs):

    defaults = dict(

        plan_requires_team_delegation=False,

        has_active_plan=False,

        has_running_delegates=False,

        has_non_terminal_team=False,

        is_delegate_loop=False,

        orchestrator_recovery=False,

    )

    defaults.update(kwargs)

    state = defaults.pop("state", LoopState(user_input="build"))

    config = defaults.pop("config", LoopConfig(enable_delegation=True))

    return must_delegate_before_impl(state, config, **defaults)





def test_sync_goals_from_wm():

    state = LoopState(user_input="build")

    state.goals = []



    def _wm():

        return ["read prd", "create repo", "deploy app"]



    sync_goals_from_wm(state, _wm)

    assert len(state.goals) == 3





def test_filter_stale_blocker_goals():

    raw = [

        "Build platform",

        "BLOCKER: delegate #3 needs API key",

        "Deploy app",

    ]

    assert len(filter_stale_tactical_goals(raw)) == 2





def test_plan_requires_team_two_pending_delegatable():

    p = _plan(delegatable_pending=2)

    assert plan_requires_team_delegation(p)





def test_plan_simple_no_delegatable():

    p = _plan(solo_steps=3)

    assert not plan_requires_team_delegation(p)





def test_plan_single_delegatable_is_solo():

    p = _plan(delegatable_pending=1, solo_steps=2)

    assert not plan_requires_team_delegation(p)





def test_plan_all_delegatable_done():

    p = _plan(delegatable_done=3, solo_steps=1)

    assert not plan_requires_team_delegation(p)


def test_plan_suppresses_raw_delegate_single_pending():

    p = _plan(delegatable_pending=1, solo_steps=2)

    assert plan_suppresses_raw_delegate(p)

    assert not plan_requires_team_delegation(p)


def test_plan_suppresses_raw_delegate_when_all_delegatable_done():

    p = _plan(delegatable_done=2, solo_steps=1)

    assert not plan_suppresses_raw_delegate(p)


def test_apply_runtime_filter_hides_delegate_when_suppressed():
    from nls.agentic.orchestration_policy import apply_runtime_tool_filter
    from nls.agentic.types import AgentMode, LoopState

    schemas = [
        {"type": "function", "function": {"name": "team"}},
        {"type": "function", "function": {"name": "delegate"}},
        {"type": "function", "function": {"name": "plan"}},
    ]
    unlocked = {"team", "delegate", "plan"}
    state = LoopState(user_input="x")
    state.active_mode = AgentMode.DELEGATING

    filtered, tools = apply_runtime_tool_filter(
        schemas,
        unlocked,
        AgentMode.DELEGATING,
        state,
        None,
        suppress_raw_delegate=True,
    )
    names = {s["function"]["name"] for s in filtered}
    assert "delegate" not in names
    assert "team" in names
    assert "delegate" not in tools





def test_must_delegate_when_goals_but_no_plan():

    state = LoopState(user_input="build platform")

    state.active_mode = AgentMode.PLANNING

    state.orchestration_profile = "orchestrated"

    state.goals = ["read prd", "create repo", "deploy"]

    assert _must(state=state, has_active_plan=False)





def test_must_delegate_false_stale_goals_with_active_plan_absent():
    """After plan archived, stale WM goals must not block solo patch writes."""
    state = LoopState(user_input="fix frontend")
    state.active_mode = AgentMode.EXECUTING
    state.orchestration_profile = "orchestrated"
    state.goals = filter_stale_tactical_goals([
        "read prd", "create repo", "deploy", "BLOCKER: delegate #3",
    ])
    assert len(state.goals) == 3
    assert _must(state=state, has_active_plan=False)
    state.goals = ["patch EvaluationView", "wire analyze endpoint"]
    assert not _must(state=state, has_active_plan=False)





def test_must_delegate_false_orchestrator_recovery_with_team_plan():
    """After accept_partial/disband, recovery allows solo salvage writes."""
    state = LoopState(user_input="build")
    state.goals = ["build", "deploy", "platform"]
    assert not _must(
        state=state,
        plan_requires_team_delegation=True,
        orchestrator_recovery=True,
    )


def test_must_delegate_false_orchestrator_recovery_solo():
    state = LoopState(user_input="fix ui")
    state.active_mode = AgentMode.EXECUTING
    assert not _must(
        state=state,
        plan_requires_team_delegation=False,
        orchestrator_recovery=True,
    )





def test_must_delegate_when_team_plan_pending():

    state = LoopState(user_input="build")

    state.active_mode = AgentMode.EXECUTING

    state.orchestration_profile = "orchestrated"

    assert _must(state=state, plan_requires_team_delegation=True)





def test_must_delegate_false_simple_active_plan():

    state = LoopState(user_input="fix typo")

    state.active_mode = AgentMode.EXECUTING

    assert not _must(state=state, has_active_plan=True)





def test_must_delegate_false_when_evaluating():

    state = LoopState(user_input="build")

    state.active_mode = AgentMode.EVALUATING

    assert not _must(state=state, plan_requires_team_delegation=True)





def test_must_delegate_false_when_team_active():

    state = LoopState(user_input="build")

    assert not _must(

        state=state,

        plan_requires_team_delegation=True,

        has_non_terminal_team=True,

    )





def test_block_executing_escape_team_plan():

    msg = block_executing_mode_escape(

        AgentMode.EXECUTING,

        active_mode=AgentMode.PLANNING,

        plan_requires_team_delegation=True,

        has_non_terminal_team=False,

        enable_delegation=True,

        is_delegate_loop=False,

        orchestration_profile="orchestrated",

    )

    assert msg is not None

    assert "Blocked" in msg





def test_block_executing_escape_from_delegating():

    msg = block_executing_mode_escape(

        AgentMode.EXECUTING,

        active_mode=AgentMode.DELEGATING,

        plan_requires_team_delegation=True,

        has_non_terminal_team=False,

        enable_delegation=True,

        is_delegate_loop=False,

        orchestration_profile="orchestrated",

    )

    assert msg is not None





def test_block_executing_allowed_from_evaluating():

    assert block_executing_mode_escape(

        AgentMode.EXECUTING,

        active_mode=AgentMode.EVALUATING,

        plan_requires_team_delegation=True,

        has_non_terminal_team=False,

        enable_delegation=True,

        is_delegate_loop=False,

    ) is None





def test_block_executing_allowed_recovery():

    assert block_executing_mode_escape(

        AgentMode.EXECUTING,

        active_mode=AgentMode.PLANNING,

        plan_requires_team_delegation=True,

        has_non_terminal_team=False,

        enable_delegation=True,

        is_delegate_loop=False,

        orchestrator_recovery=True,

    ) is None





def test_block_executing_allowed_simple_plan():

    assert block_executing_mode_escape(

        AgentMode.EXECUTING,

        active_mode=AgentMode.PLANNING,

        plan_requires_team_delegation=False,

        has_non_terminal_team=False,

        enable_delegation=True,

        is_delegate_loop=False,

    ) is None





def test_pre_delegate_blocks_write_in_executing():

    msg = pre_delegate_block_message(

        "write",

        {"path": "app/package.json", "content": "{}"},

        active_mode=AgentMode.EXECUTING,

        block_reason="team_plan",

        orchestration_profile="orchestrated",

    )

    assert msg is not None

    assert "team waves" in msg





def test_pre_delegate_build_goals_message():

    msg = pre_delegate_block_message(

        "write",

        {"path": "x", "content": "y"},

        active_mode=AgentMode.PLANNING,

        block_reason="build_goals",

        orchestration_profile="orchestrated",

    )

    assert msg is not None

    assert "no active plan" in msg.lower() or "plan" in msg.lower()





def test_pre_delegate_allows_write_in_evaluating():

    assert pre_delegate_block_message(

        "write",

        {"path": "app/package.json", "content": "{}"},

        active_mode=AgentMode.EVALUATING,

        block_reason="team_plan",

    ) is None





def test_pre_delegate_allows_write_in_recovery():

    assert pre_delegate_block_message(

        "write",

        {"path": "app/package.json", "content": "{}"},

        active_mode=AgentMode.EXECUTING,

        block_reason="team_plan",

        orchestrator_recovery=True,

    ) is None





def test_pre_delegate_blocks_gh_repo_in_executing():

    msg = pre_delegate_block_message(

        "bash",

        {"command": "gh repo create foo --public"},

        active_mode=AgentMode.EXECUTING,

        block_reason="tactical_goals",

        orchestration_profile="orchestrated",

    )

    assert msg is not None





def test_pre_delegate_allows_plan():

    assert pre_delegate_block_message(

        "plan",

        {"action": "create"},

        active_mode=AgentMode.PLANNING,

        block_reason="team_plan",

    ) is None





def test_pre_delegate_reason_tactical_goals():

    state = LoopState(user_input="x")

    state.orchestration_profile = "orchestrated"

    state.goals = ["a", "b", "c"]

    assert pre_delegate_reason(

        state,

        LoopConfig(enable_delegation=True),

        plan_requires_team_delegation=False,

        has_active_plan=False,

        has_running_delegates=False,

        has_non_terminal_team=False,

        is_delegate_loop=False,

    ) == "tactical_goals"


def test_monitoring_advance_requires_recent_inspect():

    state = LoopState(user_input="x")

    state.active_mode = AgentMode.MONITORING

    blocked = monitoring_advance_block_message(state, "team_abc")

    assert blocked is not None

    assert "inspect" in blocked

    record_team_inspect(state, "team_abc")

    assert monitoring_advance_block_message(state, "team_abc") is None


def test_monitoring_advance_guard_not_in_executing():

    state = LoopState(user_input="x")

    state.active_mode = AgentMode.EXECUTING

    assert monitoring_advance_block_message(state, "team_abc") is None


