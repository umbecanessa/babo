"""Tests for context supersession (supersede-by-intent)."""

from __future__ import annotations

import json

from nls.agentic.compactor import CompactionAnchor
from nls.agentic.context_supersession import (
    SupersessionPolicy,
    apply_supersession,
    build_intent_key,
    register_tool_msg_outcome,
    resolve_deliverable_paths,
    resolve_supersession_policy,
    sync_open_blockers,
    _pair_tool_turns,
)
from nls.agentic.types import AgentMode, LoopState


def _ctx_with_bash_attempts(fail_first: bool = True) -> list[dict]:
    ctx: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run test"},
    ]
    for i, err in enumerate([True, True, not fail_first]):
        tc_id = f"call_{i}"
        ctx.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": tc_id,
                "type": "function",
                "function": {
                    "name": "bash",
                    "arguments": json.dumps({"command": "python -m pytest"}),
                },
            }],
        })
        body = "Error: ModuleNotFoundError: app" if err else "ok"
        ctx.append({"role": "tool", "tool_call_id": tc_id, "content": body * 20})
    return ctx


def test_build_intent_key_bash_normalizes():
    k1 = build_intent_key("bash", {"command": 'cd foo && python test.py "a"'})
    k2 = build_intent_key("bash", {"command": 'cd foo && python test.py "b"'})
    assert k1 == k2
    assert k1.startswith("bash:")


def test_delegate_aggressive_supersedes_bash_failures():
    ctx = _ctx_with_bash_attempts(fail_first=True)
    state = LoopState()
    stats = apply_supersession(
        ctx,
        policy=SupersessionPolicy.DELEGATE_AGGRESSIVE,
        state=state,
        start_index=0,
    )
    assert stats.stubs_applied >= 1
    tool_msgs = [m for m in ctx if m.get("role") == "tool"]
    assert any(m["content"].startswith("[superseded") for m in tool_msgs[:-1])
    assert not tool_msgs[-1]["content"].startswith("[superseded")


def test_completion_review_frozen_skips_supersession():
    ctx = _ctx_with_bash_attempts()
    stats = apply_supersession(
        ctx,
        policy=SupersessionPolicy.COMPLETION_REVIEW_FROZEN,
        start_index=0,
    )
    assert stats.stubs_applied == 0


def test_resolve_policy_frozen_on_pending_reviews():
    p = resolve_supersession_policy(
        enabled=True,
        is_delegate_loop=False,
        has_pending_completion_reviews=True,
    )
    assert p == SupersessionPolicy.COMPLETION_REVIEW_FROZEN


def test_delegate_not_frozen_when_pending_reviews():
    """Running delegates must keep aggressive supersession during EM review."""
    p = resolve_supersession_policy(
        enabled=True,
        is_delegate_loop=True,
        has_pending_completion_reviews=True,
    )
    assert p == SupersessionPolicy.DELEGATE_AGGRESSIVE


def test_resolve_deliverable_paths_from_plan():
    from types import SimpleNamespace

    step = SimpleNamespace(
        owned_paths=["backend/app/"],
        output_files=["docs/report.md"],
    )
    plan = SimpleNamespace(steps=[step])
    store = SimpleNamespace(find_active=lambda: plan)
    tool = SimpleNamespace(_store=store)
    paths = resolve_deliverable_paths(tool)
    assert "backend/app/" in paths
    assert "docs/report.md" in paths


def test_bash_soft_error_used_for_supersession():
    from nls.tools.agent_tools.base import ToolResult

    ctx = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "bash",
                    "arguments": '{"command": "gh pr list"}',
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "not logged in to github",
        },
    ]
    state = LoopState()
    register_tool_msg_outcome(
        state, 2, "bash",
        ToolResult(content="not logged in to github", is_error=False),
        args={"command": "gh pr list"},
    )
    records = _pair_tool_turns(ctx, start_index=0, state=state)
    assert len(records) == 1
    assert records[0].is_error is True


def test_sync_open_blockers_completion_review():
    from types import SimpleNamespace

    anchor = CompactionAnchor()
    tm = SimpleNamespace(has_pending_completion_reviews=lambda: True)
    sync_open_blockers(anchor, state=LoopState(), team_manager=tm)
    assert anchor.open_blockers
    assert "Completion review" in anchor.open_blockers[0]


def test_resolve_policy_delegate_aggressive():
    p = resolve_supersession_policy(
        enabled=True,
        is_delegate_loop=True,
    )
    assert p == SupersessionPolicy.DELEGATE_AGGRESSIVE


def test_read_max_chars_intent_key_distinct():
    k1 = build_intent_key("read", {"path": "docs/a.md"})
    k2 = build_intent_key("read", {"path": "docs/a.md", "max_chars": 50000})
    assert k1 != k2


def test_supersession_records_anchor():
    ctx = _ctx_with_bash_attempts()
    anchor = CompactionAnchor()
    apply_supersession(
        ctx,
        policy=SupersessionPolicy.DELEGATE_AGGRESSIVE,
        anchor=anchor,
        start_index=0,
    )
    assert anchor.superseded_attempts
