"""Outbound notification ledger and lifecycle gates for channel tools.

Channel tools (WhatsApp, Telegram, email, communicate) send freely by default
(final_summary=false).  Set final_summary=true only for the final handoff;
the gate then checks plan/team/delegate state — language-agnostic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

OUTBOUND_TOOLS = frozenset({
    "whatsapp_send",
    "telegram_send",
    "email_send",
    "communicate",
})

FINAL_SUMMARY_SCHEMA_PROPERTY: dict[str, Any] = {
    "type": "boolean",
    "description": (
        "Default false — send progress, milestones, and updates freely. "
        "Set true ONLY for the final handoff when the entire task is finished. "
        "When using a plan: all steps done, plan(action='complete') called, "
        "no active teams, no running delegates."
    ),
}

_PROGRESS_MIN_INTERVAL_S = 45 * 60.0


def extract_outbound_text(args: dict[str, Any]) -> str:
    for key in ("text", "message", "body", "content", "html"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def is_final_summary_requested(args: dict[str, Any]) -> bool:
    val = args.get("final_summary", False)
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return bool(val)


def strip_outbound_control_args(args: dict[str, Any]) -> dict[str, Any]:
    """Remove gate-only params before the channel adapter runs."""
    return {k: v for k, v in args.items() if k != "final_summary"}


def tool_to_channel(tool_name: str) -> str:
    return {
        "whatsapp_send": "whatsapp",
        "telegram_send": "telegram",
        "email_send": "email",
        "communicate": "ui",
    }.get(tool_name, tool_name)


class OutboundNotifyLedger:
    """Persisted dedupe ledger per agent."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._entries = data.get("entries", data)
        except (json.JSONDecodeError, OSError):
            logger.warning("OutboundNotifyLedger: could not load %s", self._path)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"entries": self._entries}, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("OutboundNotifyLedger: save failed: %s", exc)

    @staticmethod
    def _key(kind: str, scope_id: str, channel: str) -> str:
        return f"{kind}:{scope_id}:{channel}"

    def should_skip(
        self,
        kind: str,
        scope_id: str,
        channel: str,
        status_hash: str,
    ) -> bool:
        entry = self._entries.get(self._key(kind, scope_id, channel))
        if not entry:
            return False
        if entry.get("status_hash") != status_hash:
            return False
        if kind == "progress":
            last = float(entry.get("sent_at", 0))
            if (time.time() - last) >= _PROGRESS_MIN_INTERVAL_S:
                return False
        return True

    def record(
        self,
        kind: str,
        scope_id: str,
        channel: str,
        status_hash: str,
    ) -> None:
        self._entries[self._key(kind, scope_id, channel)] = {
            "kind": kind,
            "scope_id": scope_id,
            "channel": channel,
            "status_hash": status_hash,
            "sent_at": time.time(),
        }
        self._save()


