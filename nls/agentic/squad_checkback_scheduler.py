"""Periodic squad lead checkbacks — low-frequency coordination wakes."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from .squad_registry import Squad, SquadRegistry

logger = logging.getLogger(__name__)

DEFAULT_CHECKBACK_INTERVAL_SECONDS = 1800  # 30 minutes
DEFAULT_PROPOSAL_SLA_SECONDS = 4 * 3600  # 4 hours
MIN_CHECKBACK_INTERVAL_SECONDS = 300  # 5 minutes
TICK_INTERVAL_SECONDS = 60.0


class SquadCheckbackScheduler:
    """Background tick that wakes squad leads on interval or inbox SLA."""

    def __init__(
        self,
        registry: SquadRegistry,
        squad_manager: Any,
        *,
        has_dispatch_prefix: Callable[[str, str], bool] | None = None,
        tick_interval_seconds: float = TICK_INTERVAL_SECONDS,
    ) -> None:
        self._registry = registry
        self._manager = squad_manager
        self._has_dispatch_prefix = has_dispatch_prefix
        self._tick_interval = tick_interval_seconds
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="squad_checkback_scheduler",
        )
        logger.info(
            "SquadCheckbackScheduler started (tick=%.0fs)",
            self._tick_interval,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.warning("Squad checkback tick failed", exc_info=True)
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._tick_interval,
                )
            except asyncio.TimeoutError:
                pass

    def tick(self) -> int:
        """Evaluate all squads; return number of checkback wakes enqueued."""
        n = 0
        for squad in self._registry.list_squads():
            if self._maybe_wake(squad):
                n += 1
            n += self._maybe_wake_members(squad)
        return n

    def _maybe_wake_members(self, squad: Squad) -> int:
        if squad.paused or not getattr(squad, "member_checkback_enabled", True):
            return 0
        if self._manager is None:
            return 0
        now = time.time()
        wakes = 0
        last_map = getattr(squad, "member_last_checkback_at", None) or {}
        for member_id in squad.all_member_ids:
            if member_id == squad.lead_agent_id:
                continue
            if self._has_dispatch_prefix and self._has_dispatch_prefix(
                member_id, squad.id,
            ):
                continue
            try:
                from nls.runtime.job_background import (
                    background_wake_due,
                    job_allows_background_work,
                )
                from nls.runtime.job_trust import load_job

                agent_dir = self._manager._agent_dir(member_id)
                job = load_job(agent_dir)
                if not job_allows_background_work(job, agent_dir):
                    continue
                if not background_wake_due(job, now):
                    continue
            except Exception:
                continue
            detail = self._manager.build_member_checkback_detail(squad, member_id)
            if not (detail or "").strip():
                continue
            self._manager._wake_member(squad, member_id, "checkback", detail)
            last_map[member_id] = now
            wakes += 1
            logger.info(
                "Squad %s: member checkback wake for %s",
                squad.id, member_id,
            )
        if wakes:
            squad.member_last_checkback_at = last_map
            self._registry.save(squad)
        return wakes

    def _maybe_wake(self, squad: Squad) -> bool:
        if squad.paused or not squad.lead_agent_id:
            return False
        if not getattr(squad, "checkback_enabled", True):
            return False

        interval = max(
            MIN_CHECKBACK_INTERVAL_SECONDS,
            int(getattr(squad, "checkback_interval_seconds", 0) or 0)
            or DEFAULT_CHECKBACK_INTERVAL_SECONDS,
        )
        now = time.time()
        last = float(getattr(squad, "last_checkback_at", 0) or 0)
        due_interval = (now - last) >= interval

        pending = [i for i in squad.inbox if i.status == "proposed"]
        open_esc = [e for e in squad.escalations if e.status == "open"]
        sla = float(
            getattr(squad, "proposal_sla_seconds", 0) or 0
        ) or DEFAULT_PROPOSAL_SLA_SECONDS
        overdue_proposals = [
            i for i in pending
            if (now - (i.created_at or now)) >= sla
        ]

        has_pending = bool(pending)
        urgent = bool(overdue_proposals or open_esc)
        if not due_interval and not urgent and not has_pending:
            return False

        # Rate-limit all wakes (urgent escalations used to re-fire every tick).
        if last > 0 and (now - last) < MIN_CHECKBACK_INTERVAL_SECONDS:
            return False

        if self._has_dispatch_prefix and self._has_dispatch_prefix(
            squad.lead_agent_id, squad.id,
        ):
            return False

        detail = self._manager.build_checkback_detail(squad) if self._manager else ""
        self._manager._wake_lead(squad, "checkback", detail)
        squad.last_checkback_at = now
        self._registry.save(squad)
        logger.info(
            "Squad %s: checkback wake for lead %s (urgent=%s)",
            squad.id, squad.lead_agent_id, urgent,
        )
        return True
