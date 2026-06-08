"""Tests for cross-tool path normalization."""

from __future__ import annotations

from nls.tools.agent_tools.tool_path_args import normalize_tool_path_arg


def test_normalize_strips_project_prefix_when_cwd_in_backend():
    path, err = normalize_tool_path_arg(
        "ai-powered-icf/backend/app/models/transcript.py",
        cwd="/workspace/ai-powered-icf/backend",
        key="path",
    )
    assert err is None
    assert path == "app/models/transcript.py"
