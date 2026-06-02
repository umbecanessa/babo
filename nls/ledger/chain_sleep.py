"""Merkle chain updates for genesis and consolidation sleep (BYO / no weight training)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nls.ledger.domain_db import DomainDB
from nls.ledger.manifest import load_manifest, save_manifest
from nls.ledger.merkle import GENESIS_PARENT_HASH, compute_block_hash
from nls.models import Block, BlockMetadata, BlockType, ChainState

logger = logging.getLogger(__name__)


def _insert_block_db(db: DomainDB, block: Block) -> None:
    """Insert block; SQLite schema allows delta/epoch only — genesis stored as epoch @0."""
    if block.block_type == BlockType.GENESIS:
        extra = dict(block.metadata.extra or {})
        extra["kind"] = "genesis"
        db_block = block.model_copy(
            update={
                "block_type": BlockType.EPOCH,
                "metadata": BlockMetadata(extra=extra),
            },
        )
        db.insert_block(db_block)
    else:
        db.insert_block(block)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _tip_hash(state: ChainState) -> str:
    if state.active_epoch is not None:
        return state.active_epoch.block_hash
    if state.frozen_epochs:
        return state.frozen_epochs[-1].block_hash
    if state.consolidated:
        return state.consolidated[-1].block_hash
    return state.genesis_hash or GENESIS_PARENT_HASH


def _has_genesis_block(state: ChainState) -> bool:
    if state.consolidated:
        if any(b.block_type == BlockType.GENESIS for b in state.consolidated):
            return True
        if any(b.height == 0 for b in state.consolidated):
            return True
    if state.active_epoch is not None and state.active_epoch.height == 0:
        return True
    return False


def _write_epoch_artifact(agent_dir: Path, height: int, payload: dict[str, Any]) -> str:
    """Persist sleep metadata; returns relative delta_path for the block."""
    epochs_dir = agent_dir / "epochs"
    epochs_dir.mkdir(parents=True, exist_ok=True)
    rel = f"epochs/sleep_{height:04d}.json"
    path = agent_dir / rel
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return rel


def ensure_genesis_block(agent_dir: Path) -> ChainState:
    """Create height-0 genesis block if missing (agent creation / first sleep)."""
    agent_dir = Path(agent_dir)
    state = load_manifest(agent_dir)
    if _has_genesis_block(state):
        return state

    ts = _utc_now()
    delta_path = "genesis/soul"
    block_hash = state.genesis_hash or compute_block_hash(
        GENESIS_PARENT_HASH, delta_path, 0, ts.isoformat(),
    )
    genesis = Block(
        height=0,
        block_hash=block_hash,
        parent_hash=GENESIS_PARENT_HASH,
        block_type=BlockType.GENESIS,
        delta_path=delta_path,
        timestamp=ts,
        aku_count=0,
        metadata=BlockMetadata(
            extra={
                "kind": "genesis",
                "soul_hash": state.soul_hash,
                "base_model": state.base_model,
            },
        ),
    )

    db_path = agent_dir / "knowledge.db"
    if db_path.exists():
        db = DomainDB(db_path)
        try:
            if db.get_block(0) is None:
                _insert_block_db(db, genesis)
        finally:
            db.close()

    state.consolidated = [genesis] + [
        b for b in state.consolidated
        if b.height != 0 and b.block_type != BlockType.GENESIS
    ]
    state.current_height = max(state.current_height, 0)
    save_manifest(agent_dir, state)
    logger.info("Chain: genesis block recorded for agent %s", state.agent_id)
    return state


def record_consolidation_epoch(
    agent_dir: Path,
    *,
    sleep_index: int,
    aku_count: int = 0,
    summary: str = "",
    sleep_type: str = "sleep",
) -> ChainState:
    """Append one epoch block per successful consolidation sleep."""
    agent_dir = Path(agent_dir)
    state = ensure_genesis_block(agent_dir)
    state = load_manifest(agent_dir)

    if sleep_index <= 0:
        sleep_index = max(1, state.current_height + 1)

    if state.active_epoch is not None and state.active_epoch.height == sleep_index:
        return state

    existing_heights = {
        b.height
        for b in (
            state.consolidated
            + state.frozen_epochs
            + ([state.active_epoch] if state.active_epoch else [])
            + state.active_deltas
        )
    }
    if sleep_index in existing_heights:
        return state

    parent = _tip_hash(state)
    ts = _utc_now()
    delta_path = _write_epoch_artifact(
        agent_dir,
        sleep_index,
        {
            "sleep_index": sleep_index,
            "sleep_type": sleep_type,
            "aku_count": aku_count,
            "summary": summary[:2000] if summary else "",
            "recorded_at": ts.isoformat(),
        },
    )
    block_hash = compute_block_hash(parent, delta_path, sleep_index, ts.isoformat())
    epoch = Block(
        height=sleep_index,
        block_hash=block_hash,
        parent_hash=parent,
        block_type=BlockType.EPOCH,
        delta_path=delta_path,
        timestamp=ts,
        aku_count=aku_count,
        metadata=BlockMetadata(
            extra={
                "sleep_index": sleep_index,
                "sleep_type": sleep_type,
                "consolidation_summary": (summary or "")[:500],
            },
        ),
    )

    db_path = agent_dir / "knowledge.db"
    if db_path.exists():
        db = DomainDB(db_path)
        try:
            _insert_block_db(db, epoch)
        finally:
            db.close()

    # Archive previous active epoch (not genesis — genesis stays in consolidated)
    if (
        state.active_epoch is not None
        and state.active_epoch.block_type != BlockType.GENESIS
        and state.active_epoch.height > 0
    ):
        if not any(b.height == state.active_epoch.height for b in state.frozen_epochs):
            state.frozen_epochs.append(state.active_epoch)

    state.active_epoch = epoch
    state.current_height = sleep_index
    save_manifest(agent_dir, state)
    logger.info(
        "Chain: sleep epoch block height=%d aku=%d agent=%s",
        sleep_index, aku_count, state.agent_id,
    )
    return state


def reconcile_chain_from_session_meta(agent_dir: Path) -> ChainState:
    """Backfill genesis + sleep epochs for agents created before chain recording."""
    agent_dir = Path(agent_dir)
    state = ensure_genesis_block(agent_dir)

    sleep_count = 0
    meta_path = agent_dir / "session_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            sleep_count = int(meta.get("sleep_count", 0) or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    for n in range(1, sleep_count + 1):
        state = record_consolidation_epoch(
            agent_dir,
            sleep_index=n,
            aku_count=0,
            summary="[backfilled epoch]",
            sleep_type="backfill",
        )
    return state


def sync_manifest_from_db(agent_dir: Path) -> ChainState | None:
    """Rebuild ledger.yaml block lists from knowledge.db if yaml was never updated."""
    agent_dir = Path(agent_dir)
    db_path = agent_dir / "knowledge.db"
    if not db_path.exists():
        return None
    db = DomainDB(db_path)
    try:
        blocks = db.get_all_blocks()
    finally:
        db.close()
    if not blocks:
        return None

    state = load_manifest(agent_dir)
    genesis_blocks: list[Block] = []
    epoch_blocks: list[Block] = []
    deltas: list[Block] = []
    for b in blocks:
        _kind = (b.metadata.extra or {}).get("kind") if b.metadata else None
        if b.block_type == BlockType.GENESIS or b.height == 0 or _kind == "genesis":
            if b.block_type != BlockType.GENESIS:
                b = b.model_copy(update={"block_type": BlockType.GENESIS})
            genesis_blocks.append(b)
        elif b.block_type == BlockType.EPOCH:
            epoch_blocks.append(b)
        else:
            deltas.append(b)

    if genesis_blocks:
        state.consolidated = genesis_blocks
    if len(epoch_blocks) > 1:
        state.frozen_epochs = epoch_blocks[:-1]
        state.active_epoch = epoch_blocks[-1]
    elif len(epoch_blocks) == 1:
        state.active_epoch = epoch_blocks[0]
    state.active_deltas = deltas
    if blocks:
        state.current_height = max(b.height for b in blocks)
    save_manifest(agent_dir, state)
    return state