class OutboundNotifyGate:
    """Ledger dedupe + final_summary lifecycle gate."""

    def __init__(
        self,
        agent_dir: str | Path,
        *,
        team_manager: Any | None = None,
        plan_store: Any | None = None,
        delegate_manager: Any | None = None,
    ) -> None:
        self._team_manager = team_manager
        self._plan_store = plan_store
        self._delegate_manager = delegate_manager
        self._ledger = OutboundNotifyLedger(
            Path(agent_dir) / "outbound_notify.json",
        )

    def check(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name not in OUTBOUND_TOOLS:
            return None
        text = extract_outbound_text(args)
        if not text:
            return None

        final = is_final_summary_requested(args)
        kind = "final_summary" if final else "progress"
        channel = tool_to_channel(tool_name)
        scope_id, status_hash = self._resolve_scope()

        if final:
            allowed, reason = self._final_summary_allows()
            if not allowed:
                return (
                    f"Blocked: {reason} "
                    "Finish remaining work, call plan(action='complete') when "
                    "the plan is truly done, then retry with final_summary=true."
                )

        if self._ledger.should_skip(kind, scope_id, channel, status_hash):
            return (
                f"Skipped: unchanged status ({kind}, scope={scope_id}). "
                "Do not resend the same update."
            )
        return None

    def record(self, tool_name: str, args: dict[str, Any]) -> None:
        if tool_name not in OUTBOUND_TOOLS:
            return
        text = extract_outbound_text(args)
        if not text:
            return
        final = is_final_summary_requested(args)
        kind = "final_summary" if final else "progress"
        channel = tool_to_channel(tool_name)
        scope_id, status_hash = self._resolve_scope()
        self._ledger.record(kind, scope_id, channel, status_hash)

    def _resolve_scope(self) -> tuple[str, str]:
        tm = self._team_manager
        ps = self._plan_store

        team = self._active_team_for_notify()
        if team:
            return team.id, self._team_hash(team)
        if ps is not None:
            active = ps.find_active()
            if active:
                return active.id, self._plan_hash(active)
            done_roots = [
                p for p in ps.list_plans()
                if p.parent_id is None and p.status == "done"
            ]
            if done_roots:
                plan = max(done_roots, key=lambda p: p.updated_at)
                return plan.id, self._plan_hash(plan)
        return "orchestrator", "idle"

    def _active_team_for_notify(self) -> Any | None:
        tm = self._team_manager
        if tm is None:
            return None
        teams = tm.list_teams(include_terminal=True)
        non_terminal = [t for t in teams if not t.is_terminal]
        if non_terminal:
            return max(non_terminal, key=lambda t: t.created_at)
        terminal = [t for t in teams if t.is_terminal]
        if terminal:
            return max(terminal, key=lambda t: t.completed_at or t.created_at)
        return None

    @staticmethod
    def _team_hash(team: Any) -> str:
        parts = [
            team.id,
            team.status,
            team.plan_id,
            str(team.wave_index),
            str(getattr(team, "completion_reported", False)),
        ]
        for m in team.members:
            parts.extend([
                str(m.delegate_number),
                m.status,
                (m.result_summary or "")[:40],
            ])
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    @staticmethod
    def _plan_hash(plan: Any) -> str:
        parts = [str(plan.id), str(plan.status)]
        for s in plan.steps[:30]:
            parts.extend([str(getattr(s, "id", "")), str(getattr(s, "status", ""))])
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def _final_summary_allows(self) -> tuple[bool, str]:
        """State-based check — no message-language analysis."""
        ps = self._plan_store
        tm = self._team_manager
        dm = self._delegate_manager

        if ps is not None:
            try:
                active = ps.find_active()
            except Exception:
                active = None
            if active is not None:
                try:
                    pending = active.pending_steps()
                except Exception:
                    pending = []
                if pending:
                    labels = ", ".join(
                        (getattr(s, "label", s.id) or s.id)[:40]
                        for s in pending[:5]
                    )
                    return (
                        False,
                        f"plan {active.id} has {len(pending)} pending step(s): "
                        f"{labels}.",
                    )
                if active.status != "done":
                    return (
                        False,
                        f"plan {active.id} is not marked done "
                        f"(status={active.status}) — call plan(action='complete') "
                        "first.",
                    )

        if tm is not None:
            try:
                for t in tm.list_teams(include_terminal=False):
                    if not t.is_terminal:
                        return False, f"team {t.id} is still active."
            except Exception:
                pass

        if dm is not None:
            try:
                if dm.has_active_delegates():
                    return False, "delegates are still running."
            except Exception:
                pass

        return True, ""


def make_outbound_hooks(
    gate: OutboundNotifyGate | None,
) -> tuple[Callable[[str, dict], str | None] | None, Callable[[str, dict], None] | None]:
    if gate is None:
        return None, None

    def check(name: str, args: dict) -> str | None:
        return gate.check(name, args)

    def record(name: str, args: dict) -> None:
        gate.record(name, args)

    return check, record
