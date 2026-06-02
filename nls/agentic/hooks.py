"""Pre-hook and post-hook pipeline for the v3 agentic loop.

Pre-hooks run BEFORE each tool call and can block or redirect.
Post-hooks run AFTER each tool call and observe/update state.

Each hook is a small, testable, single-responsibility function.
The pipeline is ordered and explicit — no nested conditionals.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from nls.tools.agent_tools.base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

# Patterns that indicate NLS infra tampering
_NLS_INFRA_PATTERNS = (
    "python -m nls", "nls.engine", "nls.server",
    "kill -9", "pkill nls", "systemctl stop nls",
    "babo-desktop", "gpu_worker",
)

_EXTENDED_PATH_DIRS: list[str] = []
if sys.platform == "darwin":
    _EXTENDED_PATH_DIRS = [
        "/opt/homebrew/bin", "/usr/local/bin",
        "/usr/bin", "/bin",
    ]
elif sys.platform == "win32":
    _EXTENDED_PATH_DIRS = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
        os.path.expandvars(r"%ProgramData%\chocolatey\bin"),
        os.path.expanduser("~\\scoop\\shims"),
    ]
else:
    _EXTENDED_PATH_DIRS = [
        "/usr/local/bin", "/usr/bin", "/bin",
        "/snap/bin",
    ]

_EXTENDED_PATH = (
    os.pathsep.join(_EXTENDED_PATH_DIRS)
    if _EXTENDED_PATH_DIRS
    else None
)


# ---------------------------------------------------------------------------
# Pre-hook result
# ---------------------------------------------------------------------------


@dataclass
class PreHookResult:
    """Result from a pre-hook check."""

    allow: bool = True
    tool_result: ToolResult | None = None
    skip_execution: bool = False


# ---------------------------------------------------------------------------
# Loop state — shared mutable state across hooks
# ---------------------------------------------------------------------------


@dataclass
class LoopState:
    """Shared mutable state that hooks read and write."""

    consecutive_errors: int = 0
    consecutive_same_tool: dict[str, int] = field(default_factory=dict)
    files_read: set[str] = field(default_factory=set)
    tool_call_history: list[tuple[str, str]] = field(default_factory=list)
    result_hashes: list[tuple[str, str]] = field(default_factory=list)
    not_found_tools: dict[str, int] = field(default_factory=dict)
    total_errors: int = 0
    total_successes: int = 0
    on_recovery: Callable[[], None] | None = None


def _tool_path_from_args(args: dict[str, Any]) -> str:
    """File path from tool args (read/write/edit use ``path``)."""
    raw = args.get("path") or args.get("file_path") or ""
    return raw.strip() if isinstance(raw, str) else ""


# ---------------------------------------------------------------------------
# Pre-hooks
# ---------------------------------------------------------------------------


_RESULT_REPEAT_THRESHOLD = 3


def loop_detect(
    tool_name: str,
    args: dict[str, Any],
    state: LoopState,
    suppression_shift: float = 0.0,
) -> PreHookResult:
    """Block repeated identical calls or poll-no-progress patterns.

    Two detection modes:
    1. Same (tool_name, args) repeated N times → block (existing).
    2. Same tool returned the same result hash N times → block
       ("poll-no-progress": even with different args, identical
       results mean no progress).

    Uses suppression_shift from thalamus to modulate strictness:
    higher suppression = block sooner.
    """
    args_key = json.dumps(args, sort_keys=True, default=str)[:200]
    call_sig = (tool_name, args_key)

    threshold = max(1, 3 - int(suppression_shift * 2))

    recent = state.tool_call_history[-10:]
    repeat_count = sum(1 for c in recent if c == call_sig)

    if repeat_count >= threshold:
        logger.warning(
            "loop_detect: blocking %s (repeated %d times, threshold %d)",
            tool_name, repeat_count, threshold,
        )
        if tool_name == "read":
            _block_msg = (
                f"BLOCKED: You already read this file ({repeat_count} times). "
                f"Its contents are in your conversation history. "
                f"Analyze what you already have or read a different file."
            )
        else:
            _block_msg = (
                f"BLOCKED: You have called {tool_name} with the same "
                f"arguments {repeat_count} times. Try a different "
                f"approach or different arguments."
            )
        return PreHookResult(
            allow=False,
            tool_result=ToolResult(
                content=_block_msg,
                is_error=True,
                blocked_by_hook=True,
            ),
            skip_execution=True,
        )

    recent_results = state.result_hashes[-10:]
    for prev_tool, prev_hash in recent_results:
        if prev_tool != tool_name:
            continue
        same_result_count = sum(
            1 for t, h in recent_results if t == tool_name and h == prev_hash
        )
        if same_result_count >= _RESULT_REPEAT_THRESHOLD:
            logger.warning(
                "loop_detect: blocking %s (same result %d times — "
                "poll-no-progress)",
                tool_name, same_result_count,
            )
            return PreHookResult(
                allow=False,
                tool_result=ToolResult(
                    content=(
                        f"BLOCKED: {tool_name} has returned the same "
                        f"result {same_result_count} times. The data you "
                        f"need is already in your context. Analyze what "
                        f"you have, try different parameters, or use a "
                        f"different tool entirely."
                    ),
                    is_error=True,
                    blocked_by_hook=True,
                ),
                skip_execution=True,
            )
        break

    state.tool_call_history.append(call_sig)
    if len(state.tool_call_history) > 30:
        state.tool_call_history = state.tool_call_history[-30:]

    return PreHookResult(allow=True)


def safety_guard(
    tool_name: str,
    args: dict[str, Any],
    state: LoopState,
    tool_map: dict[str, AgentTool],
) -> PreHookResult:
    """NLS infrastructure protection and read-before-edit."""

    if tool_name == "edit":
        fpath = _tool_path_from_args(args)
        if fpath and fpath not in state.files_read:
            return PreHookResult(
                allow=False,
                tool_result=ToolResult(
                    content=(
                        f"You must call read('{fpath}') before editing it, "
                        f"or create it with write() in this session first. "
                        f"This prevents blind edits that break code."
                    ),
                    is_error=True,
                    blocked_by_hook=True,
                ),
                skip_execution=True,
            )

    if tool_name == "bash":
        cmd = args.get("command", "")
        cmd_lower = cmd.lower()
        for pattern in _NLS_INFRA_PATTERNS:
            if pattern in cmd_lower:
                return PreHookResult(
                    allow=False,
                    tool_result=ToolResult(
                        content=(
                            f"BLOCKED: Command touches NLS infrastructure "
                            f"(matched '{pattern}'). This is not allowed."
                        ),
                        is_error=True,
                        blocked_by_hook=True,
                    ),
                    skip_execution=True,
                )

    return PreHookResult(allow=True)


async def cli_redirect(
    tool_name: str,
    args: dict[str, Any],
    tool_map: dict[str, AgentTool],
    state: LoopState,
    instruction_skill_slugs: frozenset[str] | Callable[[], frozenset[str]] = frozenset(),
) -> PreHookResult:
    """Auto-redirect unknown tool names to bash if they're CLI binaries."""

    if tool_name in tool_map:
        return PreHookResult(allow=True)

    _slugs = instruction_skill_slugs() if callable(instruction_skill_slugs) else instruction_skill_slugs
    if tool_name in _slugs:
        return PreHookResult(
            allow=False,
            tool_result=ToolResult(
                content=(
                    f"'{tool_name}' is an instruction-based skill, not a "
                    f"callable tool. Read the skill instructions in your "
                    f"prompt and follow them using bash() or other tools."
                ),
                is_error=True,
                blocked_by_hook=True,
            ),
            skip_execution=True,
        )

    binary_path = shutil.which(tool_name, path=_EXTENDED_PATH)

    if binary_path:
        cmd_part = args.get("command", "")
        if not cmd_part:
            parts = [
                str(v) for v in args.values()
                if isinstance(v, str)
            ]
            cmd_part = " ".join(parts)

        full_cmd = f"{tool_name} {cmd_part}".strip()

        for pattern in _NLS_INFRA_PATTERNS:
            if pattern in full_cmd.lower():
                return PreHookResult(
                    allow=False,
                    tool_result=ToolResult(
                        content=f"BLOCKED: Command touches NLS infrastructure.",
                        is_error=True,
                        blocked_by_hook=True,
                    ),
                    skip_execution=True,
                )

        bash_tool = tool_map.get("bash")
        if not bash_tool:
            return PreHookResult(allow=True)

        result = await bash_tool.execute({"command": full_cmd})

        _note = (
            f"[Auto-redirected: '{tool_name}' is not a registered tool "
            f"but exists as CLI binary at {binary_path}. "
            f"Ran via bash: {full_cmd}]\n\n"
        )

        is_err = result.is_error
        lo = result.content[:400].lower()
        if not is_err:
            is_err = any(p in lo for p in (
                "unknown flag", "unknown shorthand",
                "unknown command", "not found",
                "command not found", "error:",
                "permission denied", "not logged in",
                "not authenticated", "fatal:",
                "INTERACTIVE PROMPT DETECTED",
            ))

        return PreHookResult(
            allow=False,
            tool_result=ToolResult(
                content=_note + result.content,
                is_error=is_err,
                details=result.details,
            ),
            skip_execution=True,
        )

    count = state.not_found_tools.get(tool_name, 0) + 1
    state.not_found_tools[tool_name] = count

    available = sorted(tool_map.keys())[:15]
    available_str = ", ".join(available)

    return PreHookResult(
        allow=False,
        tool_result=ToolResult(
            content=(
                f"Tool '{tool_name}' does not exist. "
                f"Available tools: {available_str}.\n"
                f"Use one of these directly, or search ClawHub for a "
                f"skill: clawhub(action='search', query='{tool_name}')"
            ),
            is_error=True,
            blocked_by_hook=True,
        ),
        skip_execution=True,
    )


