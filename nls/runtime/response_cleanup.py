"""Strip NLS-internal artifacts from user-visible model text."""
from __future__ import annotations

import re

_BRACKET_SIGNAL_RE = re.compile(
    r"\[(?:LEARN|RECALL|EVALUATE|ADJUST|PLAN|REFLECT|CONNECT|DOUBT|VALUES|BOND|PLEASED|IDENTITY)"
    r"(?:[:|][^\]]*)?\]\s*",
    re.IGNORECASE,
)


def strip_nls_signal_calls(text: str) -> str:
    """Remove nls_signal(...) pseudo-calls leaked into visible model text."""
    if not text:
        return text
    lower = text.lower()
    parts: list[str] = []
    i = 0
    while i < len(text):
        idx = lower.find("nls_signal", i)
        if idx < 0:
            parts.append(text[i:])
            break
        parts.append(text[i:idx])
        j = idx + len("nls_signal")
        while j < len(text) and text[j].isspace():
            j += 1
        if j >= len(text) or text[j] != "(":
            parts.append(text[idx:j])
            i = j
            continue
        depth = 0
        k = j
        while k < len(text):
            ch = text[k]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        i = k
    return "".join(parts)


def strip_nls_artifacts(text: str) -> str:
    """Strip nls_signal calls and bracket signal tags from user-visible text."""
    if not text:
        return text
    cleaned = strip_nls_signal_calls(text)
    cleaned = _BRACKET_SIGNAL_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# Python-style pseudo tool calls leaked when chat mode runs without tools.
_PSEUDO_PYTHON_TOOL_RE = re.compile(
    r"^[a-z][a-z0-9_]*\([^)]*\)\s*$",
    re.IGNORECASE,
)


def sanitize_channel_outbound(text: str) -> str:
    """Remove tool-call debris before sending text to Discord/Telegram/etc.

    Chat-mode channel turns have no tool schemas; the model sometimes prints
    ``channel_inspect(...)`` or ``<tool_call>`` blocks as plain text.
    """
    if not text:
        return ""
    try:
        from nls.agentic.types import _strip_toolcall_pollution

        cleaned = _strip_toolcall_pollution(text.strip())
    except Exception:
        cleaned = strip_nls_artifacts(text.strip())

    kept: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _PSEUDO_PYTHON_TOOL_RE.match(stripped):
            continue
        kept.append(line)
    result = "\n".join(kept).strip()
    if not result and text.strip():
        if _PSEUDO_PYTHON_TOOL_RE.match(text.strip()):
            return ""
    return result


def is_channel_outbound_tool_leak(text: str) -> bool:
    """True when *text* is only tool syntax and must not be sent to a channel."""
    if not text or not text.strip():
        return False
    return not sanitize_channel_outbound(text)
