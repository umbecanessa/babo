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


def parse_member_escalation_source(source: str) -> tuple[str, int]:
    """Extract (team_id, delegate_number) from team_member_escalation:{team}:{delegate}."""
    prefix = "team_member_escalation:"
    if not (source or "").startswith(prefix):
        return "", 0
    rest = source[len(prefix):]
    if ":" not in rest:
        return rest, 0
    team_id, delegate_s = rest.rsplit(":", 1)
    try:
        return team_id, int(delegate_s)
    except ValueError:
        return team_id, 0


def member_escalation_is_stale(
    team_manager: Any,
    team_id: str,
    delegate_number: int,
) -> bool:
    """True when an escalation wake for this delegate should not run."""
    if not team_id or delegate_number < 0:
        return True
    team = team_manager._teams.get(team_id)
    if team is None:
        return True
    reconcile = getattr(team_manager, "reconcile_with_delegates", None)
    if reconcile is not None:
        try:
            reconcile(team_id=team_id, persist=False)
        except Exception:
            logger.debug("WakeCoord: reconcile before escalation check failed", exc_info=True)
    member = team.member_by_delegate(delegate_number)
    if member is None:
        return True
    return member.status in ("done", "failed", "cancelled")


def should_skip_stale_orchestration_wake(
    team_manager: Any,
    source: str,
    *,
    context: str = "",
) -> bool:
    """Drop stale completion-review or member-escalation dispatches."""
    if is_completion_review_source(source):
        team_id = parse_completion_review_team_id(source)
        return skip_stale_completion_review_wake(
            team_manager, team_id, context=context or source,
        )
    if is_member_escalation_source(source):
        team_id, delegate_number = parse_member_escalation_source(source)
        return skip_stale_member_escalation_wake(
            team_manager,
            team_id,
            delegate_number,
            context=context or source,
        )
    return False


def skip_stale_member_escalation_wake(
    team_manager: Any,
    team_id: str,
    delegate_number: int,
    *,
    context: str = "",
) -> bool:
    """Drop stale member-escalation work. Returns True if skipped."""
    if not member_escalation_is_stale(team_manager, team_id, delegate_number):
        return False

    member_idx = -1
    team = team_manager._teams.get(team_id)
    if team is not None:
        member = team.member_by_delegate(delegate_number)
        if member is not None:
            member_idx = team.members.index(member)

    hooks = getattr(team_manager, "_hooks", None)
    if hooks is not None and member_idx >= 0:
        resolve = getattr(hooks, "wm_orch_resolve_escalation", None)
        if resolve is not None:
            try:
                resolve(team_id, member_idx, "member_done_stale_wake")
            except Exception:
                pass

    team_manager.clear_completion_review(delegate_number)

    drain = getattr(team_manager, "_drain_member_escalation_dispatch", None)
    if drain is not None:
        try:
            drain(team_id, delegate_number)
        except Exception:
            logger.debug(
                "WakeCoord: escalation drain failed for %s #%d",
                team_id,
                delegate_number,
                exc_info=True,
            )

    drain_cr = getattr(team_manager, "_drain_completion_review_dispatches", None)
    if drain_cr is not None and team_id:
        try:
            drain_cr(team_id)
        except Exception:
            logger.debug(
                "WakeCoord: completion-review drain failed for %s",
                team_id,
                exc_info=True,
            )

    sync_wake_attention_board(team_manager)
    logger.info(
        "WakeCoord: skip stale member-escalation wake for %s delegate #%d (%s)",
        team_id,
        delegate_number,
        context or "member terminal",
    )
    return True


def parse_completion_review_team_id(source: str) -> str:
    """Extract team_id from team_completion_review:{team_id}[:suffix]."""
    parts = (source or "").split(":")
    if len(parts) >= 2 and parts[0] == "team_completion_review":
        return parts[1]
    return ""


def team_completion_review_is_stale(
    team_manager: Any,
    team_id: str,
) -> bool:
    """True when a completion-review wake for *team_id* should not run."""
    if not team_id:
        return True
    team = team_manager._teams.get(team_id)
    if team is None:
        return True
    if getattr(team, "completion_reported", False):
        return True
    if team.is_terminal and pending_completion_review_count(team_manager, team_id) == 0:
        return True
    return False


