"""Tests for shared tool path argument normalization."""

from __future__ import annotations

from nls.tools.agent_tools.tool_path_args import (
    build_write_missing_content_error,
    normalize_tool_path_arg,
    path_arg_looks_malformed,
    recover_write_tool_args,
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


def test_build_write_missing_content_error_truncated():
    raw = '{"path": "workspace/discord-channel/adapter.py"'
    msg = build_write_missing_content_error(raw)
    assert "content" in msg.lower()
    assert "discord-channel/adapter.py" in msg
    assert "truncated" in msg.lower() or "token" in msg.lower()


def test_build_write_missing_content_error_after_executor_unwrap():
    """Executor unwraps embedded JSON path before write() sees params."""
    resolved = (
        "C:/Users/umber/AppData/Roaming/babo-desktop/data/skills/"
        "discord-channel/adapter.py"
    )
    msg = build_write_missing_content_error(
        resolved,
        resolved_path=resolved,
        content_key_absent=True,
    )
    assert "adapter.py" in msg
    assert "truncated" in msg.lower() or "token" in msg.lower()
    assert "write() a short stub" in msg


def test_recover_write_from_embedded_blob():
    raw_path = '{"path": "src/main.py", "content": "print(1)\\n"'
    path, content = recover_write_tool_args({"path": raw_path})
    assert path == "src/main.py"
    assert content == "print(1)\n"


def test_recover_write_content_alt_key():
    path, content = recover_write_tool_args(
        {"path": "foo.txt", "text": "hello"},
    )
    assert path == "foo.txt"
    assert content == "hello"


def test_recover_write_missing_content():
    path, content = recover_write_tool_args(
        {"path": '{"path": "workspace/discord-channel/adapter.py"'},
    )
    assert path == "workspace/discord-channel/adapter.py"
    assert content is None
