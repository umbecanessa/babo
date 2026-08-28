"""Tests for shutdown tracing and SIGINT guard during agentic work."""

from __future__ import annotations

import signal

import pytest

import server.shutdown_trace as st


@pytest.fixture(autouse=True)
def _reset_shutdown_trace(monkeypatch):
    st._INITIATOR = None
    st._DETAIL = {}
    st._SIGNAL_LOGGED.clear()
    st._ALLOW_SIGINT = False
    monkeypatch.setattr(st, "agentic_loops_active", lambda: 0)


def test_record_initiator_first_wins():
    st.record_initiator("http:admin_shutdown", client="127.0.0.1")
    st.record_initiator("signal:SIGINT")
    source, detail = st.get_initiator()
    assert source == "http:admin_shutdown"
    assert detail.get("client") == "127.0.0.1"


def test_suppress_sigint_during_agentic(monkeypatch):
    monkeypatch.setattr(st, "agentic_loops_active", lambda: 2)
    assert st._should_suppress_sigint(signal.SIGINT) is True
    source, _ = st.get_initiator()
    assert source == "signal:SIGINT_suppressed"


def test_allow_intentional_sigint_after_admin_shutdown():
    st.record_initiator("http:admin_shutdown", client="127.0.0.1")
    assert st._should_suppress_sigint(signal.SIGINT) is False


def test_allow_intentional_sigint_after_restart_approved():
    st.record_initiator("agent:request_restart_approved")
    assert st._should_suppress_sigint(signal.SIGINT) is False


def test_request_sigint_exit_sets_allow_flag(monkeypatch):
    monkeypatch.setattr(st.os, "kill", lambda *args, **kwargs: None)
    st.request_sigint_exit()
    assert st._ALLOW_SIGINT is True
