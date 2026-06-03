"""Task-epoch WM hygiene — lifecycle by slot class, not keyword blocklists.

Session-scoped facts (research snapshots, tool activation imperatives) are
cleared when a *new* user/channel task starts.  Orchestration wakes, delegate
continuations, and follow-up turns with the same goals keep session context.

See ``should_begin_task_epoch`` for the boundary rules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from nls.agentic.plan_goal_hygiene import normalize_plan_text, _content_words
from nls.brain.working_memory import (
    ACCESS_GENESIS,
    ACCESS_SESSION,
    ACCESS_SYSTEM,
    WMSlot,
)
from nls.runtime.dispatch_sources import is_orchestration_dispatch_source

logger = logging.getLogger(__name__)

TASK_EPOCH_GOALS_DOMAIN = "TaskEpoch.goals"
TASK_EPOCH_ID_DOMAIN = "TaskEpoch.id"

_FRESH_TASK_EXACT = frozenset({"user", "user:channel"})

_SESSION_DOMAIN_PREFIXES = (
    "Research:",
    "ResearchError:",
    "Digest.",
    "Skill.",
)

_SESSION_CLEAR_RING_IDS = frozenset({
    "project_facts",
    "skills",
    "tools_mcp",
})

_EXPLORATORY_RE = re.compile(
    r"\b("
    r"search|find|check|look up|lookup|compare|review|explore|"
    r"investigate|survey|analyze|research|read"
    r")\b",
    re.IGNORECASE,
)
_DELIVERABLE_RE = re.compile(
    r"\b("
    r"scaffold|build|write|implement|create|deploy|install|ship|"
    r"configure|author|translate|integrate|set up|setup"
    r")\b",
    re.IGNORECASE,
)


def is_fresh_task_dispatch(dispatch_source: str) -> bool:
    """Human- or channel-initiated turns (not scheduler/team wakes)."""
    src = (dispatch_source or "user").strip()
    if src in _FRESH_TASK_EXACT:
        return True
    return src.startswith("user:")


def goals_same_task_epoch(a: list[str], b: list[str]) -> bool:
    """True when two goal lists describe the same task (continuation turn)."""
    if not a or not b:
        return False
    words_a = _content_words(" ".join(a))
    words_b = _content_words(" ".join(b))
    if not words_a or not words_b:
        return a == b
    inter = len(words_a & words_b)
    union = len(words_a | words_b)
    if union == 0:
        return False
    return (inter / union) >= 0.55


def should_begin_task_epoch(
    dispatch_source: str,
    new_goals: list[str],
    prior_goals: list[str] | None,
) -> bool:
    """Whether to rotate session WM for this loop start."""
    if is_orchestration_dispatch_source(dispatch_source):
        return False
    if not is_fresh_task_dispatch(dispatch_source):
        return False
    if not new_goals:
        return False
    if prior_goals and goals_same_task_epoch(new_goals, prior_goals):
        return False
    return True


def is_session_ephemeral_slot(slot: WMSlot) -> bool:
    """Facts that should not survive a new task epoch."""
    if slot.slot_type == "credential":
        return False
    access = getattr(slot, "access", "") or ""
    if access in (ACCESS_GENESIS, ACCESS_SYSTEM):
        return False
    if access == ACCESS_SESSION:
        return True
    meta_class = (slot.metadata or {}).get("slot_class", "")
    if meta_class in ("research_snapshot", "tool_activation"):
        return True
    domain = slot.domain or ""
    if any(domain.startswith(p) for p in _SESSION_DOMAIN_PREFIXES):
        return True
    if slot.source in ("clawhub", "digest") and slot.slot_type == "fact":
        return True
    if slot.source == "tool" and slot.slot_type == "fact":
        if domain.startswith("Research") or domain.startswith("Skill."):
            return True
    return False


def _prune_slots_in_list(slots: list[WMSlot]) -> int:
    before = len(slots)
    kept = [s for s in slots if not is_session_ephemeral_slot(s)]
    removed = before - len(kept)
    slots[:] = kept
    return removed


def clear_session_ephemeral_slots(
    cryptex: Any | None,
    working_memory: Any | None,
) -> int:
    """Drop session-tier research/activation facts; keep credentials & system."""
    removed = 0
    if cryptex is not None:
        rings = getattr(cryptex, "_rings", None) or {}
        for ring_id in _SESSION_CLEAR_RING_IDS:
            ring = rings.get(ring_id)
            if ring is None:
                continue
            positions = getattr(ring, "positions", {}) or {}
            for slots in positions.values():
                if isinstance(slots, list):
                    removed += _prune_slots_in_list(slots)
    if working_memory is not None:
        slots = getattr(working_memory, "_slots", None)
        if isinstance(slots, list):
            removed += _prune_slots_in_list(slots)
    if removed:
        logger.info("[TASK_EPOCH] cleared %d session ephemeral WM slot(s)", removed)
    return removed


def _read_epoch_goals_from_slots(slots: list[WMSlot]) -> list[str]:
    for slot in slots:
        if slot.domain != TASK_EPOCH_GOALS_DOMAIN:
            continue
        try:
            parsed = json.loads(slot.content)
            if isinstance(parsed, list):
                return [str(g) for g in parsed if g]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def read_prior_epoch_goals(
    cryptex: Any | None,
    working_memory: Any | None = None,
) -> list[str]:
    if cryptex is not None:
        try:
            from nls.brain.cryptex import RING_ENVIRONMENT

            ring = cryptex.get_ring(RING_ENVIRONMENT)
            if ring is not None:
                for slots in (getattr(ring, "positions", {}) or {}).values():
                    found = _read_epoch_goals_from_slots(list(slots))
                    if found:
                        return found
        except Exception:
            pass
    if working_memory is not None:
        slots = getattr(working_memory, "_slots", None)
        if isinstance(slots, list):
            return _read_epoch_goals_from_slots(slots)
    return []


def store_task_epoch_marker(
    cryptex: Any | None,
    working_memory: Any | None,
    *,
    loop_id: str,
    goals: list[str],
) -> None:
    payload = json.dumps(goals[:5], ensure_ascii=False)
    if cryptex is not None:
        try:
            cryptex.upsert_environment(
                TASK_EPOCH_ID_DOMAIN,
                loop_id,
                source="task_epoch",
                salience=0.55,
            )
            cryptex.upsert_environment(
                TASK_EPOCH_GOALS_DOMAIN,
                payload,
                source="task_epoch",
                salience=0.55,
            )
        except Exception:
            pass
    if working_memory is not None:
        try:
            upsert = getattr(working_memory, "upsert_fact", None)
            if callable(upsert):
                upsert(
                    TASK_EPOCH_GOALS_DOMAIN,
                    payload,
                    source="task_epoch",
                    salience=0.55,
                )
        except Exception:
            pass


def begin_task_epoch(
    cryptex: Any | None,
    working_memory: Any | None,
    *,
    loop_id: str,
    goals: list[str],
    dispatch_source: str,
) -> bool:
    """Rotate session WM when a new task starts. Returns True if rotated."""
    prior = read_prior_epoch_goals(cryptex, working_memory)
    if not should_begin_task_epoch(dispatch_source, goals, prior or None):
        return False
    clear_session_ephemeral_slots(cryptex, working_memory)
    store_task_epoch_marker(cryptex, working_memory, loop_id=loop_id, goals=goals)
    logger.info(
        "[TASK_EPOCH] began epoch %s goals=%d dispatch=%s",
        (loop_id or "")[:8] or "?",
        len(goals),
        dispatch_source,
    )
    return True


def goal_overlaps_fact(goal_text: str, slot: WMSlot) -> bool:
    """True when a session fact likely supported this goal only."""
    g_words = _content_words(goal_text)
    if not g_words:
        return False
    blob = normalize_plan_text(
        f"{slot.domain} {(slot.content or '')[:300]}",
    )
    f_words = _content_words(blob)
    if not f_words:
        return False
    inter = len(g_words & f_words)
    union = len(g_words | f_words)
    if union == 0:
        return False
    return (inter / union) >= 0.35


def prune_supporting_facts_for_goal(
    cryptex: Any | None,
    working_memory: Any | None,
    goal_text: str,
) -> int:
    """Remove session facts that overlap a completed goal."""
    if not (goal_text or "").strip():
        return 0
    removed = 0
    if cryptex is not None:
        rings = getattr(cryptex, "_rings", None) or {}
        for ring_id in _SESSION_CLEAR_RING_IDS:
            ring = rings.get(ring_id)
            if ring is None:
                continue
            positions = getattr(ring, "positions", {}) or {}
            for pos_id, slots in positions.items():
                if not isinstance(slots, list):
                    continue
                before = len(slots)
                positions[pos_id] = [
                    s for s in slots
                    if not (
                        is_session_ephemeral_slot(s)
                        and goal_overlaps_fact(goal_text, s)
                    )
                ]
                removed += before - len(positions[pos_id])
    if working_memory is not None:
        slots = getattr(working_memory, "_slots", None)
        if isinstance(slots, list):
            before = len(slots)
            slots[:] = [
                s for s in slots
                if not (
                    is_session_ephemeral_slot(s)
                    and goal_overlaps_fact(goal_text, s)
                )
            ]
            removed += before - len(slots)
    if removed:
        logger.info(
            "[TASK_EPOCH] pruned %d supporting fact(s) for completed goal",
            removed,
        )
    return removed


def reconcile_goals_with_hints(
    goals: list[str],
    hints: list[str] | None,
) -> list[str]:
    """Prefer deliverable goals over exploratory ones when hints say how to build."""
    if not goals:
        return goals
    hint_tokens = {h.strip().lower() for h in (hints or []) if h and h.strip()}
    has_setup = any(t.startswith("setup:") for t in hint_tokens)

    exploratory = [g for g in goals if _EXPLORATORY_RE.search(g or "")]
    deliverable = [g for g in goals if _DELIVERABLE_RE.search(g or "")]
    non_exploratory = [g for g in goals if g not in exploratory]

    if deliverable and exploratory:
        merged = deliverable + [g for g in non_exploratory if g not in deliverable]
        return merged[:5] if merged else goals

    if has_setup and exploratory and not deliverable:
        primary = (
            "Scaffold and activate the requested skill "
            "(workspace files, verify, skill_install)"
        )
        return [primary] + [g for g in goals if g not in exploratory][:4]

    if has_setup and len(exploratory) == len(goals) and len(goals) == 1:
        return [
            "Scaffold and activate the requested skill "
            "(workspace files, verify, skill_install)",
        ]

    return goals


def research_domain_key(tool_name: str, args: dict[str, Any]) -> str:
    """Stable WM domain for research supersession (same URL/path → replace)."""
    raw = (
        args.get("url")
        or args.get("path")
        or args.get("query")
        or args.get("pattern")
        or args.get("command")
        or ""
    )
    key = normalize_plan_text(str(raw))[:120]
    if not key:
        return f"Research:{tool_name}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"Research:{tool_name}:{digest}"


def apply_goal_evaluation_to_wm(
    hooks: Any,
    goals: list[str],
    pending_indices: list[int],
    *,
    previous_pending: list[int] | None = None,
) -> None:
    """Sync newly completed goals from evaluator into WM."""
    if not goals or hooks is None:
        return
    mark_done = getattr(hooks, "wm_mark_task_goal_done", None)
    prune = getattr(hooks, "wm_prune_supporting_facts_for_goal", None)
    if previous_pending is None:
        previous_pending = list(range(len(goals)))
    prev_set = set(previous_pending)
    curr_set = set(pending_indices)
    newly_done = sorted(prev_set - curr_set)
    for i in newly_done:
        if i < 0 or i >= len(goals):
            continue
        goal = goals[i]
        if not (goal or "").strip():
            continue
        if callable(mark_done):
            try:
                mark_done(goal)
            except Exception:
                pass
        if callable(prune):
            try:
                prune(goal)
            except Exception:
                pass


def session_slot_kwargs(
    *,
    slot_class: str,
    loop_id: str = "",
) -> dict[str, Any]:
    """Keyword args for upsert_slot / upsert_fact on ephemeral task data."""
    meta: dict[str, Any] = {"slot_class": slot_class}
    if loop_id:
        meta["task_epoch"] = loop_id
    return {
        "access": ACCESS_SESSION,
        "metadata": meta,
    }
