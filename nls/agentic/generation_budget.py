"""Detect output-budget exhaustion and inject recovery nudges."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import GenerationResult, LoopConfig

logger = logging.getLogger(__name__)

_FILE_WRITE_TOOLS = frozenset({"write", "edit"})
_BUDGET_EPSILON = 8
_BUDGET_RATIO = 0.98
TRUNCATED_WRITE_ESCALATE_AFTER = 2


@dataclass
class TruncatedFileToolEvent:
    """A write/edit tool call that hit output-budget truncation."""

    tool_name: str
    target_path: str
    kind: str  # missing_content | partial_content | incomplete_edit


@dataclass
class GenerationBudgetAnalysis:
    """Signals when a generation turn hit the output token ceiling."""

    output_budget_exhausted: bool = False
    thinking_budget_exhausted: bool = False
    truncated_file_tools: list[str] = field(default_factory=list)
    truncated_file_events: list[TruncatedFileToolEvent] = field(default_factory=list)


def output_budget_exhausted(
    completion_tokens: int,
    max_new_tokens: int,
    *,
    finish_reason: str = "",
) -> bool:
    if max_new_tokens <= 0:
        return False
    finish = (finish_reason or "").strip().lower()
    if finish == "length":
        return True
    if completion_tokens <= 0:
        return False
    threshold = max(max_new_tokens - _BUDGET_EPSILON, int(max_new_tokens * _BUDGET_RATIO))
    return completion_tokens >= threshold


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    text = raw.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def content_looks_truncated(text: str) -> bool:
    """Heuristic: file body was cut off mid-stream at the token limit."""
    if not text or len(text) < 80:
        return False
    stripped = text.rstrip()
    if stripped.endswith("\\"):
        return True
    if stripped.count('"') % 2 == 1 or stripped.count("'") % 2 == 1:
        return True
    if stripped.count('"""') % 2 == 1 or stripped.count("'''") % 2 == 1:
        return True
    if stripped.endswith((",", "(", "[", "{", ":", "\\", "=")):
        return True
    last_line = stripped.rsplit("\n", 1)[-1].strip()
    if last_line.startswith(("def ", "class ", "async def ", "import ", "from ")):
        if not last_line.endswith(":") and last_line.count("(") > last_line.count(")"):
            return True
    return False


def extract_file_tool_target(tool_name: str, args_raw: Any) -> str | None:
    params = _parse_tool_args(args_raw)
    if tool_name == "write":
        from nls.tools.agent_tools.tool_path_args import recover_write_tool_args

        path, _ = recover_write_tool_args(params)
        return path or None
    if tool_name == "edit":
        path = params.get("path", "")
        if isinstance(path, str) and path.strip() and not path.strip().startswith("{"):
            return path.strip()
    return None


def classify_truncated_file_tool(
    tool_name: str,
    args_raw: Any,
    *,
    budget_hit: bool,
) -> str | None:
    """Return truncation kind, or None if the tool call looks complete."""
    if tool_name not in _FILE_WRITE_TOOLS or not budget_hit:
        return None

    params = _parse_tool_args(args_raw)
    if not params and isinstance(args_raw, str) and args_raw.strip():
        return "missing_content"

    if tool_name == "write":
        from nls.tools.agent_tools.tool_path_args import recover_write_tool_args

        _path, content = recover_write_tool_args(params)
        if content is None:
            return "missing_content"
        if content_looks_truncated(content):
            return "partial_content"
        return None

    if tool_name == "edit":
        path = params.get("path", "")
        if isinstance(path, str) and path.strip().startswith("{"):
            return "incomplete_edit"
        old = params.get("old_string")
        new = params.get("new_string")
        if not isinstance(path, str) or not path.strip():
            return "incomplete_edit"
        if old is None and new is None:
            return "incomplete_edit"
        if isinstance(old, str) and old.strip() and new is None:
            return "incomplete_edit"
        if isinstance(new, str) and new.strip() and old is None:
            return "incomplete_edit"
        for blob in (old, new):
            if isinstance(blob, str) and content_looks_truncated(blob):
                return "partial_content"
    return None


def file_tool_call_looks_truncated(tool_name: str, args_raw: Any) -> bool:
    """Backward-compatible boolean check (budget_hit assumed True)."""
    return classify_truncated_file_tool(
        tool_name, args_raw, budget_hit=True,
    ) is not None


def analyze_generation_budget(
    response: GenerationResult,
    config: LoopConfig,
) -> GenerationBudgetAnalysis:
    """Classify budget-related failure modes for a single generation turn."""
    max_tok = config.max_new_tokens
    comp = response.completion_tokens
    finish = (getattr(response, "finish_reason", "") or "").strip().lower()
    exhausted = output_budget_exhausted(comp, max_tok, finish_reason=finish)
    budget_hit = exhausted or finish == "length"

    thinking_budget_exhausted = (
        not response.tool_calls
        and bool(response.thinking)
        and (
            finish == "length"
            or (exhausted and len(response.thinking) > 500)
        )
    )

    events: list[TruncatedFileToolEvent] = []
    if response.tool_calls and budget_hit:
        for tc in response.tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            args_raw = fn.get("arguments", "")
            kind = classify_truncated_file_tool(
                name, args_raw, budget_hit=True,
            )
            if not kind:
                continue
            events.append(TruncatedFileToolEvent(
                tool_name=name,
                target_path=extract_file_tool_target(name, args_raw) or "",
                kind=kind,
            ))

    truncated = [e.tool_name for e in events]
    if events:
        logger.info(
            "Generation budget: truncated file events=%s completion_tokens=%d "
            "max=%d finish=%s",
            [(e.tool_name, e.kind, e.target_path[:60]) for e in events],
            comp,
            max_tok,
            finish or "?",
        )
    if thinking_budget_exhausted:
        logger.info(
            "Generation budget: thinking-only length stop "
            "thinking_len=%d completion_tokens=%d max=%d",
            len(response.thinking),
            comp,
            max_tok,
        )

    return GenerationBudgetAnalysis(
        output_budget_exhausted=exhausted,
        thinking_budget_exhausted=thinking_budget_exhausted,
        truncated_file_tools=truncated,
        truncated_file_events=events,
    )


