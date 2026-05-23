"""WebSocket Connection Manager.

Tracks active WebSocket connections per agent so that background
tasks (DaydreamScheduler, Drive Engine, Sleep Scheduler) can push
events to connected clients in real-time.

Usage::

    # In chat.py (on connect)
    app.state.connection_manager.register(agent_id, websocket)

    # In chat.py (on disconnect)
    app.state.connection_manager.unregister(agent_id, websocket)

    # In daydream_scheduler.py (after a dream)
    await app.state.connection_manager.broadcast(agent_id, {
        "type": "daydream",
        "content": "I was thinking about...",
    })
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


# IR-11.1: Event priority classification
HIGH_PRIORITY_EVENTS = frozenset({
    "intention_triggered", "episode_start", "sleep_start", "sleep_complete",
    "safety_net_learned", "response_end", "error", "skill_installed",
    "delegate_batch_complete",
})

LOW_PRIORITY_EVENTS = frozenset({
    "network_switch", "regulation_applied", "wm_slot_update", "pe_update",
    "heartbeat_tick", "hormone_update", "resonance_update",
})


class ConnectionManager:
    """Manages active WebSocket connections grouped by agent_id.

    Thread/async safe -- all mutations go through asyncio-safe structures.

    Also supports relay clients for remote dashboard tunneling:
    broadcasts are forwarded through ChannelRelayClient instances
    so phone/browser clients connected via NestJS receive them.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._relay_clients: dict[str, Any] = {}  # agent_id -> ChannelRelayClient
        # IR-11.1: Low-priority event batch buffer
        self._event_buffer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._batch_interval: float = 5.0  # seconds

    def register(self, agent_id: str, websocket: WebSocket) -> None:
        """Register a new WebSocket connection for an agent."""
        self._connections[agent_id].add(websocket)
        logger.info(
            "WS registered: agent %s (total=%d)",
            agent_id, len(self._connections[agent_id]),
        )

    def unregister(self, agent_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket connection for an agent."""
        conns = self._connections.get(agent_id)
        if conns is not None:
            conns.discard(websocket)
            if not conns:
                del self._connections[agent_id]
        logger.info(
            "WS unregistered: agent %s (remaining=%d)",
            agent_id,
            len(self._connections.get(agent_id, set())),
        )

    def register_relay(self, agent_id: str, relay_client: Any) -> None:
        """Register a ChannelRelayClient for remote broadcast forwarding."""
        self._relay_clients[agent_id] = relay_client
        logger.info("Relay client registered for agent %s", agent_id)

    def unregister_relay(self, agent_id: str) -> None:
        """Remove a relay client for an agent."""
        self._relay_clients.pop(agent_id, None)

    def is_connected(self, agent_id: str) -> bool:
        """Check if any client is connected for this agent."""
        return bool(self._connections.get(agent_id))

    def connected_agents(self) -> list[str]:
        """Return list of agent_ids with active connections."""
        return [aid for aid, conns in self._connections.items() if conns]

    async def broadcast(self, agent_id: str, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients for an agent.

        Silently drops connections that have closed.
        """
        # Forward through relay for remote browser/phone clients first
        relay = self._relay_clients.get(agent_id)
        if relay is not None:
            try:
                asyncio.ensure_future(relay.broadcast_event(message))
            except Exception:
                pass

        conns = self._connections.get(agent_id)
        if not conns:
            return

        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            conns.discard(ws)
            logger.debug(
                "Removed dead WS for agent %s", agent_id,
            )

    async def broadcast_prioritized(
        self,
        agent_id: str,
        message: dict[str, Any],
    ) -> None:
        """Broadcast with event priority classification (IR-11.1).

        High-priority events are sent immediately. Low-priority events
        are buffered and flushed periodically via ``flush_buffer()``.
        Unknown events default to immediate broadcast.
        """
        event_type = message.get("type", "")
        if event_type in LOW_PRIORITY_EVENTS:
            self._event_buffer[agent_id].append(message)
            return
        await self.broadcast(agent_id, message)

    async def flush_buffer(self, agent_id: str) -> int:
        """Flush buffered low-priority events as a single batch message.

        Returns the number of events flushed.  Called by the heartbeat
        tick or consciousness scheduler at the configured interval.
        """
        events = self._event_buffer.pop(agent_id, [])
        if not events:
            return 0
        await self.broadcast(agent_id, {
            "type": "batch_update",
            "events": events,
            "count": len(events),
        })
        return len(events)

    async def flush_all_buffers(self) -> int:
        """Flush all buffered events for all agents."""
        total = 0
        for agent_id in list(self._event_buffer.keys()):
            total += await self.flush_buffer(agent_id)
        return total

    async def broadcast_all(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all connected agents."""
        for agent_id in list(self._connections.keys()):
            await self.broadcast(agent_id, message)

    @property
    def stats(self) -> dict[str, int]:
        """Return connection statistics."""
        return {
            "agents_connected": len(self._connections),
            "total_connections": sum(
                len(c) for c in self._connections.values()
            ),
        }
