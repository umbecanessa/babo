"""Tests for turn triage prompt and tool catalog."""

from __future__ import annotations

from types import SimpleNamespace

from nls.agentic.goals import (
    build_triage_system_prompt,
    summarize_tools_for_triage,
)


def test_summarize_tools_for_triage_lists_names():
    tools = [
        SimpleNamespace(name="read", description="Read a file from disk"),
        SimpleNamespace(name="bash", description="Run a shell command"),
    ]
    catalog = summarize_tools_for_triage(tools)
    assert "AVAILABLE TOOLS" in catalog
    assert "- read:" in catalog
    assert "- bash:" in catalog


def test_build_triage_system_prompt_includes_catalog():
    catalog = "AVAILABLE TOOLS (agent may call any that help):\n- read: Read files"
    prompt = build_triage_system_prompt(tool_catalog=catalog)
    assert "TOOL GATING" in prompt
    assert "- read: Read files" in prompt
    assert "forbid:tools" in prompt
    assert "ANY available tool could usefully help" in prompt
    assert "NEVER forbid:team" in prompt
    assert "WRONG (do not output)" in prompt
