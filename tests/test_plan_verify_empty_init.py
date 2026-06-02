"""Plan verify should not flag empty package __init__.py files."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nls.tools.agent_tools.plan import PlanTool


def test_audit_empty_project_files_skips_init_py(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "__init__.py").write_bytes(b"")
    (root / "main.py").write_text("print('ok')\n", encoding="utf-8")

    tool = PlanTool(str(tmp_path))
    plan = MagicMock()
    plan.project_dir = "proj"

    issues = tool._audit_empty_project_files(plan)
    assert issues == []
