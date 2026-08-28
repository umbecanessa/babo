"""Regression tests for session_meta persistence with session routing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nls.runtime.session_routing.config import SessionRoutingConfig


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    meta = {
        "turn_count": 3,
        "session_routing": {
            "version": "1.0",
            "default_home_session_key": "websocket:main",
            "primary_reachability_session_key": "telegram:group:-100111",
            "mirror_channel_progress_to_home": True,
            "default_report_mode": "origin_or_explicit",
            "report_channels": [],
            "exclusions": [],
        },
    }
    (tmp_path / "session_meta.json").write_text(
        json.dumps(meta), encoding="utf-8",
    )
    return tmp_path


def test_save_state_preserves_session_routing(agent_dir: Path):
    from nls.runtime.agent_runtime import AgentRuntime, is_valid_home_session_key

    rt = MagicMock(spec=AgentRuntime)
    rt.agent_id = "agent-test"
    rt.agent_dir = agent_dir
    rt._turn_count = 7
    rt._sleep_count = 2
    rt._last_interaction = 1234567890.0
    rt.session_orchestrator_model = None
    rt.session_orchestrator_route = None
    rt.session_delegate_model = None
    rt.session_delegate_route = None
    rt.session_delegate_lock_orchestrator = False
    rt.default_home_session_key = "websocket:main"
    rt._session_router = None
    for attr in (
        "hypothalamus", "ans", "self_state", "temporal_self", "ofc",
        "narrative_self", "theory_of_mind", "predictive", "network_dynamics",
        "dual_wm", "working_memory", "agency", "dmn", "delegate_manager",
        "channel_registry", "calibrator", "drive_engine",
    ):
        setattr(rt, attr, None)

    AgentRuntime.save_state(rt)

    saved = json.loads((agent_dir / "session_meta.json").read_text(encoding="utf-8"))
    assert saved["turn_count"] == 7
    assert saved["session_routing"]["primary_reachability_session_key"] == (
        "telegram:group:-100111"
    )
    assert saved["session_routing"]["mirror_channel_progress_to_home"] is True


def test_set_default_home_does_not_wipe_session_routing(agent_dir: Path):
    from nls.runtime.agent_runtime import AgentRuntime

    rt = MagicMock(spec=AgentRuntime)
    rt.agent_id = "agent-test"
    rt.agent_dir = agent_dir
    rt.default_home_session_key = "websocket:main"
    rt._session_router = None
    rt.get_default_home_session_key = lambda: "websocket:main"

    branch = "websocket:thread:abc123"
    assert AgentRuntime.set_default_home_session_key(rt, branch) is True

    saved = json.loads((agent_dir / "session_meta.json").read_text(encoding="utf-8"))
    assert saved["default_home_session_key"] == branch
    assert saved["session_routing"]["primary_reachability_session_key"] == (
        "telegram:group:-100111"
    )
    assert saved["session_routing"]["mirror_channel_progress_to_home"] is True
