"""Stall detection for skill_install + channel file rewrite loops."""

from __future__ import annotations

from nls.agentic.evaluator import _detect_skill_loader_rewrite_stall
from nls.agentic.types import LoopConfig, LoopState


def test_skill_loader_rewrite_stall_nudge():
    state = LoopState()
    state.tool_errors["skill_install"] = 1
    state.last_error_preview = "cannot import name 'router'"
    state.tool_call_signatures = [
        'write:{"path": "discord-channel/webhook.py"}',
        'edit:{"path": "discord-channel/webhook.py"}',
    ]
    msg = _detect_skill_loader_rewrite_stall(state)
    assert msg is not None
    assert "router" in msg.lower()
