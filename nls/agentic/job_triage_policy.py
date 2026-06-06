"""Solo-agent Job charter policy — hint tokens, loop nudges, and triage cleanup.

Intent classification (ongoing role vs one-shot task) is triage micro-inference only.
This module does not regex-parse user messages to add hints or goals.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nls.tools.agent_tools.base import ToolResult

HINT_JOB_CHARTER = "job:charter_candidate"
HINT_JOB_CONFIRM = "continuation:job_confirm"

_JOB_ACTIVE_TOOLS = frozenset({"set_job", "ask_user"})

_JOB_CANDIDATE_KEYS = frozenset({
    "title", "mission", "persona", "playbook", "in_scope", "out_of_scope",
    "default_profile",
})

_JOB_PROPOSAL_RE = re.compile(
    r"\b(?:job charter|set_job|in_scope|out_of_scope|owner_confirmed|"
    r"proposed (?:job|role|charter)|your (?:new )?role)\b",
    re.IGNORECASE,
)

_OWNER_CONFIRM_RE = re.compile(
    r"^\s*(?:"
    r"(?:ok\s+done|done|proceed(?:\s+then)?|continue|retry|go\s+ahead|try\s+again|"
    r"please\s+do|that\s+works|approved?|go\s+for\s+it)"
    r"|(?:yes|yep|yeah|sure)[,.]?\s*(?:that\s+)?(?:looks?\s+good|sounds?\s+good)?"
    r"|(?:looks?\s+good|sounds?\s+good)"
    r")\s*[.!?]?\s*$",
    re.IGNORECASE,
)

from nls.agentic.fleet_triage_policy import (  # noqa: E402
    agent_in_squad,
    fleet_hint_active,
    squad_role_for_agent,
)


def solo_agent_eligible_for_job_charter(agent_id: str) -> bool:
    """Job charter triage applies to solo agents only (not squad lead/member)."""
    return not agent_in_squad(agent_id)


def normalize_job_candidate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _JOB_CANDIDATE_KEYS:
        val = raw.get(key)
        if val is None or val == "":
            continue
        if key in ("in_scope", "out_of_scope"):
            if isinstance(val, list):
                cleaned = [str(v).strip() for v in val if str(v).strip()]
                if cleaned:
                    out[key] = cleaned
            elif isinstance(val, str) and val.strip():
                out[key] = [val.strip()]
            continue
        out[key] = str(val).strip()
    return out


def job_hint_active(hints: list[str] | None) -> bool:
    tokens = {(h or "").strip().lower() for h in (hints or []) if h and h.strip()}
    return HINT_JOB_CHARTER in tokens or HINT_JOB_CONFIRM in tokens


def apply_job_triage_policy(
    hints: list[str],
    goals: list[str],
    job_candidate: dict[str, Any] | None,
    *,
    agent_id: str = "",
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Strip job charter hints for squad agents; keep solo policy intact."""
    hints = list(hints or [])
    goals = list(goals or [])
    candidate = dict(job_candidate or {})
    if not solo_agent_eligible_for_job_charter(agent_id):
        hints = [
            h for h in hints
            if (h or "").strip().lower()
            not in {HINT_JOB_CHARTER, HINT_JOB_CONFIRM}
        ]
        return goals, hints, {}
    if fleet_hint_active(hints):
        hints = [
            h for h in hints
            if (h or "").strip().lower()
            not in {HINT_JOB_CHARTER, HINT_JOB_CONFIRM}
        ]
        return goals, hints, {}
    if not job_hint_active(hints):
        return goals, hints, {}
    return goals, hints, normalize_job_candidate(candidate)


def job_active_tool_names() -> frozenset[str]:
    """Tools to pre-unlock when triage emitted job charter hints."""
    return _JOB_ACTIVE_TOOLS


def can_owner_apply_job_patch(
    *,
    session_key: str,
    dispatch_source: str,
    agent_id: str = "",
) -> tuple[bool, str]:
    """Owner-only guard — Home chat, not channel threads or orchestration wakes."""
    if not solo_agent_eligible_for_job_charter(agent_id):
        role = squad_role_for_agent(agent_id)
        if role == "lead":
            return (
                False,
                "BLOCKED: squad leads update their Job via "
                "squad(action='set_lead_job', owner_confirmed=true) after ask_user().",
            )
        if role == "member":
            return (
                False,
                "BLOCKED: squad members receive Job charters from the lead via "
                "squad(action='set_member_job', ...).",
            )
    effective_sk = (session_key or "").strip() or "websocket:main"
    if effective_sk != "websocket:main":
        from nls.skills.surface_send import is_surface_session_key

        if is_surface_session_key(effective_sk):
            detail = "not from channel threads"
        else:
            detail = f"session must be websocket:main, got {effective_sk!r}"
        return (
            False,
            f"BLOCKED: set_job is owner-only on Home chat — {detail}.",
        )
    src = (dispatch_source or "user").strip()
    if src.startswith("user:channel:") or src.startswith("channel:"):
        return (
            False,
            "BLOCKED: set_job cannot be applied from a channel dispatch — use Home chat.",
        )
    return True, ""