# ---------------------------------------------------------------------------
# Pre-hook pipeline runner
# ---------------------------------------------------------------------------


async def run_pre_hooks(
    tool_name: str,
    args: dict[str, Any],
    state: LoopState,
    tool_map: dict[str, AgentTool],
    suppression_shift: float = 0.0,
    instruction_skill_slugs: frozenset[str] | Callable[[], frozenset[str]] = frozenset(),
) -> PreHookResult:
    """Run all pre-hooks in order. Short-circuit on first block."""

    result = loop_detect(tool_name, args, state, suppression_shift)
    if not result.allow:
        return result

    result = safety_guard(tool_name, args, state, tool_map)
    if not result.allow:
        return result

    result = await cli_redirect(
        tool_name, args, tool_map, state, instruction_skill_slugs,
    )
    if not result.allow:
        return result

    return PreHookResult(allow=True)


# ---------------------------------------------------------------------------
# Post-hooks
# ---------------------------------------------------------------------------


def error_tracker(
    tool_name: str,
    result: ToolResult,
    state: LoopState,
) -> None:
    """Update consecutive error tracking and soft-error detection."""
    from nls.agentic.tool_result_semantics import counts_toward_error_budget

    _is_cli = tool_name == "bash" or "[Auto-redirected:" in result.content

    is_error = result.is_error
    if not is_error and _is_cli:
        lo = result.content[:500].lower()
        is_error = any(p in lo for p in (
            "is not recognized", "unknown flag",
            "unknown shorthand flag", "unknown command",
            "not found", "permission denied",
            "not logged in", "fatal:",
            "commandnotfoundexception",
        ))

    budget_error = counts_toward_error_budget(tool_name, result) if is_error else False

    if budget_error:
        state.consecutive_errors += 1
        state.total_errors += 1
        state.consecutive_same_tool[tool_name] = (
            state.consecutive_same_tool.get(tool_name, 0) + 1
        )
    elif is_error:
        state.last_error_preview = (result.content or "")[:200]
    else:
        _was_recovering = state.consecutive_errors > 0
        state.consecutive_errors = 0
        state.consecutive_same_tool.pop(tool_name, None)
        state.total_successes += 1
        if _was_recovering and state.on_recovery:
            try:
                state.on_recovery()
            except Exception:
                pass

    if tool_name == "clawhub" and not is_error:
        for slug in list(state.not_found_tools):
            if slug in result.content.lower():
                state.not_found_tools.pop(slug, None)


