"""Stall detection — batch maintenance bash loops should not false-positive."""

from nls.agentic.evaluator import detect_stall
from nls.agentic.types import LoopConfig, LoopState
from nls.tools.agent_tools.base import ToolResult


def _list_sig() -> str:
    return (
        'bash:{"command": "gh repo list umbecanessa --limit 200 | '
        'Select-String -Pattern \\"icf|coach|mentor\\""}'
    )


def _delete_sig(repo: str) -> str:
    return f'bash:{{"command": "gh repo delete umbecanessa/{repo} --yes"}}'


def test_repeated_inventory_after_deletes_not_stalled():
    """gh delete → list → delete → list is legitimate cleanup."""
    state = LoopState()
    cfg = LoopConfig(max_iterations=50, enable_delegation=True)
    sigs = [
        _delete_sig("mentorsight"),
        _list_sig(),
        _delete_sig("coachsignal-ai"),
        _list_sig(),
        _list_sig(),
        _list_sig(),
    ]
    for sig in sigs:
        state.tool_call_signatures.append(sig)
        state.tool_history.append(("bash", False))
        state.tool_successes["bash"] = state.tool_successes.get("bash", 0) + 1

    assert detect_stall(state, cfg) is None


def test_bash_delete_list_cycle_not_stalled():
    """Alternating delete/list bash cycle with successes is maintenance."""
    state = LoopState()
    cfg = LoopConfig(max_iterations=50, enable_delegation=True)
    pattern = [_delete_sig("a"), _list_sig()]
    for _ in range(3):
        for sig in pattern:
            state.tool_call_signatures.append(sig)
            state.tool_history.append(("bash", False))
    state.tool_successes["bash"] = 6

    assert detect_stall(state, cfg) is None


def test_identical_failed_bash_still_stalls():
    """Same failing bash 3x should still trigger repeat nudge."""
    state = LoopState()
    cfg = LoopConfig(max_iterations=50, enable_delegation=True)
    sig = 'bash:{"command": "npm install broken-pkg"}'
    for _ in range(3):
        state.tool_call_signatures.append(sig)
        state.record_tool("bash", ToolResult(content="err", is_error=True), sig)

    assert detect_stall(state, cfg) is not None
