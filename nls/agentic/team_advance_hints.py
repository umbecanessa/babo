"""Team advance / post-approve breadcrumbs — avoid advance spam while members run."""

from __future__ import annotations

from typing import Any


def _running_members(team: Any) -> list[Any]:
    if team is None:
        return []
    return [
        m for m in getattr(team, "members", [])
        if getattr(m, "status", None) in ("running", "pending")
    ]


def _pending_review_delegate_numbers(team_manager: Any, team_id: str) -> list[int]:
    pending: list[int] = []
    reviews = getattr(team_manager, "_pending_completion_reviews", None) or {}
    for delegate_num, info in reviews.items():
        if info.get("team_id") == team_id:
            pending.append(int(delegate_num))
    return pending


def format_advance_blocked_message(
    team_id: str,
    *,
    reason: str,
    team: Any | None = None,
    team_manager: Any | None = None,
) -> str:
    """Human-readable advance rejection with actionable next steps."""
    lines = [f"Cannot advance team {team_id}: {reason}", ""]

    running = _running_members(team)
    if running:
        lines.append("Still active on this wave:")
        for m in running:
            dn = getattr(m, "delegate_number", "?")
            task = (getattr(m, "task", "") or "").split("\n")[0][:72]
            lines.append(
                f"  - Delegate #{dn} ({getattr(m, 'status', '?')}): {task}"
            )
        lines.append("")
        lines.append(
            "[BREADCRUMB] Do NOT call team(advance) again until the wave is "
            "quiet. Options:"
        )
        lines.append(
            f"  • team(action='inspect', team_id='{team_id}') — status snapshot"
        )
        lines.append(
            "  • await_delegates(summary='...') — end turn while they finish"
        )
        lines.append(
            "  • team(action='intervene', decision='hint'|'approve') when a "
            "member is in completion review"
        )
        return "\n".join(lines)

    if team_manager is not None:
        pending = _pending_review_delegate_numbers(team_manager, team_id)
        if pending:
            lines.append("Delegates awaiting completion review (not advance yet):")
            for dn in pending:
                lines.append(f"  - Delegate #{dn}")
            lines.append("")
            lines.append(
                "[BREADCRUMB] Spot-check outputs (read/list_dir), then "
                f"team(intervene, team_id='{team_id}', decision='approve') "
                "per waiting member. Advance only when all members are done "
                "or approved."
            )
            return "\n".join(lines)

    lines.append(
        "[BREADCRUMB] team(action='inspect', team_id='"
        f"{team_id}') then resolve blockers before team(advance)."
    )
    return "\n".join(lines)


def format_post_approve_breadcrumb(
    team_id: str,
    *,
    team: Any | None = None,
    team_manager: Any | None = None,
    approved_delegate_number: int | None = None,
) -> str:
    """After approve — never nudge advance while siblings still run or review pending."""
    running = _running_members(team)
    pending_reviews: list[int] = []
    if team_manager is not None:
        pending_reviews = _pending_review_delegate_numbers(team_manager, team_id)

    lines = [
        "[BREADCRUMB] Delegate approved.",
    ]
    if approved_delegate_number is not None:
        lines[0] = f"[BREADCRUMB] Delegate #{approved_delegate_number} approved."

    if running:
        nums = ", ".join(f"#{getattr(m, 'delegate_number', '?')}" for m in running)
        lines.append(
            f"Still running on this wave: {nums}. "
            "Do NOT team(advance) yet."
        )
        lines.append(
            f"Use team(action='inspect', team_id='{team_id}') or "
            "await_delegates(summary='...') until the wave is quiet."
        )
        return "\n".join(lines)

    if pending_reviews:
        nums = ", ".join(f"#{n}" for n in pending_reviews)
        lines.append(
            f"Other member(s) still in completion review: {nums}. "
            "Review and approve them before team(advance)."
        )
        lines.append(
            "Spot-check with read/list_dir before each approve."
        )
        return "\n".join(lines)

    lines.append(
        "Release bar: read/list_dir + smoke-test critical paths before advance."
    )
    lines.append(
        f"Wave fully quiet — you may team(action='advance', team_id='{team_id}')."
    )
    return "\n".join(lines)


def format_intervene_terminal_member_block(
    team_id: str,
    member_idx: int,
    member: Any,
    *,
    team: Any | None = None,
    decision: str = "",
) -> str:
    """Reject hint/extend on a member that is already terminal."""
    status = getattr(member, "status", "unknown")
    dn = getattr(member, "delegate_number", "?")
    lines = [
        f"Cannot {decision or 'intervene'} team {team_id} member #{member_idx}: "
        f"status is {status!r} (delegate #{dn}).",
        "Finished members cannot receive hints or extensions.",
        "",
    ]
    running = _running_members(team)
    if running:
        lines.append("Members still active on this wave:")
        members = getattr(team, "members", []) if team is not None else []
        for i, m in enumerate(members):
            if getattr(m, "status", None) not in ("running", "pending"):
                continue
            task = (getattr(m, "task", "") or "").split("\n")[0][:72]
            lines.append(
                f"  - Member #{i} delegate #{getattr(m, 'delegate_number', '?')} "
                f"({getattr(m, 'status', '?')}): {task}"
            )
    else:
        lines.append("No members still running on this wave.")
    lines.extend([
        "",
        f"[BREADCRUMB] team(action='inspect', team_id='{team_id}') for a live snapshot. "
        "Use team(decision='approve') only for members in completion review, "
        "or team(advance) when the wave is fully quiet.",
    ])
    return "\n".join(lines)