def record_truncated_file_events(
    path_attempts: dict[str, int],
    events: list[TruncatedFileToolEvent],
) -> dict[str, int]:
    """Increment per-path attempt counters; returns updated counts for touched paths."""
    touched: dict[str, int] = {}
    for event in events:
        path = event.target_path.strip()
        if not path:
            continue
        path_attempts[path] = path_attempts.get(path, 0) + 1
        touched[path] = path_attempts[path]
    return touched


def clear_truncated_write_attempt(path_attempts: dict[str, int], path: str) -> None:
    key = (path or "").strip()
    if key:
        path_attempts.pop(key, None)


def should_suppress_error_recovery(file_recovery_injected: bool) -> bool:
    """Skip generic ERROR_RECOVERY when a file-budget message was already injected."""
    return file_recovery_injected


def build_file_tool_recovery_nudge(
    events: list[TruncatedFileToolEvent],
    max_new_tokens: int,
    path_attempts: dict[str, int],
) -> str:
    """Single consolidated recovery message (replaces duplicate nudges)."""
    paths = sorted({e.target_path for e in events if e.target_path})
    kinds = sorted({e.kind for e in events})
    kind_hint = ", ".join(kinds)
    path_hint = ", ".join(paths[:3]) if paths else "target file"
    attempts = [path_attempts.get(p, 0) for p in paths] or [1]
    max_attempt = max(attempts)
    escalated = max_attempt >= TRUNCATED_WRITE_ESCALATE_AFTER

    lines = [
        "FILE OUTPUT TRUNCATED — read the tool error above, then follow this plan.",
        (
            f"Your last response hit the {max_new_tokens}-token limit "
            f"({kind_hint}) before the file tool finished."
        ),
    ]
    if paths:
        lines.append(f"Target: {path_hint}")
    lines.extend([
        "Do NOT retry one large write() in a single tool call.",
        "Retry strategy:",
        "  1. write() a short stub (~30–80 lines) with path and content as separate fields,",
        "  2. then use edit() to add sections incrementally.",
        "Pass path and content as separate top-level fields — never nest JSON inside path.",
    ])
    if escalated:
        lines.extend([
            f"MANDATORY (attempt {max_attempt} on this path): your NEXT tool call MUST be "
            f"a stub write under ~80 lines to {path_hint}, then edit() only.",
            "Do not attempt another full-file write until the stub exists on disk.",
        ])
    return "\n".join(lines)


def build_truncated_file_tool_nudge(
    tool_names: list[str],
    max_new_tokens: int,
) -> str:
    """Legacy helper — prefer build_file_tool_recovery_nudge()."""
    events = [
        TruncatedFileToolEvent(tool_name=n, target_path="", kind="missing_content")
        for n in sorted(set(tool_names))
    ]
    return build_file_tool_recovery_nudge(events, max_new_tokens, {})


def build_thinking_length_nudge(
    thinking_len: int,
    max_new_tokens: int,
) -> str:
    """Legacy alias — prefer tier-1 spiral nudge for thinking-loop recovery."""
    _ = thinking_len, max_new_tokens
    return build_thinking_spiral_nudge_tier1()


def is_thinking_spiral(
    response: GenerationResult,
    budget: GenerationBudgetAnalysis,
    *,
    min_thinking_chars: int = 800,
) -> bool:
    """Detect extended internal reasoning with no tool action."""
    if response.tool_calls:
        return False
    if budget.thinking_budget_exhausted:
        return True

    thinking = (response.thinking or "").strip()
    if not thinking or len(thinking) < min_thinking_chars:
        return False

    text = (response.text or "").strip()
    if text:
        return False

    finish = (getattr(response, "finish_reason", "") or "").strip().lower()
    if finish == "length" or len(thinking) >= 2000:
        return True
    return False


def build_thinking_spiral_nudge_tier1() -> str:
    return (
        "THINKING LOOP DETECTED: you have spiraled into extended internal reasoning "
        "without acting. You already thought about this enough — wrap it up and execute.\n"
        "On your NEXT turn, call a tool immediately or give a concise final answer. "
        "Do not spend another turn re-planning."
    )


def build_thinking_spiral_nudge_tier2() -> str:
    return (
        "THINKING LOOP — SECOND STRIKE: you repeated extended reasoning without acting. "
        "Thinking is DISABLED for this turn — act now with a tool call or concise answer.\n"
        "You already analyzed enough. Execute immediately; do not re-plan."
    )


def build_thinking_spiral_recovery_nudge(consecutive_spirals: int) -> str:
    """Tier-1 nudge on first spiral; tier-2 (thinking off) on second+."""
    if consecutive_spirals >= 2:
        return build_thinking_spiral_nudge_tier2()
    return build_thinking_spiral_nudge_tier1()
