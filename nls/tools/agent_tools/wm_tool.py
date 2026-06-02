"""Working Memory (Cryptex) navigation tool.

Gives the agent explicit control over its cryptex context engine:
scan ring contents, rotate positions, borrow slots across projects,
search across all rings, and view the cryptex state.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)


class WMTool:
    """Full-access WM navigation tool for the orchestrator."""

    @property
    def name(self) -> str:
        return "wm"

    @property
    def description(self) -> str:
        return (
            "Navigate your working memory (cryptex context engine). "
            "Scan ring contents, rotate project/domain positions, "
            "borrow slots across projects, search across all memory, "
            "or get a snapshot of the full cryptex state."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["scan", "rotate", "borrow", "search", "positions", "snapshot"],
                    "description": (
                        "scan: read contents of a ring/position. "
                        "rotate: switch a ring's active position. "
                        "borrow: pull a slot from another position. "
                        "search: keyword search across all rings. "
                        "positions: list available positions for a ring. "
                        "snapshot: overview of entire cryptex state."
                    ),
                },
                "ring": {
                    "type": "string",
                    "description": (
                        "Ring to operate on. Project rings: orchestration, "
                        "instructions, project_facts, credentials, tactical_goals. "
                        "Domain rings: skills, tools_mcp, channels."
                    ),
                },
                "position": {
                    "type": "string",
                    "description": (
                        "Position ID (project name or domain area). "
                        "Use 'all' with scan to see all positions. "
                        "Use 'active' or omit for the current position."
                    ),
                },
                "to": {
                    "type": "string",
                    "description": "Target position for rotate action.",
                },
                "from_position": {
                    "type": "string",
                    "description": "Source position for borrow action.",
                },
                "domain": {
                    "type": "string",
                    "description": "Domain filter for borrow action (e.g. 'Project.Credential.github').",
                },
                "query": {
                    "type": "string",
                    "description": "Search query for search action.",
                },
            },
        }

    def __init__(self, cryptex: Any) -> None:
        self._cryptex = cryptex

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "")
        try:
            if action == "scan":
                return self._scan(params)
            elif action == "rotate":
                return self._rotate(params)
            elif action == "borrow":
                return self._borrow(params)
            elif action == "search":
                return self._search(params)
            elif action == "positions":
                return self._positions(params)
            elif action == "snapshot":
                return self._snapshot()
            else:
                return ToolResult(
                    content=f"Unknown action: {action}. Use: scan, rotate, borrow, search, positions, snapshot.",
                    is_error=True,
                )
        except Exception as exc:
            logger.warning("WM tool error: %s", exc, exc_info=True)
            return ToolResult(content=f"WM tool error: {exc}", is_error=True)

    def _scan(self, params: dict[str, Any]) -> ToolResult:
        ring_id = params.get("ring", "")
        position = params.get("position", "")

        if not ring_id:
            return ToolResult(
                content="Error: 'ring' parameter required for scan.",
                is_error=True,
            )

        ring = self._cryptex.get_ring(ring_id)
        if ring is None:
            available = ", ".join(r.spec.ring_id for r in self._cryptex._rings.values())
            return ToolResult(
                content=f"Unknown ring: {ring_id}. Available: {available}",
                is_error=True,
            )

        if position == "all":
            lines = [f"Ring: {ring.spec.display_name} — all positions"]
            for pos_id, slots in ring.positions.items():
                marker = " (active)" if pos_id == ring.active_position else ""
                lines.append(f"\n  Position: {pos_id}{marker} ({len(slots)} slots)")
                for s in slots:
                    domain_tag = f" [{s.domain}]" if s.domain else ""
                    lines.append(f"    - {s.slot_type}: {s.content[:120]}{domain_tag}")
            return ToolResult(content="\n".join(lines))

        pos = position if position and position != "active" else ring.active_position
        slots = ring.positions.get(pos, [])
        lines = [f"Ring: {ring.spec.display_name} — position: {pos} ({len(slots)} slots)"]
        for s in slots:
            domain_tag = f" [{s.domain}]" if s.domain else ""
            meta = ""
            full_instr = s.metadata.get("full_instructions")
            if full_instr:
                meta = f" (has full instructions: {len(full_instr)} chars)"
            lines.append(f"  - {s.slot_type}: {s.content[:200]}{domain_tag}{meta}")
        if not slots:
            lines.append("  (empty)")
        return ToolResult(content="\n".join(lines))

    def _rotate(self, params: dict[str, Any]) -> ToolResult:
        ring_id = params.get("ring", "")
        to_pos = params.get("to", "")

        if not ring_id or not to_pos:
            return ToolResult(
                content="Error: 'ring' and 'to' parameters required for rotate.",
                is_error=True,
            )

        ring = self._cryptex.get_ring(ring_id)
        if ring is None:
            return ToolResult(
                content=f"Unknown ring: {ring_id}",
                is_error=True,
            )

        old = ring.rotate(to_pos)

        # If it's a project ring, also rotate all other project rings
        if ring.spec.category == "project":
            self._cryptex._active_project = to_pos
            for r in self._cryptex._rings.values():
                if r.spec.category == "project" and r.spec.ring_id != ring_id:
                    r.rotate(to_pos)

        # If it's a domain ring, also rotate all other domain rings
        if ring.spec.category == "domain":
            self._cryptex._active_domain = to_pos
            for r in self._cryptex._rings.values():
                if r.spec.category == "domain" and r.spec.ring_id != ring_id:
                    r.rotate(to_pos)

        count = len(ring.positions.get(to_pos, []))
        return ToolResult(
            content=f"Rotated {ring.spec.display_name} from '{old}' to '{to_pos}' ({count} slots in new position).",
        )

    def _borrow(self, params: dict[str, Any]) -> ToolResult:
        ring_id = params.get("ring", "")
        from_pos = params.get("from_position", "")
        domain = params.get("domain", "")

        if not ring_id or not from_pos:
            return ToolResult(
                content="Error: 'ring' and 'from_position' required for borrow.",
                is_error=True,
            )

        ring = self._cryptex.get_ring(ring_id)
        if ring is None:
            return ToolResult(content=f"Unknown ring: {ring_id}", is_error=True)

        source_slots = ring.positions.get(from_pos, [])
        if not source_slots:
            return ToolResult(
                content=f"No slots in position '{from_pos}' of ring '{ring_id}'.",
            )

        if domain:
            matches = [s for s in source_slots if s.domain == domain]
        else:
            matches = source_slots

        if not matches:
            return ToolResult(
                content=f"No matching slots found in {ring_id}:{from_pos} for domain '{domain}'.",
            )

        active_pos = ring.active_position
        borrowed = 0
        for slot in matches:
            existing = ring.positions.get(active_pos, [])
            if not any(s.domain == slot.domain and s.slot_type == slot.slot_type for s in existing):
                from nls.brain.working_memory import WMSlot
                copied = WMSlot.from_dict(slot.to_dict())
                ring.add_slot(copied, position=active_pos)
                borrowed += 1

        return ToolResult(
            content=f"Borrowed {borrowed} slot(s) from '{from_pos}' into active position '{active_pos}'.",
        )

    def _search(self, params: dict[str, Any]) -> ToolResult:
        query = params.get("query", "")
        if not query:
            return ToolResult(content="Error: 'query' required for search.", is_error=True)

        results = self._cryptex.search_all_rings(query, max_results=10)
        if not results:
            return ToolResult(content=f"No results found for '{query}'.")

        lines = [f"Search results for '{query}' ({len(results)} found):"]
        for r in results:
            slot = r["slot"]
            lines.append(
                f"  [{r['ring_name']}:{r['position']}] "
                f"{slot.slot_type}: {slot.content[:120]} "
                f"(score={r['score']:.2f})"
            )
        return ToolResult(content="\n".join(lines))

    def _positions(self, params: dict[str, Any]) -> ToolResult:
        ring_id = params.get("ring", "")
        if not ring_id:
            lines = ["All rings and their positions:"]
            for ring in self._cryptex._rings.values():
                pos_list = ring.position_ids
                active = ring.active_position
                lines.append(
                    f"  {ring.spec.display_name} ({ring.spec.ring_id}): "
                    f"active={active}, positions=[{', '.join(pos_list) or 'none'}]"
                )
            return ToolResult(content="\n".join(lines))

        ring = self._cryptex.get_ring(ring_id)
        if ring is None:
            return ToolResult(content=f"Unknown ring: {ring_id}", is_error=True)

        lines = [f"Positions for {ring.spec.display_name}:"]
        for pos_id in ring.position_ids:
            count = len(ring.positions.get(pos_id, []))
            marker = " (active)" if pos_id == ring.active_position else ""
            lines.append(f"  - {pos_id}: {count} slots{marker}")
        if not ring.position_ids:
            lines.append("  (no positions)")
        return ToolResult(content="\n".join(lines))

    def _snapshot(self) -> ToolResult:
        return ToolResult(content=self._cryptex.get_cryptex_snapshot())


class WMReadOnlyTool:
    """Read-only WM tool for delegates (scan, search, snapshot only)."""

    @property
    def name(self) -> str:
        return "wm"

    @property
    def description(self) -> str:
        return (
            "Read your working memory context. "
            "Scan ring contents, search across memory, "
            "or get a snapshot of available context."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["scan", "search", "snapshot"],
                    "description": (
                        "scan: read contents of a ring. "
                        "search: keyword search across all rings. "
                        "snapshot: overview of cryptex state."
                    ),
                },
                "ring": {
                    "type": "string",
                    "description": "Ring to scan.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query.",
                },
            },
        }

    def __init__(self, cryptex: Any) -> None:
        self._full = WMTool(cryptex)

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "")
        if action not in ("scan", "search", "snapshot"):
            return ToolResult(
                content=f"Delegates can only use: scan, search, snapshot (not '{action}').",
                is_error=True,
            )
        return await self._full.execute(params, signal)
