"""Loop exit_reason → LoopResult.aborted mapping."""

from nls.agentic.types import LoopState


def test_post_launch_yield_is_not_aborted():
    state = LoopState()
    state.exit_reason = "post_launch_yield"
    state.iteration = 8
    result = state.to_result()
    assert not result.aborted
    assert result.exit_reason == "post_launch_yield"


def test_user_abort_is_aborted():
    state = LoopState()
    state.exit_reason = "user_abort"
    result = state.to_result()
    assert result.aborted
    assert result.abort_reason == "user_abort"
