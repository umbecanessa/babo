"""Orchestration Context — scoped orchestration for multi-project support.

An OrchestrationContext wraps a (TeamManager, DelegateManager) pair
scoped to a specific project/task.  The OrchestrationRegistry manages
multiple concurrent contexts per agent, enabling scenarios like:

  - Primary context: WebSocket-initiated project orchestration
  - Secondary context: Channel-initiated task from WhatsApp
  - Background context: Scheduled autonomous project work

Each context has its own teams, delegates, and event routing.
The "default" context (id="primary") is backward-compatible with
the existing singleton TeamManager/DelegateManager.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationContext:
    """A single orchestration scope — one project/task being managed."""
    context_id: str = "primary"
    name: str = ""
    source: str = ""             # "ws", "whatsapp", "scheduler", etc.
    project_id: str = ""         # cryptex project position this maps to
    team_manager: Any | None = None
    delegate_manager: Any | None = None
    created_at: float = field(default_factory=time.time)
    is_active: bool = True

    def has_active_work(self) -> bool:
        """Check if this context has running teams or delegates."""
        if self.team_manager is not None:
            try:
                if self.team_manager.has_active_orchestration():
                    return True
            except Exception:
                pass
        if self.delegate_manager is not None:
            try:
                if self.delegate_manager.has_active_delegates():
                    return True
            except Exception:
                pass
        return False

    def get_summary(self) -> str:
        """Compact summary of this context's state."""
        parts = [f"Context: {self.name or self.context_id} (source={self.source})"]
        if self.team_manager is not None:
            try:
                ctx = self.team_manager.get_orchestration_context(compact=True)
                if ctx:
                    parts.append(ctx)
            except Exception:
                pass
        return "\n".join(parts)


class OrchestrationRegistry:
    """Manages multiple orchestration contexts per agent.

    The primary context (id="primary") wraps the existing singleton
    TeamManager/DelegateManager for backward compatibility.  Additional
    contexts can be created for concurrent orchestration from different
    channels or scheduled tasks.
    """

    def __init__(self) -> None:
        self._contexts: dict[str, OrchestrationContext] = {}

    def register_primary(
        self,
        team_manager: Any | None = None,
        delegate_manager: Any | None = None,
        project_id: str = "general",
        cryptex: Any | None = None,
    ) -> OrchestrationContext:
        """Register the primary (backward-compat) context."""
        ctx = OrchestrationContext(
            context_id="primary",
            name="Primary",
            source="ws",
            project_id=project_id,
            team_manager=team_manager,
            delegate_manager=delegate_manager,
        )
        self._contexts["primary"] = ctx

        if cryptex is not None:
            try:
                cryptex.get_or_create_project(project_id)
            except Exception:
                pass

        return ctx

    def create_context(
        self,
        context_id: str,
        name: str = "",
        source: str = "",
        project_id: str = "",
        team_manager: Any | None = None,
        delegate_manager: Any | None = None,
        cryptex: Any | None = None,
    ) -> OrchestrationContext:
        """Create a new orchestration context.

        If *cryptex* is provided, also creates a project position on all
        project-rotating rings so the context has its own WM slice.
        """
        if context_id in self._contexts:
            logger.warning(
                "OrchestrationRegistry: context %s already exists, returning existing",
                context_id,
            )
            return self._contexts[context_id]

        _pid = project_id or context_id
        ctx = OrchestrationContext(
            context_id=context_id,
            name=name or context_id,
            source=source,
            project_id=_pid,
            team_manager=team_manager,
            delegate_manager=delegate_manager,
        )
        self._contexts[context_id] = ctx

        if cryptex is not None:
            try:
                cryptex.get_or_create_project(_pid)
            except Exception:
                pass

        logger.info(
            "OrchestrationRegistry: created context %s (source=%s, project=%s)",
            context_id, source, _pid,
        )
        return ctx

    def get(self, context_id: str) -> OrchestrationContext | None:
        return self._contexts.get(context_id)

    def get_primary(self) -> OrchestrationContext | None:
        return self._contexts.get("primary")

    def get_active_contexts(self) -> list[OrchestrationContext]:
        return [
            ctx for ctx in self._contexts.values()
            if ctx.is_active and ctx.has_active_work()
        ]

    def get_all_contexts(self) -> list[OrchestrationContext]:
        return list(self._contexts.values())

    def find_by_source(self, source: str) -> OrchestrationContext | None:
        """Find a context by its source channel."""
        for ctx in self._contexts.values():
            if ctx.source == source and ctx.is_active:
                return ctx
        return None

    def has_active_orchestration(self) -> bool:
        """True if any context has active work."""
        return any(ctx.has_active_work() for ctx in self._contexts.values())

    def get_orchestration_snapshot(self) -> str:
        """Compact summary of all active orchestration across contexts."""
        active = self.get_active_contexts()
        if not active:
            return "No active orchestration."

        parts: list[str] = [f"Active orchestration contexts: {len(active)}"]
        for ctx in active:
            parts.append(f"\n--- {ctx.name} (source={ctx.source}) ---")
            parts.append(ctx.get_summary())
        return "\n".join(parts)

    def deactivate(self, context_id: str) -> None:
        ctx = self._contexts.get(context_id)
        if ctx is not None:
            ctx.is_active = False
            logger.info(
                "OrchestrationRegistry: deactivated context %s", context_id,
            )

    def cleanup_stale(self, max_age_seconds: float = 7200) -> int:
        """Remove inactive contexts older than max_age_seconds."""
        now = time.time()
        stale = [
            cid for cid, ctx in self._contexts.items()
            if not ctx.is_active
            and (now - ctx.created_at) > max_age_seconds
            and cid != "primary"
        ]
        for cid in stale:
            del self._contexts[cid]
        if stale:
            logger.info(
                "OrchestrationRegistry: cleaned up %d stale contexts", len(stale),
            )
        return len(stale)

    def get_status(self) -> dict[str, Any]:
        return {
            "total_contexts": len(self._contexts),
            "active_contexts": len(self.get_active_contexts()),
            "contexts": {
                cid: {
                    "name": ctx.name,
                    "source": ctx.source,
                    "active": ctx.is_active,
                    "has_work": ctx.has_active_work(),
                    "created_at": ctx.created_at,
                }
                for cid, ctx in self._contexts.items()
            },
        }
