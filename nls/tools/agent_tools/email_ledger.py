"""Email ledger — per-agent log of all sent and received emails.

Every email sent via ``email_send`` and every email received via the
email-channel adapter is appended as a JSONL entry to:
  {agent_dir}/email_ledger.jsonl

Fields per entry:
  ts          — ISO-8601 UTC timestamp
  direction   — "sent" | "received"
  from_addr   — sender address
  to          — primary recipient(s)
  cc          — CC list (may be empty)
  bcc         — BCC list (may be empty, omitted on received)
  subject     — email subject
  body_preview — first 300 chars of body
  message_id  — Message-ID header if available
  thread_id   — In-Reply-To / References if available
  status      — "ok" | "failed" (sent only)

The ``EmailHistoryTool`` lets the agent query this ledger:
  email_history()                   — 20 most recent entries (sent + received)
  email_history(direction="sent")   — only sent
  email_history(direction="received")
  email_history(query="gal@tal")    — filter by address/subject fragment
  email_history(limit=50)           — up to 50 entries
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)

_LEDGER_FILE = "email_ledger.jsonl"
_BODY_PREVIEW_CHARS = 300
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# EmailLedger
# ---------------------------------------------------------------------------

class EmailLedger:
    """Append-only ledger of sent/received emails for one agent."""

    def __init__(self, agent_dir: Path) -> None:
        self._path = agent_dir / _LEDGER_FILE

    def record_sent(
        self,
        *,
        from_addr: str,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        message_id: str = "",
        in_reply_to: str = "",
        status: str = "ok",
    ) -> None:
        entry: dict[str, Any] = {
            "ts": _now_iso(),
            "direction": "sent",
            "from": from_addr,
            "to": to,
            "subject": subject,
            "body_preview": body[:_BODY_PREVIEW_CHARS],
            "status": status,
        }
        if cc:
            entry["cc"] = cc
        if bcc:
            entry["bcc"] = bcc
        if message_id:
            entry["message_id"] = message_id
        if in_reply_to:
            entry["thread_id"] = in_reply_to
        self._append(entry)

    def record_received(
        self,
        *,
        from_addr: str,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        message_id: str = "",
        in_reply_to: str = "",
    ) -> None:
        entry: dict[str, Any] = {
            "ts": _now_iso(),
            "direction": "received",
            "from": from_addr,
            "to": to,
            "subject": subject,
            "body_preview": body[:_BODY_PREVIEW_CHARS],
        }
        if cc:
            entry["cc"] = cc
        if message_id:
            entry["message_id"] = message_id
        if in_reply_to:
            entry["thread_id"] = in_reply_to
        self._append(entry)

    def query(
        self,
        *,
        direction: str = "",
        query: str = "",
        limit: int = _DEFAULT_LIMIT,
    ) -> list[dict]:
        """Return ledger entries newest-first, with optional filters."""
        limit = min(max(1, limit), _MAX_LIMIT)
        if not self._path.exists():
            return []

        entries: list[dict] = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("email_ledger: read error: %s", exc)
            return []

        # newest-first
        entries.reverse()

        if direction:
            entries = [e for e in entries if e.get("direction") == direction]

        if query:
            q = query.lower()
            entries = [
                e for e in entries
                if (q in (e.get("from") or "").lower()
                    or q in (e.get("to") or "").lower()
                    or q in (e.get("cc") or "").lower()
                    or q in (e.get("subject") or "").lower()
                    or q in (e.get("body_preview") or "").lower())
            ]

        return entries[:limit]

    def _append(self, entry: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("email_ledger: append failed: %s", exc)


# ---------------------------------------------------------------------------
# EmailHistoryTool
# ---------------------------------------------------------------------------

class EmailHistoryTool:
    """Agent tool for querying the email ledger."""

    def __init__(self, agent_id: str, ledger: EmailLedger) -> None:
        self._agent_id = agent_id
        self._ledger = ledger

    @property
    def name(self) -> str:
        return "email_history"

    @property
    def description(self) -> str:
        return (
            "Query the email ledger — a log of all emails sent and received by this agent. "
            "Use to verify a sent email, find a prior thread, or audit email activity."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["sent", "received"],
                    "description": "Filter to 'sent' or 'received'. Omit for both.",
                },
                "query": {
                    "type": "string",
                    "description": "Filter by address, subject, or body fragment.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max entries to return (default {_DEFAULT_LIMIT}, max {_MAX_LIMIT}).",
                },
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        direction = (params.get("direction") or "").strip()
        query = (params.get("query") or "").strip()
        limit = int(params.get("limit") or _DEFAULT_LIMIT)

        entries = self._ledger.query(direction=direction, query=query, limit=limit)
        if not entries:
            msg = "No email history"
            if direction:
                msg += f" ({direction})"
            if query:
                msg += f" matching '{query}'"
            return ToolResult(content=msg + ".")

        lines = [f"{len(entries)} email(s):"]
        for e in entries:
            ts = e.get("ts", "?")[:16].replace("T", " ")
            direction_icon = "→" if e.get("direction") == "sent" else "←"
            frm = e.get("from", "")
            to = e.get("to", "")
            subj = e.get("subject", "(no subject)")
            cc_part = f" cc:{e['cc']}" if e.get("cc") else ""
            status_part = f" [{e['status']}]" if e.get("direction") == "sent" and e.get("status") != "ok" else ""
            preview = (e.get("body_preview") or "")[:100]
            lines.append(f"  {ts} {direction_icon} {frm} → {to}{cc_part} | {subj}{status_part}")
            if preview:
                lines.append(f"    {preview}")

        return ToolResult(content="\n".join(lines))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_email_ledger(agent_id: str) -> "EmailLedger | None":
    """Return an EmailLedger for agent_id by resolving the agents_dir from app state.

    Returns ``None`` if the agent manager is not yet available.
    """
    try:
        from server.main import app
        am = getattr(app.state, "agent_manager", None)
        if am is not None:
            return EmailLedger(am.agents_dir / agent_id)
    except Exception:
        pass
    return None