def job_charter_bootstrap_message(job_candidate: dict[str, Any] | None = None) -> str:
    lines = [
        "[JOB CHARTER — persistent role, not a one-shot task]",
        "The owner wants an ongoing Job (role charter), not just a task goal.",
        "1. Propose a concise charter (title, mission, persona, playbook, in/out of scope).",
        "2. Call ask_user() to confirm wording with the owner.",
        "3. After explicit approval, call set_job(owner_confirmed=true, ...) — never write "
        "job.json without confirmation.",
        "Job persists in job.json and shapes future behavior; task goals are ephemeral.",
    ]
    candidate = normalize_job_candidate(job_candidate)
    if candidate:
        lines.append(
            "Triage draft (refine before proposing):\n"
            + json.dumps(candidate, indent=2, ensure_ascii=False)
        )
    return "\n".join(lines)


def job_charter_confirm_message() -> str:
    return (
        "[JOB CHARTER CONFIRMATION]\n"
        "The owner approved your proposed Job charter. Call set_job(owner_confirmed=true, "
        "...fields...) now to persist job.json. Do not skip owner_confirmed."
    )


def resolve_job_candidate(
    job_candidate: dict[str, Any] | None,
    *,
    hints: list[str] | None = None,
    working_memory: Any | None = None,
) -> dict[str, Any]:
    """Prefer triage draft; fall back to WM Task.JobCandidate when job hints active."""
    resolved = normalize_job_candidate(job_candidate)
    if resolved:
        return resolved
    if not job_hint_active(hints):
        return {}
    from nls.runtime.job_trust import read_task_job_candidate

    return normalize_job_candidate(read_task_job_candidate(working_memory))


def job_loop_context_message(
    agent_id: str,
    hints: list[str] | None,
    job_candidate: dict[str, Any] | None = None,
    *,
    working_memory: Any | None = None,
) -> str | None:
    if not solo_agent_eligible_for_job_charter(agent_id):
        return None
    candidate = resolve_job_candidate(
        job_candidate,
        hints=hints,
        working_memory=working_memory,
    )
    tokens = {(h or "").strip().lower() for h in (hints or []) if h and h.strip()}
    if HINT_JOB_CONFIRM in tokens:
        if candidate:
            return (
                job_charter_confirm_message()
                + "\n\nApproved charter draft:\n"
                + json.dumps(candidate, indent=2, ensure_ascii=False)
            )
        return job_charter_confirm_message()
    if HINT_JOB_CHARTER in tokens:
        return job_charter_bootstrap_message(candidate)
    return None


async def execute_set_job(
    *,
    agent_dir: Path,
    agent_id: str,
    args: dict[str, Any],
    session_key: str,
    dispatch_source: str,
) -> ToolResult:
    ok, msg = can_owner_apply_job_patch(
        session_key=session_key,
        dispatch_source=dispatch_source,
        agent_id=agent_id,
    )
    if not ok:
        return ToolResult(content=msg, is_error=True)
    if not bool(args.get("owner_confirmed", False)):
        return ToolResult(
            content=(
                "owner_confirmed=true required — use ask_user() first to confirm "
                "the charter with the owner."
            ),
            is_error=True,
        )
    from nls.runtime.job_trust import (
        clear_task_job_candidate_for_agent,
        job_fields_from_kwargs,
        patch_job_fields,
        read_task_job_candidate,
        runtime_working_memory,
        sync_runtime_job_trust_for_agent,
    )

    fields = job_fields_from_kwargs(**args)
    wm = runtime_working_memory(agent_id)
    stored = normalize_job_candidate(read_task_job_candidate(wm))
    if stored:
        merged = dict(stored)
        merged.update(fields)
        fields = merged
    if not fields:
        return ToolResult(content="No job fields to apply.", is_error=True)
    try:
        job = patch_job_fields(agent_dir, fields)
        sync_runtime_job_trust_for_agent(agent_id)
        clear_task_job_candidate_for_agent(agent_id)
        return ToolResult(content=json.dumps(job.to_dict(), indent=2))
    except Exception as exc:
        return ToolResult(content=str(exc), is_error=True)


def boost_job_charter_continuation(
    triage: Any,
    user_input: str,
    *,
    history: list[dict] | None = None,
    agent_id: str = "",
) -> None:
    """Add continuation:job_confirm when owner approves a proposed Job charter."""
    if not solo_agent_eligible_for_job_charter(agent_id):
        return
    ui = (user_input or "").strip()
    if not ui or not _OWNER_CONFIRM_RE.match(ui):
        return
    last_asst = ""
    if history:
        for turn in reversed(history[-10:]):
            if turn.get("role") == "assistant":
                last_asst = (turn.get("content") or "")[:2000]
                break
    if not last_asst or not _JOB_PROPOSAL_RE.search(last_asst):
        return
    hints = list(getattr(triage, "hints", None) or [])
    tokens = {(h or "").strip().lower() for h in hints if h and h.strip()}
    if HINT_JOB_CONFIRM in tokens:
        return
    hints.append(HINT_JOB_CONFIRM)
    triage.hints = hints
    goals = list(getattr(triage, "goals", None) or [])
    if not any("set_job" in (g or "").lower() for g in goals):
        goals.insert(0, "Apply approved Job via set_job(owner_confirmed=true)")
    triage.goals = goals[:5]
    if not (getattr(triage, "intent", "") or "").startswith("TASK"):
        triage.intent = "TASK_THINK"
        triage.thinking = True
