"""Supersede-by-intent: collapse stale tool results in chat history.

Only rewrites ``role: tool`` message bodies; assistant ``tool_calls`` stay
visible for API pairing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from nls.agentic.compactor import CompactionAnchor
from nls.agentic.tool_result_semantics import effective_tool_error
from nls.agentic.types import AgentMode, LoopState
from nls.agentic.wake_coordination import is_completion_review_source
from nls.tools.agent_tools.base import ToolResult
from nls.tools.agent_tools.file_ledger import normalize_ledger_path
from nls.tools.agent_tools.tool_path_args import normalize_tool_path_arg

logger = logging.getLogger(__name__)

_REPRESENTATIVE_FAILURE_CHARS = 200
_STUB_MAX_CHARS = 320


class SupersessionPolicy(str, Enum):
    DISABLED = "disabled"
    DELEGATE_AGGRESSIVE = "delegate_aggressive"
    ORCHESTRATOR_CONSERVATIVE = "orchestrator_conservative"
    COMPLETION_REVIEW_FROZEN = "completion_review_frozen"


@dataclass
class ToolTurnRecord:
    """One assistant tool_call paired with its tool result."""

    msg_index: int
    tool_index: int
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    intent_key: str
    is_error: bool
    content: str


@dataclass
class SupersessionStats:
    stubs_applied: int = 0
    chars_saved: int = 0
    cache_refs: int = 0


def resolve_supersession_policy(
    *,
    enabled: bool,
    is_delegate_loop: bool,
    dispatch_source: str = "",
    has_pending_completion_reviews: bool = False,
    active_mode: AgentMode | None = None,
    coordinator_mode: bool = False,
) -> SupersessionPolicy:
    if not enabled:
        return SupersessionPolicy.DISABLED
    # Delegates always get aggressive supersession — never inherit EM review freeze.
    if is_delegate_loop:
        return SupersessionPolicy.DELEGATE_AGGRESSIVE
    src = (dispatch_source or "").strip()
    if is_completion_review_source(src) or has_pending_completion_reviews:
        return SupersessionPolicy.COMPLETION_REVIEW_FROZEN
    if coordinator_mode or active_mode == AgentMode.EVALUATING:
        return SupersessionPolicy.ORCHESTRATOR_CONSERVATIVE
    return SupersessionPolicy.ORCHESTRATOR_CONSERVATIVE


def resolve_deliverable_paths(plan_tool: Any | None) -> frozenset[str]:
    """Paths EM should not stub during completion review / evaluating."""
    paths: set[str] = set()
    if plan_tool is None:
        return frozenset()
    store = getattr(plan_tool, "_store", None) or getattr(plan_tool, "get_store", lambda: None)()
    if store is None:
        return frozenset()
    try:
        if hasattr(store, "find_active"):
            plan = store.find_active()
        else:
            plan = None
    except Exception:
        plan = None
    if plan is None:
        return frozenset()
    for step in getattr(plan, "steps", []) or []:
        for raw in getattr(step, "owned_paths", None) or []:
            norm = normalize_ledger_path(str(raw))
            if norm:
                paths.add(norm)
        for raw in getattr(step, "output_files", None) or []:
            norm = normalize_ledger_path(str(raw))
            if norm:
                paths.add(norm)
    return frozenset(paths)


def sync_open_blockers(
    anchor: CompactionAnchor,
    *,
    state: LoopState | None,
    team_manager: Any | None,
) -> None:
    """Keep anchor.open_blockers aligned with active orchestration blockers."""
    active: list[str] = []
    if team_manager is not None:
        try:
            if team_manager.has_pending_completion_reviews():
                active.append(
                    "Completion review pending — spot-check deliverables, "
                    "then team(intervene, decision='approve')"
                )
        except Exception:
            pass
    if state is not None and state.has_pending_escalation:
        tid = getattr(state, "pending_escalation_team_id", "") or "?"
        active.append(f"Delegate escalation pending (team {tid})")
    if state is not None and state.last_turn_had_errors:
        preview = (state.last_error_preview or "")[:160]
        if preview and any(
            k in preview for k in ("MUST READ FIRST", "STALE FILE", "FILE LOCKED")
        ):
            active.append(preview)
    # Drop resolved blockers; merge active ones
    keep = [
        b for b in anchor.open_blockers
        if b not in active
        and "Completion review pending" not in b
        and "Delegate escalation pending" not in b
        and "MUST READ FIRST" not in b
        and "STALE FILE" not in b
    ]
    anchor.open_blockers = keep
    for item in active:
        if item not in anchor.open_blockers:
            anchor.open_blockers.append(item)


def register_tool_msg_outcome(
    state: LoopState,
    msg_index: int,
    tool_name: str,
    result: ToolResult,
    *,
    args: dict[str, Any] | None = None,
) -> None:
    """Record effective error bit for supersession (bash soft-fail aware)."""
    bash_args = args if tool_name == "bash" else None
    state.tool_msg_is_error[msg_index] = effective_tool_error(
        tool_name, result, args=bash_args,
    )


def _normalize_bash_command(cmd: str) -> str:
    if not cmd:
        return "bash:"
    s = cmd.strip()
    s = re.sub(r'"[^"]*"', '"<str>"', s)
    s = re.sub(r"'[^']*'", "'<str>'", s)
    s = re.sub(r"\b\d+\b", "<n>", s)
    s = re.sub(r"(?:/[\\w.-]+)+", "<path>", s)
    s = re.sub(r"[A-Za-z]:\\(?:[\\w.-]+\\)+", "<path>", s)
    s = re.sub(r"\s+", " ", s)[:120]
    return f"bash:{s}"


def _error_class(content: str) -> str:
    if not content:
        return "empty"
    head = content[:120].lower()
    for pat in (
        "modulenotfounderror",
        "no module named",
        "no such file",
        "cannot find path",
        "command not found",
        "syntaxerror",
        "permission denied",
    ):
        if pat in head:
            return pat
    return head[:40]


def build_intent_key(tool_name: str, args: dict[str, Any], *, cwd: str = "") -> str:
    if tool_name == "bash":
        return _normalize_bash_command(str(args.get("command", "")))
    if tool_name in ("read", "write", "edit", "delete_file", "move_file"):
        path, _ = normalize_tool_path_arg(args.get("path", ""), cwd=cwd)
        if tool_name == "read":
            offset = args.get("offset", 1)
            limit = args.get("limit")
            max_chars = args.get("max_chars")
            if max_chars is not None:
                return f"read:{path}:max_chars={max_chars}"
            return f"read:{path}:o{offset}:l{limit or 0}"
        return f"file:{path}"
    if tool_name in ("grep", "glob", "semantic_search"):
        pattern = args.get("pattern") or args.get("query") or ""
        scope = args.get("path") or args.get("target_directory") or "."
        path, _ = normalize_tool_path_arg(scope, cwd=cwd)
        return f"search:{tool_name}:{pattern}:{path}"
    if tool_name == "list_dir":
        path, _ = normalize_tool_path_arg(args.get("path", "."), cwd=cwd)
        depth = args.get("depth", 1)
        return f"list:{path}:d{depth}"
    if tool_name in ("plan", "team", "todo"):
        action = args.get("action", "")
        stable = (
            args.get("plan_id")
            or args.get("team_id")
            or args.get("todo_id")
            or args.get("step_id")
            or ""
        )
        return f"{tool_name}:{action}:{stable}"
    return f"tool:{tool_name}"


def _looks_like_error(content: str) -> bool:
    if not content:
        return False
    head = content[:200]
    return (
        head.startswith("Error")
        or "[FAIL" in head
        or "Traceback" in head
        or "STALE FILE" in head
        or "MUST READ FIRST" in head
    )


def _record_is_error(
    state: LoopState | None,
    msg_index: int,
    content: str,
) -> bool:
    if state is not None and msg_index in state.tool_msg_is_error:
        return state.tool_msg_is_error[msg_index]
    return _looks_like_error(content)


def _is_protected_read(record: ToolTurnRecord) -> bool:
    return record.tool_name == "read" and "max_chars=" in record.intent_key


def _path_from_read_intent(intent_key: str) -> str:
    if not intent_key.startswith("read:"):
        return ""
    body = intent_key[5:]
    return body.split(":o")[0].split(":max")[0]


def _pair_tool_turns(
    context: list[dict],
    *,
    start_index: int,
    cwd: str = "",
    state: LoopState | None = None,
) -> list[ToolTurnRecord]:
    records: list[ToolTurnRecord] = []
    tool_results: dict[str, tuple[int, dict]] = {}
    for i, msg in enumerate(context):
        if i < start_index:
            continue
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id:
                tool_results[tc_id] = (i, msg)

    for i, msg in enumerate(context):
        if i < start_index:
            continue
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue
        for ti, tc in enumerate(msg["tool_calls"]):
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tc_id = tc.get("id", "")
            tr = tool_results.get(tc_id)
            if tr is None:
                continue
            msg_index, tool_msg = tr
            content = tool_msg.get("content") or ""
            records.append(
                ToolTurnRecord(
                    msg_index=msg_index,
                    tool_index=ti,
                    tool_call_id=tc_id,
                    tool_name=name,
                    args=args,
                    intent_key=build_intent_key(name, args, cwd=cwd),
                    is_error=_record_is_error(state, msg_index, content),
                    content=content,
                ),
            )
    return records


def _make_stub(
    record: ToolTurnRecord,
    *,
    reason: str,
    count: int = 1,
    representative: str = "",
    cache_key: str = "",
) -> str:
    parts = [f"[superseded ×{count}] {record.tool_name} — {reason}"]
    if cache_key:
        parts.append(f"cache_key={cache_key}")
    if representative:
        parts.append(f"Last error: {representative[:_REPRESENTATIVE_FAILURE_CHARS]}")
    parts.append(
        "Assistant tool_call preserved above. "
        "Re-run the tool or use read(offset=…) / read(force=true) if you need full output.",
    )
    return "\n".join(parts)[: max(_STUB_MAX_CHARS, len(representative) + 120)]


def apply_supersession(
    context: list[dict],
    *,
    policy: SupersessionPolicy,
    state: LoopState | None = None,
    anchor: CompactionAnchor | None = None,
    start_index: int = 0,
    cwd: str = "",
    deliverable_paths: frozenset[str] | None = None,
    read_index: Any | None = None,
) -> SupersessionStats:
    """Rewrite stale tool message bodies in *context* (in place)."""
    stats = SupersessionStats()
    if policy in (SupersessionPolicy.DISABLED, SupersessionPolicy.COMPLETION_REVIEW_FROZEN):
        return stats

    records = _pair_tool_turns(
        context, start_index=start_index, cwd=cwd, state=state,
    )
    if not records:
        return stats

    by_intent: dict[str, list[ToolTurnRecord]] = {}
    for rec in records:
        by_intent.setdefault(rec.intent_key, []).append(rec)

    deliverable_paths = deliverable_paths or frozenset()

    for intent_key, group in by_intent.items():
        if len(group) < 2:
            continue

        latest = group[-1]
        older = group[:-1]
        if not older:
            continue

        if latest.tool_name == "read":
            path = _path_from_read_intent(intent_key)
            if _is_protected_read(latest):
                continue
            if (
                policy == SupersessionPolicy.ORCHESTRATOR_CONSERVATIVE
                and path in deliverable_paths
            ):
                continue

        latest_success = not latest.is_error
        latest_error_class = _error_class(latest.content) if latest.is_error else ""

        rep_failure = ""
        for rec in reversed(older):
            if rec.is_error:
                rep_failure = rec.content
                break

        cache_key = ""
        if read_index is not None and latest.tool_name == "read":
            path = _path_from_read_intent(intent_key)
            if path:
                entry = read_index.find_any_version(path)
                if entry:
                    cache_key = entry.cache_key

        for idx, rec in enumerate(older):
            if rec.msg_index >= len(context):
                continue
            tool_msg = context[rec.msg_index]
            if tool_msg.get("role") != "tool":
                continue
            original_len = len(tool_msg.get("content") or "")

            if policy == SupersessionPolicy.ORCHESTRATOR_CONSERVATIVE:
                if rec.tool_name not in ("list_dir", "read", "bash"):
                    continue
                path = _path_from_read_intent(rec.intent_key) if rec.tool_name == "read" else ""
                if rec.tool_name == "read" and path in deliverable_paths:
                    continue
                if rec.tool_name == "read" and rec is not older[-1]:
                    if latest.tool_name != "read":
                        continue
                if rec.tool_name == "bash" and not rec.is_error:
                    continue

            if rec.tool_name == "read" and _is_protected_read(rec):
                continue

            rec_cache = cache_key
            if rec.tool_name == "read" and read_index is not None and not rec_cache:
                p = _path_from_read_intent(rec.intent_key)
                if p:
                    ent = read_index.find_any_version(p)
                    if ent:
                        rec_cache = ent.cache_key

            if latest_success and rec.is_error:
                stub = _make_stub(
                    rec,
                    reason="resolved by later success",
                    count=len(older),
                    representative=rep_failure if idx == len(older) - 1 else "",
                    cache_key=rec_cache,
                )
            elif latest.is_error and rec.is_error:
                if latest_error_class == _error_class(rec.content):
                    if len(older) >= 2 and rec is older[-2]:
                        continue
                    stub = _make_stub(
                        rec, reason="same error class", count=len(older),
                        cache_key=rec_cache,
                    )
                else:
                    continue
            elif rec.tool_name == "read" and latest.tool_name == "read":
                stub = _make_stub(
                    rec, reason="superseded by newer read", count=len(older),
                    cache_key=rec_cache,
                )
                if rec_cache:
                    stats.cache_refs += 1
            elif rec.tool_name == "bash" and latest.tool_name == "bash":
                if rec.is_error:
                    stub = _make_stub(
                        rec,
                        reason="superseded bash attempt",
                        count=len(older),
                        representative=rep_failure if idx == len(older) - 2 else "",
                        cache_key=rec_cache,
                    )
                else:
                    stub = _make_stub(
                        rec, reason="superseded bash run", count=len(older),
                    )
            elif rec.tool_name == "list_dir" and latest.tool_name == "list_dir":
                stub = _make_stub(rec, reason="superseded list_dir", count=len(older))
            else:
                continue

            if tool_msg.get("content", "").startswith("[superseded"):
                continue

            tool_msg["content"] = stub
            saved = max(0, original_len - len(stub))
            stats.stubs_applied += 1
            stats.chars_saved += saved

            if anchor is not None:
                summary = f"{rec.tool_name} {intent_key[:60]} → superseded"
                if summary not in anchor.superseded_attempts:
                    anchor.superseded_attempts.append(summary)

    if state is not None:
        state.supersession_stubs_applied += stats.stubs_applied
        state.supersession_tokens_saved += stats.chars_saved // 4

    if stats.stubs_applied:
        logger.info(
            "Context supersession policy=%s stubs=%d chars_saved=%d cache_refs=%d",
            policy.value,
            stats.stubs_applied,
            stats.chars_saved,
            stats.cache_refs,
        )
    return stats


def apply_supersession_with_cache_refs(
    context: list[dict],
    *,
    policy: SupersessionPolicy,
    read_index: Any | None,
    **kwargs: Any,
) -> SupersessionStats:
    """Supersession with read-index cache_key refs on read stubs."""
    return apply_supersession(
        context,
        policy=policy,
        read_index=read_index,
        **kwargs,
    )
