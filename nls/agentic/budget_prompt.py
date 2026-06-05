"""User-facing iteration/time budget extension prompts for the orchestrator loop."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from nls.agentic.task_epoch_hygiene import is_fresh_task_dispatch

_EXTEND_RE = re.compile(
    r"(?:\+?\s*(\d+)\s*(?:more\s+)?(?:iterations?|steps?|turns?)?"
    r"|(?:extend|continue|keep\s+going|more\s+time)\s*(?:by\s+)?(\d+)?"
    r"|^(\d+)\s*$)",
    re.IGNORECASE,
)
_STOP_RE = re.compile(
    r"^(?:no|nope|nah|stop|done|wrap\s+up|enough|quit|cancel|terminate)\b",
    re.IGNORECASE,
)
_AFFIRMATIVE_RE = re.compile(
    r"^(?:yes|yep|yeah|ok(?:ay)?|sure|go\s+ahead|continue|proceed)\b",
    re.IGNORECASE,
)

HINT_EXPLORE_PARALLEL_READS = "explore:parallel_reads"

_REPO_STUDY_RE = re.compile(
    r"\b("
    r"study|analyze|analyse|explore|read|scan|review|survey|inspect|"
    r"understand|map\s+out|walk\s+through|look\s+through"
    r")\b",
    re.IGNORECASE,
)
_REPO_TARGET_RE = re.compile(
    r"\b("
    r"repo(?:sitory)?|codebase|project|monorepo|files?|directory|"
    r"source\s+tree|workspace"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BudgetDecision:
    action: str  # "extend" | "terminate"
    extra_iterations: int = 0
    message: str = ""


def boost_explore_read_hints(user_input: str, hints: list[str]) -> None:
    """Add parallel-read exploration hint when the task implies bulk file study."""
    ui = (user_input or "").strip()
    if not ui:
        return
    tokens = {h.strip().lower() for h in hints if h and h.strip()}
    if HINT_EXPLORE_PARALLEL_READS in tokens:
        return
    if _REPO_STUDY_RE.search(ui) and _REPO_TARGET_RE.search(ui):
        hints.append(HINT_EXPLORE_PARALLEL_READS)


def explore_parallel_reads_system_note() -> str:
    return (
        "LARGE REPO / FILE STUDY: Use list_dir or glob to prioritize paths first. "
        "Then issue multiple read() calls in the SAME turn (parallel batch) — "
        "do not read one file per iteration. Skip paths already in context."
    )


def should_prompt_user_for_budget(
    reason: str,
    config: Any,
    state: Any,
    *,
    has_active_team: bool,
    copilot_queue: Any | None,
) -> bool:
    if reason not in ("max_iterations", "total_timeout"):
        return False
    if not getattr(config, "prompt_user_on_budget_exhaust", True):
        return False
    if not getattr(config, "enable_delegation", True):
        return False
    if getattr(config, "escalate_on_limit", False):
        return False
    if copilot_queue is None:
        return False
    if has_active_team:
        return False
    if state.user_budget_prompts >= getattr(config, "max_user_budget_prompts", 3):
        return False
    if not is_fresh_task_dispatch(getattr(state, "dispatch_source", "") or ""):
        return False
    if int(config.max_iterations) >= int(config.max_total_iterations):
        return False
    if clamp_extension(config, 1) <= 0:
        return False
    return True


def format_budget_prompt_message(
    reason: str,
    *,
    iteration: int,
    max_iterations: int,
    options: tuple[int, ...] | list[int],
    elapsed_seconds: float | None = None,
    timeout_seconds: float | None = None,
) -> str:
    opts = list(options)
    opt_text = ", ".join(str(o) for o in opts)
    if reason == "total_timeout":
        elapsed = int(elapsed_seconds or 0)
        limit = int(timeout_seconds or 0)
        head = (
            f"I've been working for {elapsed}s and reached the time limit "
            f"({limit}s)."
        )
    else:
        head = (
            f"I've used {iteration} of {max_iterations} execution steps."
        )
    return (
        f"{head} I can keep going if you'd like.\n"
        f"Reply with {opt_text} for more steps, or say stop to wrap up."
    )


def format_channel_budget_prompt(
    reason: str,
    *,
    iteration: int,
    max_iterations: int,
    options: tuple[int, ...] | list[int],
    elapsed_seconds: float | None = None,
    timeout_seconds: float | None = None,
) -> str:
    opts = list(options)
    numbered = " | ".join(f"{o} (+{o} steps)" for o in opts)
    if reason == "total_timeout":
        elapsed = int(elapsed_seconds or 0)
        limit = int(timeout_seconds or 0)
        head = f"Time limit reached ({elapsed}s / {limit}s)."
    else:
        head = f"Step limit reached ({iteration}/{max_iterations})."
    return (
        f"{head} Still working on your request.\n"
        f"Continue? Reply: {numbered} | stop"
    )


_AFFIRMATIVE_ONLY = frozenset({
    "y", "yes", "yes.", "yep", "yeah", "yup", "ok", "ok.", "okay", "sure",
})


def classify_budget_response(text: str, options: tuple[int, ...] | list[int]) -> BudgetDecision | None:
    """Parse a user reply as a budget decision.

    Longer messages and open-ended guidance (e.g. "continue reading auth")
    return None so the loop can treat them as steering instead of extend.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if _STOP_RE.match(raw):
        return BudgetDecision(action="terminate")

    opts = sorted({int(o) for o in options if int(o) > 0})
    if not opts:
        opts = [10, 20, 40]

    low = raw.lower()
    if low in _AFFIRMATIVE_ONLY:
        return BudgetDecision(action="extend", extra_iterations=opts[0])

    if re.fullmatch(r"\+\s*\d+", raw):
        val = int(re.sub(r"\D", "", raw))
        if val in opts:
            return BudgetDecision(action="extend", extra_iterations=val)
        nearest = min(opts, key=lambda o: abs(o - val))
        return BudgetDecision(action="extend", extra_iterations=nearest)

    if re.fullmatch(r"\d+", raw):
        val = int(raw)
        if val in opts:
            return BudgetDecision(action="extend", extra_iterations=val)
        nearest = min(opts, key=lambda o: abs(o - val))
        return BudgetDecision(action="extend", extra_iterations=nearest)

    for opt in opts:
        if re.fullmatch(rf"\+?\s*{opt}\s*(?:steps?|iters?(?:ations?)?)?", raw, re.I):
            return BudgetDecision(action="extend", extra_iterations=opt)

    m = _EXTEND_RE.search(raw)
    if m and len(raw) <= 32:
        for g in m.groups():
            if g and g.isdigit():
                val = int(g)
                if val in opts:
                    return BudgetDecision(action="extend", extra_iterations=val)
                nearest = min(opts, key=lambda o: abs(o - val))
                return BudgetDecision(action="extend", extra_iterations=nearest)

    return None


def parse_budget_decision(
    item: Any,
    options: tuple[int, ...] | list[int],
) -> BudgetDecision | None:
    if isinstance(item, dict):
        action = str(item.get("action", "") or "").strip().lower()
        if action in ("terminate", "stop", "deny", "no"):
            return BudgetDecision(action="terminate", message=str(item.get("message", "") or ""))
        if action in ("extend", "hint"):
            try:
                extra = int(item.get("extra_iterations", 0) or 0)
            except (TypeError, ValueError):
                extra = 0
            if extra <= 0:
                extra = min(options) if options else 10
            return BudgetDecision(
                action="extend",
                extra_iterations=extra,
                message=str(item.get("message", "") or ""),
            )
        return None
    if isinstance(item, str):
        return classify_budget_response(item, options)
    return None


def clamp_extension(
    config: Any,
    extra_iterations: int,
) -> int:
    try:
        extra = max(1, int(extra_iterations))
    except (TypeError, ValueError):
        extra = 10
    remaining = max(0, int(config.max_total_iterations) - int(config.max_iterations))
    return min(extra, remaining) if remaining else 0
