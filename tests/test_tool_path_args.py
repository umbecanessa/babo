"""Tests for shared tool path argument normalization."""

from __future__ import annotations

from nls.tools.agent_tools.tool_path_args import (
    normalize_tool_path_arg,
    path_arg_looks_malformed,
    unwrap_embedded_json_path,
)


def test_unwrap_double_json_path():
    raw = '{"path": "frontend/src/App.tsx"'
    assert unwrap_embedded_json_path(raw) == "frontend/src/App.tsx"


def test_reject_still_malformed_path():
    _, err = normalize_tool_path_arg('{"path": "', key="path")
    assert err is not None
    assert "malformed" in err.lower()


def test_strip_project_prefix_with_cwd():
    cwd = "/workspace/my-app"
    path, err = normalize_tool_path_arg(
        "my-app/src/index.ts",
        cwd=cwd,
        key="path",
    )
    assert err is None
    assert path == "src/index.ts"


def test_plain_path_unchanged():
    path, err = normalize_tool_path_arg("backend/pkg/main.go", key="path")
    assert err is None
    assert path == "backend/pkg/main.go"


def test_malformed_detector():
    assert path_arg_looks_malformed('{"path": "x"}')
    assert not path_arg_looks_malformed("src/main.rs")
