"""Orchestration wake coalescing and Cryptex attention-board sync.

Prevents stacked ``team_completion_review:{team}:{delegate}`` dispatches by
scheduling one batched wake per team and mirroring pending work onto the
Cryptex wake-attention ring.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Canonical batched source — inner_loop dedupes on this exact string per team.
def completion_review_source(team_id: str) -> str:
    return f"team_completion_review:{team_id}"


def completion_review_team_prefix(team_id: str) -> str:
    return f"team_completion_review:{team_id}"


def is_completion_review_source(source: str) -> bool:
    return (source or "").startswith("team_completion_review:")


def member_escalation_source(team_id: str, delegate_number: int) -> str:
    """Per-delegate wake — bypasses completion-review batching."""
    return f"team_member_escalation:{team_id}:{delegate_number}"


def is_member_escalation_source(source: str) -> bool:
    return (source or "").startswith("team_member_escalation:")


def parse_completion_review_team_id(source: str) -> str:
    """Extract team_id from team_completion_review:{team_id}[:suffix]."""
    parts = (source or "").split(":")
    if len(parts) >= 2 and parts[0] == "team_completion_review":
        return parts[1]
    return ""


def build_batched_completion_review_message(
    team_manager: Any,
    team_id: str,
) -> str:
    """Single EM packet listing all pending completion reviews for a team."""
    team = team_manager._teams.get(team_id)
    if team is None:
        return f"[COMPLETION REVIEW — team {team_id} not found]"

    pending = getattr(team_manager, "_pending_completion_reviews", {}) or {}
    lines = [
        f"[COMPLETION REVIEW — BATCH — {team.name}]",
        f"Team ID: {team_id}",
        f"Wave: {team.wave_index + 1}",
        "",
        "Delegates blocked until you intervene. Work through the list once:",
    ]
    n_pending = 0
    for delegate_num, info in sorted(pending.items(), key=lambda x: x[0]):
        if info.get("team_id") != team_id:
            continue
        member_idx = int(info.get("member_idx", 0))
        if member_idx >= len(team.members):
            continue
        member = team.members[member_idx]
        if member.status in ("done", "failed", "cancelled"):
            continue
        n_pending += 1
        lines.append(
            f"  • Delegate #{delegate_num} member={member_idx}: "
            f"{member.task[:80]}…"
        )
        lines.append(
            f"    team(action='intervene', team_id='{team_id}', "
            f"member={member_idx}, decision='approve')"
        )

    if n_pending == 0:
        lines.append("  (no pending reviews — inspect team and advance if terminal)")

    running = [
        m for m in team.members
        if m.status in ("running", "pending")
    ]
    if running:
        labels = ", ".join(f"#{m.delegate_number}" for m in running)
        lines.extend([
            "",
            f"Still executing (not in review): {labels}",
            "Do NOT team(advance) until they finish or you approve waiting delegates.",
        ])

    lines.extend([
        "",
        "RULES: One inspect if needed, then approve each waiting member. "
        "Do NOT approve members already marked done.",
    ])
    return "\n".join(lines)


def schedule_member_escalation_wake(
    team_manager: Any,
    team: Any,
    delegate_number: int,
    help_msg: str,
) -> bool:
    """Immediate orchestrator wake for proactive (or repeat) delegate escalate.

    Does NOT use the per-team completion-review batch gate. Clears any stale
    completion-review slot for this delegate so help flows via intervene/extend.
    """
    schedule = getattr(team_manager, "_schedule_orchestration_wake", None)
    if schedule is None or not help_msg.strip():
        return False

    team_manager.clear_completion_review(delegate_number)

    routing = (
        f"[AGENT_MSG|agent_id={team_manager._agent_id}] "
        if getattr(team_manager, "_agent_id", None) else ""
    )
    source = member_escalation_source(team.id, delegate_number)
    try:
        schedule(routing + help_msg.strip(), source)
        logger.info(
            "WakeCoord: member escalation wake delegate #%d (source=%s)",
            delegate_number, source,
        )
        sync_wake_attention_board(team_manager)
        return True
    except Exception:
        logger.debug(
            "WakeCoord: escalation wake failed for #%d",
            delegate_number, exc_info=True,
        )
        return False


def schedule_batched_completion_review_wake(
    team_manager: Any,
    team: Any,
    *,
    is_reminder: bool = False,
) -> bool:
    """Enqueue at most one completion-review wake per team. Returns True if scheduled."""
    schedule = getattr(team_manager, "_schedule_orchestration_wake", None)
    if schedule is None:
        return False

    team_id = team.id
    source = completion_review_source(team_id)
    if is_reminder:
        source = f"{source}:reminder"

    drain = getattr(team_manager, "_dispatch_drain", None)
    if drain is not None:
        # Drop legacy per-delegate queued wakes for this team.
        try:
            removed = 0
            prefix = completion_review_team_prefix(team_id)
            # drain by exact legacy sources is handled via prefix drain helper
            if hasattr(team_manager, "_drain_completion_review_dispatches"):
                removed = team_manager._drain_completion_review_dispatches(team_id)
            if removed:
                logger.info(
                    "WakeCoord: drained %d stale completion-review dispatch(es) for %s",
                    removed, team_id,
                )
        except Exception:
            logger.debug("WakeCoord: drain failed", exc_info=True)

    if getattr(team_manager, "_drain_team_checkback_dispatch", None):
        team_manager._drain_team_checkback_dispatch(team_id)

    msg = build_batched_completion_review_message(team_manager, team_id)
    routing = (
        f"[AGENT_MSG|agent_id={team_manager._agent_id}] "
        if getattr(team_manager, "_agent_id", None) else ""
    )
    try:
        schedule(routing + msg, source)
        logger.info(
            "WakeCoord: batched completion-review wake for %s (source=%s reminder=%s)",
            team_id, source, is_reminder,
        )
        sync_wake_attention_board(team_manager)
        return True
    except Exception:
        logger.debug("WakeCoord: schedule failed for %s", team_id, exc_info=True)
        return False


def sync_wake_attention_board(team_manager: Any) -> None:
    """Mirror pending reviews + active teams onto Cryptex wake-attention ring."""
    hooks = getattr(team_manager, "_hooks", None)
    if hooks is None:
        return
    wm_sync = getattr(hooks, "wm_sync_wake_attention_board", None)
    if wm_sync is None:
        return
    try:
        wm_sync(team_manager)
    except Exception:
        logger.debug("WakeCoord: wm_sync_wake_attention_board failed", exc_info=True)


def orchestration_hygiene_after_member_done(
    team_manager: Any,
    team_id: str,
    member_idx: int,
    delegate_number: int,
) -> None:
    """Clear stale WM escalation/tactical noise when a member finishes."""
    team = team_manager._teams.get(team_id)
    hooks = getattr(team_manager, "_hooks", None)
    if hooks is None:
        return

    resolve = getattr(hooks, "wm_orch_resolve_escalation", None)
    if resolve is not None:
        try:
            resolve(team_id, member_idx, "member_done")
        except Exception:
            pass

    prune = getattr(hooks, "wm_prune_stale_tactical_goals", None)
    if prune is not None and team is not None:
        try:
            prune(team_manager._plan_store, team.plan_id)
        except Exception:
            pass

    sync_wake_attention_board(team_manager)


def orchestration_hygiene_after_wave_advanced(team_manager: Any, team_id: str) -> None:
    """Drain completion-review wakes and refresh board after team(advance)."""
    drain = getattr(team_manager, "_drain_completion_review_dispatches", None)
    if drain is not None:
        try:
            drain(team_id)
        except Exception:
            pass
    if getattr(team_manager, "_drain_team_checkback_dispatch", None):
        team_manager._drain_team_checkback_dispatch(team_id)
    if getattr(team_manager, "_drain_wave_complete_dispatch", None):
        team_manager._drain_wave_complete_dispatch(team_id)

    pending = getattr(team_manager, "_pending_completion_reviews", {})
    for delegate_num in list(pending.keys()):
        info = pending.get(delegate_num, {})
        if info.get("team_id") == team_id:
            team_manager.clear_completion_review(delegate_num)

    sync_wake_attention_board(team_manager)
