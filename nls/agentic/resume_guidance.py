"""Session-resume guidance after app restart or status-check user messages."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nls.agentic.plan_store import Plan

_RESUME_USER_RE = re.compile(
    r"\b("
    r"continue|resume|pick\s*up|carry\s+on|proceed|"
    r"where\s+(we\s+)?(left|stand)|left\s+off|"
    r"good\s+morning|app\s+(was\s+)?closed|shut\s+down|"
    r"restarted?|crashed|from\s+where\s+we"
    r")\b",
    re.IGNORECASE,
)


def user_requests_session_resume(user_input: str) -> bool:
    """True when the user is asking to resume or assess project status."""
    text = (user_input or "").strip()
    if len(text) < 8:
        return False
    return bool(_RESUME_USER_RE.search(text))


def build_session_resume_guidance(
    plan: "Plan",
    *,
    blocking_team: bool = False,
) -> str:
    """One-shot system message: reconcile plan state, then act — no re-scan loops."""
    done = [s for s in plan.steps if s.status in ("done", "skipped")]
    failed = [s for s in plan.steps if s.status == "failed"]
    pending = [s for s in plan.steps if s.status in ("pending", "in_progress")]
    delegatable_pending = [
        s for s in plan.steps
        if s.delegatable and s.status not in ("done", "skipped")
    ]

    from nls.agentic.plan_store import (
        detect_dependency_cycles,
        format_dependency_cycle_hints,
    )

    lines = [
        "[SESSION RESUME — READ ONCE, THEN ACT]",
        f"Active plan: {plan.id} — {plan.title!r} ({len(done)}/{len(plan.steps)} steps done).",
        "The user already has progress in chat/UI. Do NOT repeat the same status "
        "summary across iterations.",
        "RULES FOR THIS TURN:",
        "1. Call plan(action='read', plan_id=...) ONCE if you need the canonical step list.",
        "2. At most ONE quick disk check (list_dir OR read on a specific path) if a step "
        "is marked failed but may be done — then reconcile the plan.",
        "3. Do NOT loop on read/list_dir/glob/grep without plan or team mutations.",
        "4. Do NOT plan(delete) while done steps remain — use plan(fix_dependencies) "
        "or plan(update, depends_on=[...]) if launch is blocked.",
    ]

    if detect_dependency_cycles(plan):
        _cycle = format_dependency_cycle_hints(plan)
        if _cycle:
            lines.append(_cycle)

    if failed:
        labels = ", ".join(f'"{s.label}"' for s in failed[:4])
        lines.append(
            f"Failed step(s) on record: {labels}. "
            "If artifacts exist: plan(action='accept_partial', ...) or "
            "plan(action='update', status='done', notes='...') with evidence. "
            "Do not re-audit the whole repo."
        )

    if pending:
        labels = ", ".join(f'"{s.label}"' for s in pending[:4])
        lines.append(f"Still open: {labels}.")

    if delegatable_pending:
        if len(delegatable_pending) >= 2:
            lines.append(
                "NEXT: switch_mode(delegating) → team(create, plan_id=..., wave=N) → "
                "team(launch). Do not implement multi-step work yourself."
            )
        else:
            step = delegatable_pending[0]
            lines.append(
                f"NEXT: finish delegatable step \"{step.label}\" — "
                "team(create+launch) for that step OR complete it directly, then "
                "plan(action='complete') when all steps are verified."
            )
    elif not pending and not failed:
        lines.append(
            "All steps appear done — verify once, then plan(action='complete') if accurate."
        )

    if blocking_team:
        lines.append(
            "A team still shows active with live delegates — team(inspect) once, "
            "then await_delegates or team(advance) after review. "
            "Do not idle-poll."
        )
    elif not delegatable_pending and (failed or pending):
        lines.append(
            "No live delegates — use EVALUATING/DELEGATING mode and advance the plan; "
            "do not stay in read-only assessment."
        )

    return "\n".join(lines)
