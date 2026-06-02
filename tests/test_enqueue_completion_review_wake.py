"""Regression: completion-review enqueue must not NameError."""

from __future__ import annotations

from unittest.mock import MagicMock

from nls.engine.inner_loop import InnerLoop


def test_enqueue_completion_review_source_does_not_raise():
    rt = MagicMock()
    rt.agent_id = "agent_test"
    rt.is_busy = False
    rt.is_user_busy = False
    rt.config = {"agency": {"agentic_loop": {"use_v2": True}}}
    rt.is_agentic_enabled.return_value = True
    rt.inference_available.return_value = True
    rt._team_manager = None

    il = InnerLoop.__new__(InnerLoop)
    il.runtime = rt
    il._pending_dispatches = []
    il._running = True
    il._paused = False
    il._autonomous_executing = False
    il._use_model_a = False
    il._active_dream_task = None
    il.event_queue = MagicMock(is_empty=True)
    il._mirror_dispatch_to_event_queue = MagicMock()
    il.drain_pending_dispatches = MagicMock(return_value=0)

    InnerLoop.enqueue_autonomous_dispatch(
        il,
        "[COMPLETION REVIEW] approve delegate",
        "team_completion_review:team_c838f0cf",
    )

    assert len(il._pending_dispatches) == 1
    assert il._pending_dispatches[0][1] == "team_completion_review:team_c838f0cf"
