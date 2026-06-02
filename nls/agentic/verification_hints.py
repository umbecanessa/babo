"""Soft verification reminders for the orchestrator (breadcrumbs, not hard gates).

Delegates self-report via completion review; the EM should spot-check deliverables
before approve and before plan(verify). These strings are injected into wakes,
tool results, and stall breadcrumbs.
"""

from __future__ import annotations

from typing import Any


def completion_review_verify_breadcrumb(*, team_id: str = "") -> str:
    """Reminder when a delegate is waiting in completion review."""
    _tid = f"team_id='{team_id}'" if team_id else "team_id='...'"
    return (
        "[BREADCRUMB] Delegate says done — spot-check and verify before approve\n"
        "task_complete from a delegate is a REQUEST for review, not proof of quality.\n"
        "  1. read/list_dir their output paths (file_history if available)\n"
        "  2. Open the main entrypoint/service — confirm logic exists, not stubs\n"
        "  3. If full-stack step: trace one user flow (UI call → route → handler)\n"
        "  4. Reject placeholders: only package.json, only api.js client, no routes\n"
        "  5. Incomplete → team(intervene, "
        f"{_tid}, member=N, decision='hint', message='<exact gaps>') "
        "or team(rewake) — do NOT approve to unblock the wave\n"
        "  6. Satisfied → team(intervene, decision='approve')\n"
        "team(advance) ONLY when the whole wave is quiet (no running members, "
        "no other pending reviews) — else team(inspect) or await_delegates.\n"
        "You are the ORCHESTRATOR — not a delegate. Never switch_mode(executing) "
        "to do a member's step yourself."
    )


def pre_plan_verify_reminder() -> str:
    """Prepended to plan(action='verify') results — nudge, not a block."""
    return (
        "[BREADCRUMB] Release bar — verify is not a substitute for reading code\n"
        "plan(verify) checks artifacts on disk; YOU still confirm production quality:\n"
        "  • Server starts / build succeeds / tests run (bash in project dir)\n"
        "  • Frontend API paths match real backend routes\n"
        "  • Integrations actually called (not just package.json entries)\n"
        "If you approved delegates without reading files, read/list_dir now. "
        "Fix via team(hint)/rewake or plan(accept_partial), then verify again.\n"
    )


def post_approve_advance_nudge(
    *,
    team_id: str,
    team: Any | None = None,
    team_manager: Any | None = None,
    approved_delegate_number: int | None = None,
) -> str:
    """After approve — do not nudge advance while siblings still run."""
    from nls.agentic.team_advance_hints import format_post_approve_breadcrumb

    return "\n" + format_post_approve_breadcrumb(
        team_id,
        team=team,
        team_manager=team_manager,
        approved_delegate_number=approved_delegate_number,
    )
