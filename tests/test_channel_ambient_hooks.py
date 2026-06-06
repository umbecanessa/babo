"""Tests for ambient hook agent_dir resolution."""

from __future__ import annotations

from pathlib import Path

from nls.skills.channel_ambient import resolve_agent_dir


class _FakeRuntime:
    def __init__(self, agent_dir: Path) -> None:
        self.agent_dir = agent_dir


class _FakeAgentManager:
    def __init__(self, agents_dir: Path) -> None:
        self.agents_dir = agents_dir
        self._runtimes: dict[str, _FakeRuntime] = {}

    def get_runtime(self, agent_id: str) -> _FakeRuntime | None:
        return self._runtimes.get(agent_id)


class _FakeApp:
    def __init__(self, am: _FakeAgentManager) -> None:
        self.state = type("S", (), {"agent_manager": am})()


def test_resolve_from_runtime(tmp_path: Path) -> None:
    rt = _FakeRuntime(tmp_path / "agent-a")
    assert resolve_agent_dir(rt) == rt.agent_dir


def test_resolve_from_app_without_runtime(tmp_path: Path) -> None:
    am = _FakeAgentManager(tmp_path / "agents")
    app = _FakeApp(am)
    resolved = resolve_agent_dir(None, app=app, agent_id="dead-beef")
    assert resolved == tmp_path / "agents" / "dead-beef"
