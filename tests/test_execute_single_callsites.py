"""Regression: every _execute_single() call must pass the tools dict."""

import re
from pathlib import Path


def test_execute_single_callsites_pass_tools():
    text = Path("nls/agentic/executor.py").read_text(encoding="utf-8")
    pattern = re.compile(r"await _execute_single\(")
    for match in pattern.finditer(text):
        chunk = text[match.start() : match.start() + 280]
        assert "tools" in chunk, (
            f"_execute_single call missing tools argument near:\n{chunk[:120]!r}"
        )
