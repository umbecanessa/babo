"""grant_paths idempotency and delegate scope checks."""

from __future__ import annotations

from nls.agentic.team_manager import _paths_from_escalation_context
from nls.tools.agent_tools.file_ledger import FileLedger


def test_paths_from_escalation_context():
    ctx = "iteration: 3/40\npaths_requested: .gitignore, backend/foo.py\n"
    assert _paths_from_escalation_context("escalate:file_access: need", ctx) == [
        ".gitignore",
        "backend/foo.py",
    ]


def test_delegate_covers_paths(tmp_path):
    ledger = FileLedger(tmp_path / "ledger.jsonl")
    ledger.set_wave_ownership(0, {1: [".gitignore", "backend/"]}, project_dir="proj")
    assert ledger.delegate_covers_paths(0, 1, [".gitignore"])
    assert ledger.delegate_covers_paths(0, 1, ["backend/src/x.py"])
    assert not ledger.delegate_covers_paths(0, 1, ["frontend/app.tsx"])


def test_grant_delegate_paths_is_append_only(tmp_path):
    ledger = FileLedger(tmp_path / "ledger2.jsonl")
    ledger.set_wave_ownership(0, {2: ["src/"]}, project_dir="proj")
    first = ledger.grant_delegate_paths(0, 2, [".gitignore"])
    second = ledger.grant_delegate_paths(0, 2, [".gitignore"])
    assert first == [".gitignore"]
    assert second == []
    assert ledger.delegate_covers_paths(0, 2, [".gitignore"])
