"""delegate_ring tool — orchestrator interface for sub-agent ring memory.

Lets the orchestrator read, inspect, and manipulate a running sub-agent's
SubCryptex rings.  Changes appear on the sub-agent's next iteration
naturally via ``compose_context()``.
"""

from __future__ import annotations

import logging
from typing import Any

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)


class DelegateRingTool:
    """Per-agent delegate ring manipulation tool (AgentTool protocol)."""

    def __init__(self, delegate_manager: Any) -> None:
        self._dm = delegate_manager

    @property
    def name(self) -> str:
        return "delegate_ring"

    @property
    def description(self) -> str:
        return (
            "Read or manipulate a running sub-agent's memory rings. "
            "Each delegate has its own SubCryptex with rings: "
            "task, progress, knowledge, project_facts, credentials, "
            "tactical_goals, skills. Use this to steer delegates by "
            "injecting knowledge they are missing, boosting ring "
            "priority so they notice forgotten context, or reading "
            "their state for informed decision-making."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["delegate_number", "action"],
            "properties": {
                "delegate_number": {
                    "type": "integer",
                    "description": "The delegate number to target.",
                },
                "action": {
                    "type": "string",
                    "enum": ["read", "upsert", "boost_priority", "summary"],
                    "description": (
                        "read: show slots in a ring (task ring is readable). "
                        "upsert: push content into a mutable ring. "
                        "boost_priority: increase a ring's rendering priority. "
                        "summary: compact overview of all rings."
                    ),
                },
                "ring": {
                    "type": "string",
                    "enum": [
                        "task", "progress", "knowledge", "tactical_goals",
                        "project_facts", "credentials", "skills",
                    ],
                    "description": (
                        "Target ring (required for read/upsert/boost_priority). "
                        "The 'task' ring is read-only — can be read but not "
                        "upserted or boosted."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "Content to upsert (required for upsert).",
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Domain label for the upserted slot (default: "
                        "'orchestrator'). Used as the slot's key for updates."
                    ),
                },
                "salience": {
                    "type": "number",
                    "description": (
                        "For upsert: slot salience 0.0-1.0 (default 0.9). "
                        "Not used for other actions."
                    ),
                },
                "boost": {
                    "type": "number",
                    "description": (
                        "For boost_priority: how much to increase the ring's "
                        "priority (default 0.2, max useful ~0.5)."
                    ),
                },
            },
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        args = kwargs
        action = args.get("action", "")
        delegate_number = args.get("delegate_number")

        if delegate_number is None:
            return ToolResult(
                content="Error: 'delegate_number' is required.",
                is_error=True,
            )

        if self._dm is None:
            return ToolResult(
                content="Error: no DelegateManager available.",
                is_error=True,
            )

        sc = self._dm.get_delegate_cryptex(int(delegate_number))
        if sc is None:
            return ToolResult(
                content=(
                    f"Error: delegate #{delegate_number} has no SubCryptex "
                    f"(not running or not found)."
                ),
                is_error=True,
            )

        if action == "summary":
            text = sc.read_all_rings_summary()
            return ToolResult(
                content=f"Delegate #{delegate_number} SubCryptex:\n{text}",
            )

        ring_id = args.get("ring", "")
        if not ring_id and action != "summary":
            return ToolResult(
                content="Error: 'ring' is required for read/upsert/boost_priority.",
                is_error=True,
            )

        if action == "read":
            slots = sc.read_ring(ring_id)
            if not slots:
                return ToolResult(
                    content=f"Ring '{ring_id}' is empty for delegate #{delegate_number}.",
                )
            lines = [f"Delegate #{delegate_number} ring '{ring_id}' ({len(slots)} slots):"]
            for s in slots:
                lines.append(
                    f"  [{s['domain']}] (sal={s['salience']}) {s['content'][:200]}"
                )
            return ToolResult(content="\n".join(lines))

        elif action == "upsert":
            content = args.get("content", "")
            if not content:
                return ToolResult(
                    content="Error: 'content' is required for upsert.",
                    is_error=True,
                )
            domain = args.get("domain", "orchestrator")
            salience = float(args.get("salience", 0.9))
            ok = sc.upsert(ring_id, domain, content, salience)
            if ok:
                return ToolResult(
                    content=(
                        f"Upserted into delegate #{delegate_number} "
                        f"ring '{ring_id}' domain='{domain}'. "
                        f"Will appear on next iteration."
                    ),
                )
            return ToolResult(
                content=f"Error: could not upsert into ring '{ring_id}' (locked or not found).",
                is_error=True,
            )

        elif action == "boost_priority":
            boost_val = float(args.get("boost", 0.2))
            ok = sc.boost_priority(ring_id, boost_val)
            if ok:
                return ToolResult(
                    content=(
                        f"Boosted delegate #{delegate_number} ring "
                        f"'{ring_id}' priority by {boost_val}."
                    ),
                )
            return ToolResult(
                content=(
                    f"Error: could not boost ring '{ring_id}' "
                    f"(task ring is locked, or ring not found)."
                ),
                is_error=True,
            )

        return ToolResult(
            content=f"Error: unknown action '{action}'.",
            is_error=True,
        )
