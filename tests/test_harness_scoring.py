"""Tests for scenario harness scoring helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "run_agent_scenarios",
    _REPO / "scripts" / "run-agent-scenarios.py",
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["run_agent_scenarios"] = _mod
_spec.loader.exec_module(_mod)

_artifact_text_contains = _mod._artifact_text_contains
_artifact_tools_satisfied = _mod._artifact_tools_satisfied
_is_spurious_agentic_complete = _mod._is_spurious_agentic_complete
score_pass = _mod.score_pass


def test_spurious_scheduler_abort_ignored():
    msg = {
        "exit_reason": "user_abort",
        "aborted": True,
        "final_response": "",
    }
    assert _is_spurious_agentic_complete(msg, set())


def test_spurious_autonomous_preempt_ignored():
    msg = {
        "exit_reason": "user_abort",
        "aborted": True,
        "autonomous": True,
        "final_response": "[Loop stopped: user_abort. 1 iterations, 0 tool calls.]",
    }
    assert _is_spurious_agentic_complete(msg, set())


def test_real_abort_with_tools_not_spurious():
    msg = {"exit_reason": "user_abort", "aborted": True, "final_response": "oops"}
    assert not _is_spurious_agentic_complete(msg, {"bash"})


def test_artifact_file_satisfies_response_contains(tmp_path):
    agent_dir = tmp_path / "agent"
    ws = agent_dir / "workspace"
    ws.mkdir(parents=True)
    (ws / "res-02.md").write_text(
        "See https://example.com/asyncio for details.\n",
        encoding="utf-8",
    )
    cfg = {
        "pass": {
            "tools_any": ["web_fetch", "write"],
            "response_contains": "http",
            "artifact_files": ["res-02.md"],
        }
    }
    passed, ok, bad = score_pass(
        cfg,
        final_response="Saved to res-02.md",
        tools_used={"web_fetch", "write"},
        ws_errors=[],
        completed=True,
        agent_dir=agent_dir,
    )
    assert passed
    assert not bad
    assert any("artifact file" in r for r in ok)


def test_artifact_file_satisfies_write_tool_requirement(tmp_path):
    agent_dir = tmp_path / "agent"
    ws = agent_dir / "workspace"
    ws.mkdir(parents=True)
    (ws / "decision.md").write_text("FastAPI vs Flask comparison\n", encoding="utf-8")
    cfg = {
        "pass": {
            "tools_any": ["write"],
            "artifact_files": ["decision.md"],
            "min_response_chars": 200,
        }
    }
    passed, ok, bad = score_pass(
        cfg,
        final_response="Here's the comparison in chat...",
        tools_used={"web_fetch", "web_search"},
        ws_errors=[],
        completed=True,
        agent_dir=agent_dir,
    )
    assert passed
    assert not bad
    assert any("artifact file" in r for r in ok)
