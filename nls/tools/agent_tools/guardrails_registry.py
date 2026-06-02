"""Shared tool guardrails registry — contract errors visible to delegates.

Mirrors ``AgentReadIndex``: append-only JSONL under the agent data dir.
Orchestrator records structured-tool validation / contract failures; new
delegates receive recent entries in SubCryptex task instructions.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 40
_SPAWN_LIMIT = 8


@dataclass
class GuardrailEntry:
    tool_name: str
    rule_id: str
    message: str
    scope: str = "project"
    delegate_number: int = 0
    ts: float = 0.0

    def format_line(self) -> str:
        who = f"delegate #{self.delegate_number}" if self.delegate_number else "orchestrator"
        return f"- [{self.tool_name}/{self.rule_id}] ({who}) {self.message[:220]}"


class AgentGuardrailsRegistry:
    """Agent-scoped guardrails shared across orchestrator and delegates."""

    def __init__(self, agent_dir: Path) -> None:
        self._agent_dir = agent_dir
        self._path = agent_dir / "guardrails_registry.jsonl"
        self._lock = threading.Lock()
        self._entries: list[GuardrailEntry] = []
        try:
            self._agent_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    self._entries.append(
                        GuardrailEntry(
                            tool_name=str(d.get("tool_name", "")),
                            rule_id=str(d.get("rule_id", "")),
                            message=str(d.get("message", "")),
                            scope=str(d.get("scope", "project")),
                            delegate_number=int(d.get("delegate_number", 0)),
                            ts=float(d.get("ts", 0)),
                        ),
                    )
        except Exception:
            logger.debug("GuardrailsRegistry load failed", exc_info=True)
        if len(self._entries) > _MAX_ENTRIES:
            self._entries = self._entries[-_MAX_ENTRIES:]

    def record(
        self,
        *,
        tool_name: str,
        rule_id: str,
        message: str,
        scope: str = "project",
        delegate_number: int = 0,
    ) -> None:
        if not tool_name or not rule_id or not message.strip():
            return
        entry = GuardrailEntry(
            tool_name=tool_name,
            rule_id=rule_id,
            message=message.strip()[:500],
            scope=scope,
            delegate_number=delegate_number,
            ts=time.time(),
        )
        with self._lock:
            for existing in self._entries[-12:]:
                if (
                    existing.tool_name == entry.tool_name
                    and existing.rule_id == entry.rule_id
                    and existing.message[:120] == entry.message[:120]
                ):
                    return
            self._entries.append(entry)
            if len(self._entries) > _MAX_ENTRIES:
                self._entries = self._entries[-_MAX_ENTRIES:]
            try:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "tool_name": entry.tool_name,
                                "rule_id": entry.rule_id,
                                "message": entry.message,
                                "scope": entry.scope,
                                "delegate_number": entry.delegate_number,
                                "ts": entry.ts,
                            },
                            ensure_ascii=False,
                        )
                        + "\n",
                    )
            except Exception:
                logger.debug("GuardrailsRegistry append failed", exc_info=True)

    def recent_lines(self, *, limit: int = _SPAWN_LIMIT) -> list[str]:
        with self._lock:
            tail = self._entries[-limit:]
        return [e.format_line() for e in reversed(tail)]


def format_guardrails_block(lines: list[str]) -> str:
    if not lines:
        return ""
    return (
        "SHARED GUARDRAILS (orchestrator + prior delegates — obey these):\n"
        + "\n".join(lines)
    )


def sync_guardrails_to_cryptex(
    cryptex: Any,
    registry: AgentGuardrailsRegistry | None,
) -> None:
    """Mirror registry tail into orchestrator instructions ring."""
    if cryptex is None or registry is None:
        return
    lines = registry.recent_lines()
    body = format_guardrails_block(lines)
    if not body:
        return
    try:
        from nls.brain.cryptex import RING_INSTRUCTIONS
        from nls.brain.cryptex_tool_absorption import _upsert_slot

        _upsert_slot(
            cryptex,
            RING_INSTRUCTIONS,
            "shared_guardrails",
            body[:2500],
            salience=0.92,
            source="guardrails_registry",
        )
    except Exception:
        logger.debug("sync_guardrails_to_cryptex failed", exc_info=True)


def inject_guardrails_into_sub_cryptex(
    sub_cryptex: Any,
    registry: AgentGuardrailsRegistry | None,
) -> None:
    """Upsert recent guardrails into delegate task instructions."""
    if registry is None or sub_cryptex is None:
        return
    body = format_guardrails_block(registry.recent_lines())
    if not body:
        return
    try:
        from nls.brain.sub_cryptex import SUB_RING_TASK, _POS_INSTRUCTIONS

        sub_cryptex._rings[SUB_RING_TASK].upsert_slot(
            domain="shared_guardrails",
            content=body[:2500],
            slot_type="instruction",
            salience=0.98,
            source="guardrails_registry",
            position=_POS_INSTRUCTIONS,
        )
    except Exception:
        logger.debug("inject_guardrails_into_sub_cryptex failed", exc_info=True)


def inject_guardrails_into_cryptex(
    cryptex: Any,
    registry: AgentGuardrailsRegistry | None,
) -> None:
    """Load registry into orchestrator Cryptex (loop start / spawn prep)."""
    sync_guardrails_to_cryptex(cryptex, registry)


def record_tool_contract_guardrail(
    registry: AgentGuardrailsRegistry | None,
    *,
    tool_name: str,
    content: str,
    delegate_number: int = 0,
    cryptex: Any | None = None,
) -> None:
    """Parse contract error text, append to registry, sync Cryptex slot."""
    if registry is None:
        return
    from nls.agentic.tool_result_semantics import (
        contract_error_rule_id,
        is_tool_contract_error,
    )
    from nls.tools.agent_tools.base import ToolResult

    pseudo = ToolResult(content=content or "", is_error=True)
    if not is_tool_contract_error(tool_name, pseudo):
        return
    rule_id = contract_error_rule_id(tool_name, content)
    first_line = (content or "").strip().split("\n", 1)[0][:240]
    registry.record(
        tool_name=tool_name,
        rule_id=rule_id,
        message=first_line,
        delegate_number=delegate_number,
    )
    if cryptex is not None:
        sync_guardrails_to_cryptex(cryptex, registry)
