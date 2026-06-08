"""Default Home session pointer (no history migration)."""

from __future__ import annotations

import json
from pathlib import Path

from nls.runtime.agent_runtime import (
    DEFAULT_HOME_SESSION_KEY,
    AgentRuntime,
    is_valid_home_session_key,
)


def test_is_valid_home_session_key():
    assert is_valid_home_session_key("websocket:main") is True
    assert is_valid_home_session_key("websocket:thread:abc") is True
    assert is_valid_home_session_key("discord:dm:123") is False
    assert is_valid_home_session_key("") is False


def test_default_home_persisted_in_session_meta(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    meta_path = agent_dir / "session_meta.json"
    meta_path.write_text(
        json.dumps({"default_home_session_key": "websocket:thread:fresh"}),
        encoding="utf-8",
    )

    rt = AgentRuntime.__new__(AgentRuntime)
    rt.agent_dir = agent_dir
    rt.agent_id = "test-agent"
    rt.default_home_session_key = DEFAULT_HOME_SESSION_KEY
    rt._turn_count = 0
    rt._sleep_count = 0
    rt._last_interaction = 0.0
    rt.session_orchestrator_model = None
    rt.session_orchestrator_route = None
    rt.session_delegate_model = None
    rt.session_delegate_route = None
    rt.session_delegate_lock_orchestrator = False
    rt._load_session_meta()

    assert rt.get_default_home_session_key() == "websocket:thread:fresh"

    assert rt.set_default_home_session_key("websocket:thread:next") is True
    saved = json.loads(meta_path.read_text(encoding="utf-8"))
    assert saved["default_home_session_key"] == "websocket:thread:next"
