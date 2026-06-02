"""Session resume guidance and assessment-loop stall detection."""

from pathlib import Path
from unittest.mock import MagicMock

from nls.agentic.evaluator import detect_stall
from nls.agentic.plan_store import Plan, PlanStep
from nls.agentic.resume_guidance import (
    build_session_resume_guidance,
    user_requests_session_resume,
)
from nls.agentic.team_manager import Team, TeamManager, TeamMember
from nls.agentic.types import AgentMode, LoopConfig, LoopState
from nls.tools.agent_tools.plan import PlanStore


def test_user_requests_session_resume_detects_continue_phrases():
    assert user_requests_session_resume("hey good morning, where do we stand?")
    assert user_requests_session_resume("let's continue from where we left off")
    assert not user_requests_session_resume("ok")


def test_build_session_resume_guidance_lists_next_actions():
    plan = Plan(
        id="plan_x",
        title="App",
        steps=[
            PlanStep(id="s1", label="Scaffold", status="done", delegatable=True),
            PlanStep(id="s2", label="Deploy", status="pending", delegatable=True),
        ],
    )
    msg = build_session_resume_guidance(plan)
    assert "SESSION RESUME" in msg
    assert "Deploy" in msg
    assert "plan(action='read'" in msg
    assert "Do NOT repeat" in msg


def test_detect_stall_assessment_loop_ic_rescan():
    """Post-crash read-only re-scan with repeated status text."""
    state = LoopState(user_input="continue")
    state.orchestration_profile = "orchestrated"
    state.active_mode = AgentMode.EXECUTING
    state.consecutive_text_only = 2
    state.total_tool_calls = 10
    config = LoopConfig(max_iterations=40)
    for i in range(10):
        state.tool_history.append(("read", False))
        state.tool_call_signatures.append(f"read:path=file{i}")
    msg = detect_stall(state, config)
    assert msg is not None
    assert "re-assessing" in msg


def test_detect_stall_skips_team_inspect_read_monitoring():
    """Legitimate EM supervision: team + reads should not stall."""
    state = LoopState(user_input="check wave")
    state.active_mode = AgentMode.MONITORING
    state.consecutive_text_only = 1
    config = LoopConfig(max_iterations=40)
    pattern = [
        "team", "read", "read", "read",
        "team", "read", "read", "read",
    ]
    for name in pattern:
        state.tool_history.append((name, False))
        state.tool_call_signatures.append(f"{name}:x")
    msg = detect_stall(state, config)
    assert msg is None or "re-assessing" not in (msg or "")


def test_reconcile_skips_terminal_teams_without_running_members(tmp_path: Path):
    """reconcile_with_delegates syncs running delegates — not implicit terminalization."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    store = PlanStore(agent_dir)
    store.save(Plan(id="plan_r", title="R", project_dir="p", steps=[]))
    tm = TeamManager(agent_dir, store, delegate_manager=MagicMock())
    team = Team(
        id="team_stale",
        name="Wave 0",
        plan_id="plan_r",
        status="active",
        members=[
            TeamMember(step_id="s1", task="a", status="done", delegate_number=1),
            TeamMember(step_id="s2", task="b", status="failed", delegate_number=2),
        ],
    )
    tm._teams[team.id] = team
    changed = tm.reconcile_with_delegates()
    assert changed == 0
    assert team.status == "active"