def track_files_read(
    tool_name: str,
    args: dict[str, Any],
    result: ToolResult,
    state: LoopState,
) -> None:
    """Track files the agent has read (or written) for the read-before-edit guard."""
    if result.is_error or result.blocked_by_hook:
        return
    if tool_name in ("read", "read_file", "write", "write_file"):
        fpath = _tool_path_from_args(args)
        if fpath:
            state.files_read.add(fpath)


_MIN_RESULT_LEN_FOR_HASH = 80
"""Results shorter than this are too generic (e.g. "(no output)", "ok") to
meaningfully indicate poll-no-progress.  Different bash commands that all
return nothing should NOT collide in the hash table."""


def track_result_hash(
    tool_name: str,
    result: ToolResult,
    state: LoopState,
    args: dict[str, Any] | None = None,
) -> None:
    """Hash tool result content for poll-no-progress detection.

    Short/empty results are skipped — they are too generic to signal
    that no progress is being made.  For bash, the command is mixed
    into the hash so that different commands yielding identical output
    (e.g. both return nothing) don't collide.
    """
    if result.is_error or result.blocked_by_hook:
        return
    content = result.content or ""
    if len(content) < _MIN_RESULT_LEN_FOR_HASH:
        return
    hash_input = content
    if tool_name == "bash" and args:
        cmd = args.get("command", "")
        if cmd:
            hash_input = f"{cmd}\n{content}"
    digest = hashlib.md5(
        hash_input.encode("utf-8", errors="replace"),
    ).hexdigest()[:16]
    state.result_hashes.append((tool_name, digest))
    if len(state.result_hashes) > 30:
        state.result_hashes = state.result_hashes[-30:]


def run_post_hooks(
    tool_name: str,
    args: dict[str, Any],
    result: ToolResult,
    state: LoopState,
) -> None:
    """Run all post-hooks after tool execution."""
    error_tracker(tool_name, result, state)
    track_files_read(tool_name, args, result, state)
    track_result_hash(tool_name, result, state, args=args)
