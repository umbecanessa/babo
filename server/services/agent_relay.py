"""Ensure ChannelRelayClient connections to NestJS for desktop agents."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from server.services.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

_SKIP_PREFIXES = ("_archived_", "_template_", "edu-proof-")
_SKIP_CONTAINS = ("bootstrap",)


def _relay_env() -> tuple[str, str]:
    nestjs_url = os.environ.get("NESTJS_URL", "").strip()
    relay_secret = (
        os.environ.get("RUNTIME_SHARED_SECRET", "").strip()
        or os.environ.get("NLS_SHARED_SECRET", "").strip()
    )
    return nestjs_url, relay_secret


def _agent_meta(agents_dir: Path, agent_id: str) -> tuple[str, str]:
    meta_path = agents_dir / agent_id / "agent_meta.json"
    if not meta_path.exists():
        return "", ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        name = meta.get("agent_name", "") or meta.get("name", "")
        genesis = meta.get("genesis_version", "") or ""
        return str(name or ""), str(genesis or "")
    except Exception:
        return "", ""


def list_relay_agent_ids(agents_dir: Path) -> list[str]:
    """Return agent directory names that should maintain a cloud relay."""
    if not agents_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in agents_dir.iterdir()
        if d.is_dir()
        and (d / "ledger.yaml").exists()
        and not any(d.name.startswith(p) for p in _SKIP_PREFIXES)
        and not any(kw in d.name for kw in _SKIP_CONTAINS)
    )


async def ensure_agent_relay(
    connection_manager: ConnectionManager | None,
    agent_id: str,
    agents_dir: Path,
    runtime: Any | None = None,
) -> bool:
    """Start a ChannelRelayClient for *agent_id* when NESTJS_URL is configured."""
    nestjs_url, relay_secret = _relay_env()
    if not nestjs_url or connection_manager is None:
        return False

    if connection_manager.has_relay(agent_id):
        return connection_manager.relay_connected(agent_id)

    agent_name = ""
    genesis_ver = ""
    if runtime is not None:
        agent_name = getattr(runtime, "_agent_name", "") or ""
        genesis_ver = getattr(runtime, "_genesis_version", "") or ""

    meta_name, meta_genesis = _agent_meta(agents_dir, agent_id)
    agent_name = agent_name or meta_name
    genesis_ver = genesis_ver or meta_genesis

    try:
        from nls.runtime.channels import ChannelRelayClient

        relay = ChannelRelayClient(
            nestjs_url,
            agent_id,
            relay_secret,
            agent_name=agent_name,
            genesis_version=genesis_ver,
        )
        await relay.connect()
        connection_manager.register_relay(agent_id, relay)
        logger.info("Started relay for agent %s", agent_id)
        return True
    except Exception as exc:
        logger.warning("Failed to start relay for %s: %s", agent_id, exc)
        return False


async def ensure_all_agent_relays(
    connection_manager: ConnectionManager | None,
    agents_dir: Path,
) -> int:
    """Start relays for every agent on disk (does not require VRAM load)."""
    started = 0
    for agent_id in list_relay_agent_ids(agents_dir):
        if await ensure_agent_relay(connection_manager, agent_id, agents_dir):
            started += 1
    return started
