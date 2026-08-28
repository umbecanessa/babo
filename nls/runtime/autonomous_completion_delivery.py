"""Deliver autonomous / background task results to originating channels and Home.

Orchestration dispatches (job_background, todo-list idle runs) persist
``final_response`` in autonomous history but historically did not route it
back to Telegram/Discord/etc. or the Home chat.  This module closes that
gap architecturally — independent of whether the model remembered to call
``telegram_send`` or ``communicate()``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from nls.agentic.outbound_notify import OutboundNotifyLedger
from nls.runtime.dispatch_sources import is_orchestration_dispatch_source
from nls.skills.surface_send import is_surface_session_key

logger = logging.getLogger(__name__)

_TODO_ID_RE = re.compile(r"todo\s*\[([a-f0-9]{6,})\]", re.IGNORECASE)
_CHANNEL_HEADER_RE = re.compile(
    r"\[CHANNEL:\s*(\w+)\s*\|\s*reply_to:\s*([^\]|]+)",
    re.IGNORECASE,
)
_TELEGRAM_GROUP_ID_RE = re.compile(r"-100\d{5,}")
_SESSION_KEY_RE = re.compile(
    r"\b((?:telegram|discord|slack|whatsapp|email):(?:group|channel|dm|thread):[^\s\]|,]+)",
    re.IGNORECASE,
)
_NOOP_RE = re.compile(r"^\s*NOOP\b", re.IGNORECASE)

_MAX_CHANNEL_CHARS = 3500
_MAX_HOME_CHARS = 4000


def extract_todo_id(text: str) -> str | None:
    match = _TODO_ID_RE.search(text or "")
    return match.group(1) if match else None


def parse_session_key_from_text(text: str) -> str | None:
    """Best-effort channel session key from prompt / todo body."""
    blob = text or ""
    for match in _SESSION_KEY_RE.finditer(blob):
        key = match.group(1).strip()
        if is_surface_session_key(key):
            return key

    header = _CHANNEL_HEADER_RE.search(blob)
    if header:
        channel = header.group(1).strip().lower()
        target = header.group(2).strip()
        if channel == "telegram" and target.startswith("-100"):
            return f"telegram:group:{target}"
        if channel in ("discord", "slack", "whatsapp"):
            return f"{channel}:channel:{target}"

    group_match = _TELEGRAM_GROUP_ID_RE.search(blob)
    if group_match:
        return f"telegram:group:{group_match.group(0)}"

    return None


def _load_todo_item(agent_id: str, todo_id: str) -> Any | None:
    if not agent_id or not todo_id:
        return None
    try:
        from server.main import app

        skill_loader = getattr(app.state, "skill_loader", None)
        if skill_loader is None:
            return None
        todo_skill = skill_loader.skills.get("todo-list")
        if todo_skill is None or todo_skill.context is None:
            return None
        mgr = getattr(todo_skill.context, "adapter", None)
        if mgr is None:
            return None
        store = mgr.get_store(agent_id)
        return store.get(todo_id)
    except Exception:
        return None


def resolve_report_session_key(
    rt: Any,
    *,
    prompt: str,
    final_response: str,
    todo_id: str | None = None,
) -> str | None:
    """Resolve where an investigation completion should be reported."""
    from nls.runtime.todo_report_targets import resolve_explicit_report_session_key

    agent_id = getattr(rt, "agent_id", "") or ""
    todo_item = _load_todo_item(agent_id, todo_id) if todo_id else None

    return resolve_explicit_report_session_key(
        rt,
        todo_item=todo_item,
        prompt=prompt,
        final_response=final_response,
    )


def format_completion_summary(
    final_response: str,
    *,
    title: str = "",
    max_chars: int = _MAX_CHANNEL_CHARS,
) -> str:
    text = (final_response or "").strip()
    if not text:
        return ""
    if _NOOP_RE.match(text):
        return ""

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""

    # Prefer a concise executive summary when the model wrote structured notes.
    summary_lines: list[str] = []
    capture = False
    for ln in lines:
        lower = ln.lower()
        if any(
            token in lower
            for token in (
                "investigation complete",
                "root cause",
                "summary",
                "recommendation",
                "workaround",
                "fix:",
            )
        ):
            capture = True
        if capture:
            summary_lines.append(ln)
            if len("\n".join(summary_lines)) >= max_chars * 0.7:
                break

    body = "\n".join(summary_lines) if summary_lines else "\n".join(lines[:12])
    if title:
        body = f"✅ {title}\n\n{body}"
    if len(body) > max_chars:
        body = body[: max_chars - 3].rstrip() + "..."
    return body


def should_deliver_autonomous_completion(
    *,
    source: str,
    final_response: str,
    exit_reason: str,
    aborted: bool,
) -> bool:
    if aborted:
        return False
    if (exit_reason or "").strip() != "task_complete":
        return False
    if not (final_response or "").strip():
        return False
    if _NOOP_RE.match(final_response.strip()):
        return False
    if len(final_response.strip()) < 40:
        return False
    if not is_orchestration_dispatch_source(source):
        if (source or "").strip() != "todo-list":
            return False
    if source.startswith("scheduler") or source.startswith("check_back"):
        return False
    return True


def _status_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def deliver_autonomous_completion(
    rt: Any,
    *,
    source: str,
    prompt: str,
    final_response: str,
    exit_reason: str,
    aborted: bool,
    todo_id: str | None = None,
    connection_manager: Any | None = None,
) -> dict[str, Any]:
    """Post concise findings to the originating channel and/or Home chat."""
    outcome: dict[str, Any] = {
        "delivered": False,
        "channel": None,
        "home": False,
        "skipped_reason": "",
    }

    if not should_deliver_autonomous_completion(
        source=source,
        final_response=final_response,
        exit_reason=exit_reason,
        aborted=aborted,
    ):
        outcome["skipped_reason"] = "not_eligible"
        return outcome

    todo_id = todo_id or extract_todo_id(prompt)
    todo_item = _load_todo_item(getattr(rt, "agent_id", ""), todo_id) if todo_id else None
    title = getattr(todo_item, "title", "") if todo_item else ""

    summary = format_completion_summary(final_response, title=title)
    if not summary:
        outcome["skipped_reason"] = "empty_summary"
        return outcome

    from nls.runtime.session_routing import DeliveryIntent, get_session_router

    router = get_session_router(rt)
    ctx = router.routing_context_from_runtime(
        source=source,
        prompt=prompt,
        todo_id=todo_id or "",
        todo_title=title,
    )
    report_keys = router.resolve_report_keys(ctx=ctx, todo_item=todo_item, prompt=prompt)
    session_key = report_keys[0] if report_keys else None

    agent_dir = getattr(rt, "agent_dir", None)
    ledger = OutboundNotifyLedger(
        Path(agent_dir) / "outbound_notify.json"
        if agent_dir else Path("/dev/null")
    )
    scope_id = todo_id or _status_hash(summary)
    digest = _status_hash(summary)
    if ledger.should_skip("completion", scope_id, session_key or "home", digest):
        outcome["skipped_reason"] = "dedupe"
        return outcome

    deliver_out = await router.deliver(
        summary,
        DeliveryIntent.REPORT,
        ctx=ctx,
        todo_item=todo_item,
        connection_manager=connection_manager,
        user_facing=True,
        autonomous=True,
        source=source,
        include_default_home=True,
    )

    if deliver_out.delivered:
        ledger.record(
            "completion",
            scope_id,
            session_key or "home",
            digest,
        )
        outcome["delivered"] = True
        surface_keys = [
            k for k in deliver_out.targets
            if is_surface_session_key(k, rt)
        ]
        if surface_keys:
            outcome["channel"] = surface_keys[0]
        outcome["home"] = deliver_out.home
    else:
        outcome["skipped_reason"] = deliver_out.skipped_reason or "send_failed"

    return outcome
