"""NLS Tool Call Normalizer -- Dual-format tool call parsing.

Supports two tool invocation formats and normalizes them into a
unified ``NormalizedToolCall`` that downstream code works with:

1. **Text-based** ``[TOOL:name|args]`` -- the current NLS format,
   embedded in the model's response text.  Agents are trained to
   produce this through the education pipeline.

2. **Structured function calling** -- OpenAI-compatible JSON tool
   calls returned by vLLM's ``tool_choice`` parameter.  This is the
   future path: more reliable, schema-validated, no regex needed.

The normalizer tries structured calls first (if present), then falls
back to text-based parsing.  This allows a gradual migration:

- Phase 1 (now):  text-based only, structured is a no-op
- Phase 2 (soon): vLLM sends ``tools`` parameter, model produces both
- Phase 3 (goal): structured only, text-based becomes legacy fallback

Design: extracted from ``ServerRuntime._parse_and_execute_tool_calls()``
so the parsing logic is reusable across the single-turn pipeline,
the agentic loop, and any future entry points.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalized tool call
# ---------------------------------------------------------------------------


@dataclass
class NormalizedToolCall:
    """A parsed tool invocation, regardless of source format.

    Attributes
    ----------
    name : str
        Tool name (e.g., ``"web_search"``, ``"file_read"``).
    arguments : dict[str, Any]
        Parsed arguments as a key-value dict.
    raw_text : str
        The original text that produced this call (for logging/debug).
    source : str
        ``"text"`` for ``[TOOL:]`` signals, ``"structured"`` for
        OpenAI-format function calls.
    call_id : str | None
        Optional tool call ID (only for structured calls, used in
        multi-turn conversations to match results to calls).
    """

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    source: str = "text"
    call_id: str | None = None


# ---------------------------------------------------------------------------
# Tool argument mapping
# ---------------------------------------------------------------------------

# When parsing text-based [TOOL:name|simple_arg], we need to know
# which parameter name to assign the argument to.  This maps tool
# names to their primary parameter.  If a tool isn't listed here,
# the argument is mapped to "query" as a safe default.
#
# Extracted from ServerRuntime._TOOL_ARG_MAP to keep it in sync.

DEFAULT_ARG_MAP: dict[str, str] = {
    "web_search": "query",
    "wikipedia": "query",
    "arxiv_search": "query",
    "file_read": "path",
    "file_write": "path",
    "file_edit": "path",
    "file_search": "query",
    "file_tree": "path",
    "terminal": "command",
    "git": "command",
    "docker": "command",
    "test_runner": "command",
    "process_manager": "command",
    "browser": "action",
    "screenshot": "target",
    "clipboard": "action",
    "notification": "message",
    "calculator": "expression",
    "translate": "text",
    "regex_tool": "pattern",
    "hash_encode": "input",
}


# ---------------------------------------------------------------------------
# Text-based parser: [TOOL:name|args]
# ---------------------------------------------------------------------------

# Simple regex for quick detection (no bracket issues).
_TOOL_CALL_RE_SIMPLE = re.compile(r"\[TOOL:([a-z_][a-z0-9_]*)\|")

# Known tool names for fuzzy syntax normalization.
_KNOWN_TOOLS = set(DEFAULT_ARG_MAP.keys())

# Regex for bare tool invocations: [TOOLNAME|args] or [TOOL_NAME|args]
_BARE_TOOL_RE_SIMPLE = re.compile(r"\[([A-Z_][A-Z0-9_]*)\|")


def _extract_balanced_arg(text: str, start: int) -> tuple[str, int] | None:
    """Extract argument text from *start* up to the balanced closing ``]``.

    Tracks bracket depth so ``]`` inside JSON (e.g. ``seq = []``) doesn't
    terminate the match prematurely.  Also skips over quoted strings so
    that literal brackets inside strings are not counted.

    Returns ``(arg_text, end_pos)`` where *end_pos* points one past the
    closing ``]``, or ``None`` if no balanced close is found.
    """
    depth = 1
    pos = start
    length = len(text)

    while pos < length and depth > 0:
        ch = text[pos]
        if ch == '"':
            pos += 1
            while pos < length:
                if text[pos] == '\\':
                    pos += 2
                    continue
                if text[pos] == '"':
                    break
                pos += 1
        elif ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                return text[start:pos], pos + 1
        pos += 1

    return None


def _scan_tool_calls(text: str, prefix: str = "[TOOL:") -> list[tuple[str, str, int, int]]:
    """Scan *text* for ``[TOOL:name|args]`` with balanced-bracket support.

    Returns list of ``(tool_name, raw_args, match_start, match_end)``.
    """
    results: list[tuple[str, str, int, int]] = []
    pos = 0

    while pos < len(text):
        idx = text.find(prefix, pos)
        if idx == -1:
            break

        name_start = idx + len(prefix)
        pipe_idx = text.find("|", name_start)
        if pipe_idx == -1 or pipe_idx - name_start > 30:
            pos = name_start
            continue

        tool_name = text[name_start:pipe_idx]
        if not tool_name or not re.match(r"^[a-z_][a-z0-9_]*$", tool_name):
            pos = name_start
            continue

        result = _extract_balanced_arg(text, pipe_idx + 1)
        if result is None:
            pos = pipe_idx + 1
            continue

        raw_args, end_pos = result
        results.append((tool_name, raw_args, idx, end_pos))
        pos = end_pos

    return results


def _normalize_tool_syntax(response: str) -> str:
    """Convert common tool call variations to canonical [TOOL:name|args] format.

    Handles models that emit [TERMINAL|ls] instead of [TOOL:terminal|ls],
    [FILE_WRITE|...] instead of [TOOL:file_write|...], etc.

    Uses balanced-bracket scanning so JSON arguments with nested brackets
    are preserved correctly.
    """
    pos = 0
    parts: list[str] = []

    while pos < len(response):
        match = _BARE_TOOL_RE_SIMPLE.search(response, pos)
        if match is None:
            parts.append(response[pos:])
            break

        raw_name = match.group(1).lower()
        if raw_name not in _KNOWN_TOOLS:
            parts.append(response[pos:match.end()])
            pos = match.end()
            continue

        args_start = match.end()
        result = _extract_balanced_arg(response, args_start)
        if result is None:
            parts.append(response[pos:match.end()])
            pos = match.end()
            continue

        raw_args, end_pos = result
        parts.append(response[pos:match.start()])
        parts.append(f"[TOOL:{raw_name}|{raw_args}]")
        pos = end_pos

    return "".join(parts)


def parse_text_tool_calls(
    response: str,
    arg_map: dict[str, str] | None = None,
    max_calls: int = 10,
) -> list[NormalizedToolCall]:
    """Parse ``[TOOL:name|args]`` signals from model response text.

    Supports two argument sub-formats:

    - **Simple**: ``[TOOL:web_search|latest AI news]``
      Maps the entire argument to the tool's primary parameter.

    - **Named**: ``[TOOL:web_search|query=latest AI news]``
      Splits on the first ``=`` for a single named parameter.

    - **JSON**: ``[TOOL:file_edit|{"path":"f.py","old":"a","new":"b"}]``
      Full JSON object for tools that need multiple parameters.

    Parameters
    ----------
    response : str
        The model's full response text.
    arg_map : dict | None
        Tool name -> primary parameter name mapping.
        Defaults to ``DEFAULT_ARG_MAP``.
    max_calls : int
        Maximum tool calls to parse per response (safety cap).

    Returns
    -------
    list[NormalizedToolCall]
        Parsed and normalized tool calls, in order of appearance.
    """
    if not response:
        return []

    response = _normalize_tool_syntax(response)

    if "[TOOL:" not in response:
        return []

    effective_map = arg_map or DEFAULT_ARG_MAP
    scanned = _scan_tool_calls(response)

    if not scanned:
        return []

    calls: list[NormalizedToolCall] = []

    for tool_name, raw_arg, start, end in scanned[:max_calls]:
        raw_arg = raw_arg.strip()
        tool_args = _parse_argument(tool_name, raw_arg, effective_map)

        calls.append(NormalizedToolCall(
            name=tool_name,
            arguments=tool_args,
            raw_text=response[start:end],
            source="text",
        ))

    return calls


def _parse_argument(
    tool_name: str,
    raw_arg: str,
    arg_map: dict[str, str],
) -> dict[str, Any]:
    """Parse a raw argument string into a key-value dict.

    Tries three formats in order:
    1. JSON object: ``{"path": "foo.py", "content": "bar"}``
    2. Named parameter: ``key=value``
    3. Simple (positional): maps to the tool's primary parameter
    """
    # 1. Try JSON
    if raw_arg.startswith("{"):
        try:
            parsed = json.loads(raw_arg)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # 2. Try named parameter: key=value
    # But skip URLs (contain = in query strings) and base64-like strings
    if "=" in raw_arg and not raw_arg.startswith("http") and not raw_arg.startswith("data:"):
        # Check if it looks like a single key=value pair
        eq_index = raw_arg.index("=")
        potential_key = raw_arg[:eq_index].strip()
        # Valid key: alphanumeric + underscores, no spaces
        if potential_key.isidentifier():
            return {potential_key: raw_arg[eq_index + 1:].strip()}

    # 3. Simple positional argument
    primary_key = arg_map.get(tool_name, "query")
    return {primary_key: raw_arg}


# ---------------------------------------------------------------------------
# Structured function call parser (OpenAI-compatible)
# ---------------------------------------------------------------------------


def parse_structured_tool_calls(
    tool_calls: list[dict[str, Any]] | None,
) -> list[NormalizedToolCall]:
    """Parse OpenAI-format structured tool calls from the model response.

    Expected format (from vLLM / OpenAI API)::

        [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": "{\"query\": \"latest AI news\"}"
                }
            }
        ]

    Parameters
    ----------
    tool_calls : list[dict] | None
        The ``tool_calls`` field from an OpenAI-compatible chat
        completion response.  ``None`` or empty = no structured calls.

    Returns
    -------
    list[NormalizedToolCall]
        Parsed and normalized tool calls.
    """
    if not tool_calls:
        return []

    calls: list[NormalizedToolCall] = []

    for tc in tool_calls:
        if tc.get("type") != "function":
            continue

        func = tc.get("function", {})
        name = func.get("name", "")
        if not name:
            continue

        # Parse arguments (may be a JSON string or already a dict)
        raw_args = func.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                arguments = json.loads(raw_args)
            except (json.JSONDecodeError, ValueError):
                arguments = {"raw": raw_args}
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            arguments = {}

        calls.append(NormalizedToolCall(
            name=name,
            arguments=arguments if isinstance(arguments, dict) else {"raw": str(arguments)},
            raw_text=json.dumps(tc, ensure_ascii=False),
            source="structured",
            call_id=tc.get("id"),
        ))

    return calls


# ---------------------------------------------------------------------------
# Unified normalizer
# ---------------------------------------------------------------------------


def normalize_tool_calls(
    response_text: str,
    structured_tool_calls: list[dict[str, Any]] | None = None,
    arg_map: dict[str, str] | None = None,
    max_text_calls: int = 10,
    prefer_structured: bool = True,
) -> list[NormalizedToolCall]:
    """Parse tool calls from both formats and return a unified list.

    Priority logic:
    - If ``prefer_structured`` is True and structured calls exist,
      return those (ignore text-based duplicates).
    - Otherwise, parse text-based ``[TOOL:]`` signals.
    - If both are present and ``prefer_structured`` is False,
      merge them (structured first, then text-based).

    This function is the single entry point for the agentic loop.
    It replaces ``ServerRuntime._parse_and_execute_tool_calls()``'s
    internal parsing (but not the execution -- that stays in the loop).

    Parameters
    ----------
    response_text : str
        The model's full response text (may contain ``[TOOL:]`` signals).
    structured_tool_calls : list[dict] | None
        OpenAI-format tool calls from the API response (if any).
    arg_map : dict | None
        Tool name -> primary parameter name mapping for text parsing.
    max_text_calls : int
        Max text-based tool calls to parse.
    prefer_structured : bool
        If True and structured calls exist, skip text parsing.

    Returns
    -------
    list[NormalizedToolCall]
        All parsed tool calls, ready for execution.
    """
    structured = parse_structured_tool_calls(structured_tool_calls)

    if structured and prefer_structured:
        logger.debug("Using %d structured tool call(s)", len(structured))
        return structured

    text_calls = parse_text_tool_calls(
        response_text, arg_map=arg_map, max_calls=max_text_calls,
    )

    if structured and text_calls:
        # Both present, structured first
        logger.debug(
            "Merging %d structured + %d text tool call(s)",
            len(structured), len(text_calls),
        )
        # Deduplicate: if a structured call has the same name as a text call,
        # keep the structured one (it's more reliable)
        structured_names = {c.name for c in structured}
        unique_text = [c for c in text_calls if c.name not in structured_names]
        return structured + unique_text

    if structured:
        return structured

    if text_calls:
        logger.debug("Using %d text-based tool call(s)", len(text_calls))
        return text_calls

    return []


# ---------------------------------------------------------------------------
# Utility: strip tool call tags from response text
# ---------------------------------------------------------------------------


def strip_tool_calls(response: str) -> str:
    """Remove all ``[TOOL:...]`` tags from response text.

    Useful for cleaning the response before sending to the user,
    since tool call signals are machine-readable, not user-facing.
    """
    response = _normalize_tool_syntax(response)
    scanned = _scan_tool_calls(response)
    if not scanned:
        return response.strip()
    parts: list[str] = []
    prev_end = 0
    for _, _, start, end in scanned:
        parts.append(response[prev_end:start])
        prev_end = end
    parts.append(response[prev_end:])
    return "".join(parts).strip()


def has_tool_calls(response: str) -> bool:
    """Check if the response contains tool call tags (canonical or variant)."""
    return "[TOOL:" in _normalize_tool_syntax(response)


def detect_unparsed_tool_attempts(
    response: str,
    parsed_calls: list[NormalizedToolCall],
) -> list[str]:
    """Detect tool-call-like patterns that were NOT successfully parsed.

    After parsing, the response may still contain fragments like
    ``[TOOL:file_write|{broken json...`` that failed to match.  This
    function finds those so the agentic loop can warn the agent,
    preventing silent drops.

    Returns a list of human-readable descriptions of unparsed attempts.
    """
    parsed_spans: set[tuple[int, int]] = set()
    for call in parsed_calls:
        idx = response.find(call.raw_text)
        if idx >= 0:
            parsed_spans.add((idx, idx + len(call.raw_text)))

    unparsed: list[str] = []
    marker = "[TOOL:"
    pos = 0
    while pos < len(response):
        idx = response.find(marker, pos)
        if idx == -1:
            break
        if any(s <= idx < e for s, e in parsed_spans):
            pos = idx + len(marker)
            continue
        snippet_end = min(idx + 60, len(response))
        snippet = response[idx:snippet_end]
        if "]" in snippet:
            snippet = snippet[:snippet.index("]") + 1]
        unparsed.append(snippet.replace("\n", " ").strip())
        pos = idx + len(marker)

    return unparsed


def format_tool_result_for_context(
    call: NormalizedToolCall,
    result_text: str,
    success: bool,
    max_chars: int = 8000,
) -> str:
    """Format a tool call + result for injection into conversation context.

    Used by the agentic loop to feed tool results back to the model
    for the next iteration.

    Parameters
    ----------
    call : NormalizedToolCall
        The tool call that was executed.
    result_text : str
        The tool's output text.
    success : bool
        Whether the tool executed successfully.
    max_chars : int
        Maximum characters for the result (truncate if longer).
    """
    status = "SUCCESS" if success else "ERROR"
    args_summary = json.dumps(call.arguments, ensure_ascii=False)
    if len(args_summary) > 200:
        args_summary = args_summary[:200] + "..."

    result_display = result_text
    if len(result_display) > max_chars:
        half = max_chars // 2
        result_display = (
            result_display[:half]
            + f"\n\n... ({len(result_text) - max_chars} chars truncated) ...\n\n"
            + result_display[-half:]
        )

    return (
        f"[Tool: {call.name}({args_summary}) -> {status}]\n"
        f"{result_display}"
    )