def skip_stale_completion_review_wake(
    team_manager: Any,
    team_id: str,
    *,
    context: str = "",
) -> bool:
    """Drop stale completion-review work. Returns True if skipped."""
    if not team_completion_review_is_stale(team_manager, team_id):
        return False

    pending = getattr(team_manager, "_pending_completion_reviews", {}) or {}
    for delegate_num in list(pending.keys()):
        info = pending.get(delegate_num, {})
        if info.get("team_id") == team_id:
            team_manager.clear_completion_review(delegate_num)

    drain = getattr(team_manager, "_drain_completion_review_dispatches", None)
    if drain is not None:
        try:
            drain(team_id)
        except Exception:
            logger.debug(
                "WakeCoord: drain failed for stale %s",
                team_id,
                exc_info=True,
            )

    sync_wake_attention_board(team_manager)
    logger.info(
        "WakeCoord: skip stale completion-review wake for %s (%s)",
        team_id,
        context or "closed wave",
    )
    return True


def pending_completion_review_count(
    team_manager: Any,
    team_id: str,
) -> int:
    """Count delegates on *team_id* still waiting for EM completion review."""
    team = team_manager._teams.get(team_id)
    if team is None:
        return 0
    pending = getattr(team_manager, "_pending_completion_reviews", {}) or {}
    n_pending = 0
    for delegate_num, info in pending.items():
        if info.get("team_id") != team_id:
            continue
        member_idx = int(info.get("member_idx", 0))
        if member_idx >= len(team.members):
            continue
        member = team.members[member_idx]
        if member.status in ("done", "failed", "cancelled"):
            continue
        n_pending += 1
    return n_pending


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

    in_review_nums = {
        int(k) for k, info in pending.items()
        if info.get("team_id") == team_id
    }
    still_running = [
        m for m in team.members
        if m.status in ("running", "pending")
        and m.delegate_number not in in_review_nums
    ]
    awaiting_review = [
        m for m in team.members
        if m.status in ("running", "pending")
        and m.delegate_number in in_review_nums
    ]
    if awaiting_review:
        labels = ", ".join(f"#{m.delegate_number}" for m in awaiting_review)
        lines.extend([
            "",
            f"Awaiting your completion review (task_complete, still RUNNING until approve): {labels}",
        ])
    if still_running:
        labels = ", ".join(f"#{m.delegate_number}" for m in still_running)
        lines.extend([
            "",
            f"Still executing (not in review yet): {labels}",
        ])
    if awaiting_review or still_running:
        lines.append(
            "Do NOT team(advance) until they finish or you approve waiting delegates."
        )

    from nls.agentic.verification_hints import completion_review_verify_breadcrumb

    lines.extend([
        "",
        completion_review_verify_breadcrumb(team_id=team_id),
        "",
        "RULES: Spot-check deliverables, then approve each waiting member. "
        "Do NOT approve members already marked done without review.",
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

    if member_escalation_is_stale(team_manager, team.id, delegate_number):
        skip_stale_member_escalation_wake(
            team_manager,
            team.id,
            delegate_number,
            context="schedule",
        )
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
    if skip_stale_completion_review_wake(
        team_manager,
        team_id,
        context="schedule",
    ):
        return False

    n_pending = pending_completion_review_count(team_manager, team_id)
    if n_pending == 0:
        if team.is_terminal:
            logger.debug(
                "WakeCoord: skip completion-review wake for terminal %s (no pending)",
                team_id,
            )
            return False
        running = [
            m for m in team.members
            if m.status in ("running", "pending")
        ]
        if not running:
            logger.debug(
                "WakeCoord: skip completion-review wake for %s (no pending, wave quiet)",
                team_id,
            )
            return False

    source = completion_review_source(team_id)
    if is_reminder:
        source = f"{source}:reminder"
        has_prefix = getattr(team_manager, "_dispatch_has_prefix", None)
        if has_prefix is not None and has_prefix(
            completion_review_team_prefix(team_id),
        ):
            logger.debug(
                "WakeCoord: skip completion-review reminder for %s — already queued",
                team_id,
            )
            return False

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

    drain_esc = getattr(team_manager, "_drain_member_escalation_dispatch", None)
    if drain_esc is not None:
        try:
            drain_esc(team_id, delegate_number)
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
