"""NLS Manifest — YAML-based ledger manifest for agent identity.

The manifest (ledger.yaml) is the agent's "chain of command." It stores
the chain state, sovereignty mode, bridge config, and pointers to the
active epoch and delta blocks. The SQLite database handles the granular
fact/block data; the YAML manifest is the lightweight entry point.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from nls.config import settings
from nls.ledger.merkle import GENESIS_PARENT_HASH, compute_genesis_hash
from nls.models import Block, BlockType, ChainState, SovereigntyMode


def _manifest_path(agent_dir: Path) -> Path:
    """Return the path to an agent's ledger.yaml."""
    return agent_dir / "ledger.yaml"


def _chain_state_to_dict(state: ChainState) -> dict:
    """Serialize a ChainState to a YAML-friendly dict."""
    data = {
        "agent_id": state.agent_id,
        "base_model": state.base_model,
        "sovereignty_mode": state.sovereignty_mode.value,
        "current_height": state.current_height,
        "genesis_hash": state.genesis_hash,
        "soul_hash": state.soul_hash,
        "flip_threshold": state.flip_threshold,
        "flip_window_days": state.flip_window_days,
        "bridge_provider": state.bridge_provider,
        "bridge_model": state.bridge_model,
        "active_epoch": _block_to_dict(state.active_epoch) if state.active_epoch else None,
        "active_deltas": [_block_to_dict(b) for b in state.active_deltas],
        # Tiered memory consolidation
        "frozen_epochs": [_block_to_dict(b) for b in state.frozen_epochs],
        "consolidated": [_block_to_dict(b) for b in state.consolidated],
    }
    return data


def _block_to_dict(block: Block) -> dict:
    """Serialize a Block to a YAML-friendly dict."""
    return {
        "height": block.height,
        "block_hash": block.block_hash,
        "parent_hash": block.parent_hash,
        "block_type": block.block_type.value,
        "delta_path": block.delta_path,
        "timestamp": block.timestamp.isoformat(),
        "aku_count": block.aku_count,
        "metadata": block.metadata.model_dump(),
    }


def _dict_to_block(data: dict) -> Block:
    """Deserialize a dict into a Block."""
    data = dict(data)  # shallow copy
    data["block_type"] = BlockType(data["block_type"])
    if isinstance(data["timestamp"], str):
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
    return Block(**data)


def _dict_to_chain_state(data: dict) -> ChainState:
    """Deserialize a dict into a ChainState."""
    data = dict(data)  # shallow copy
    data["sovereignty_mode"] = SovereigntyMode(data["sovereignty_mode"])
    if data.get("active_epoch"):
        data["active_epoch"] = _dict_to_block(data["active_epoch"])
    data["active_deltas"] = [_dict_to_block(b) for b in (data.get("active_deltas") or [])]
    # Tiered memory consolidation (graceful upgrade — missing keys = empty lists)
    data["frozen_epochs"] = [_dict_to_block(b) for b in (data.get("frozen_epochs") or [])]
    data["consolidated"] = [_dict_to_block(b) for b in (data.get("consolidated") or [])]
    return ChainState(**data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def initialize_agent(
    agent_id: str,
    base_model: str,
    sovereignty_mode: SovereigntyMode | None = None,
    bridge_provider: str | None = None,
    bridge_model: str | None = None,
) -> ChainState:
    """Create a new agent identity with its directory structure and manifest.

    Creates:
        data/agents/{agent_id}/
        ├── ledger.yaml
        ├── epochs/
        ├── deltas/
        └── buffer.jsonl (empty)

    Returns the initial ChainState.
    """
    agent_dir = settings.agent_dir(agent_id)

    if agent_dir.exists():
        raise FileExistsError(f"Agent '{agent_id}' already exists at {agent_dir}")

    # Create directory structure
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "epochs").mkdir()
    (agent_dir / "deltas").mkdir()
    (agent_dir / "buffer.jsonl").touch()

    # Compute genesis hash
    genesis_hash = compute_genesis_hash(base_model)

    # Build initial state
    state = ChainState(
        agent_id=agent_id,
        base_model=base_model,
        sovereignty_mode=sovereignty_mode or settings.default_sovereignty_mode,
        current_height=0,
        genesis_hash=genesis_hash,
        active_epoch=None,
        active_deltas=[],
        flip_threshold=settings.flip_threshold,
        flip_window_days=settings.flip_window_days,
        bridge_provider=bridge_provider or settings.bridge_provider,
        bridge_model=bridge_model or settings.bridge_model,
    )

    # Write manifest
    save_manifest(agent_dir, state)

    return state


def load_manifest(agent_dir: Path) -> ChainState:
    """Load a ChainState from an agent's ledger.yaml."""
    manifest = _manifest_path(agent_dir)
    if not manifest.exists():
        raise FileNotFoundError(f"No manifest found at {manifest}")

    with open(manifest, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return _dict_to_chain_state(data)


def save_manifest(agent_dir: Path, state: ChainState) -> None:
    """Write a ChainState to an agent's ledger.yaml."""
    manifest = _manifest_path(agent_dir)
    data = _chain_state_to_dict(state)

    with open(manifest, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def append_block(agent_dir: Path, block: Block) -> ChainState:
    """Append a new block to the chain and update the manifest.

    Adds the block to active_deltas and increments current_height.
    """
    state = load_manifest(agent_dir)

    state.active_deltas.append(block)
    state.current_height = block.height

    save_manifest(agent_dir, state)
    return state


def set_epoch(agent_dir: Path, epoch_block: Block, replace_deltas: bool = True) -> ChainState:
    """Set a new epoch block after a TIES merge.

    If replace_deltas is True, clears the active_deltas list (they've been
    merged into the epoch).
    """
    state = load_manifest(agent_dir)

    state.active_epoch = epoch_block
    state.current_height = epoch_block.height
    if replace_deltas:
        state.active_deltas = []

    save_manifest(agent_dir, state)
    return state


def rollback_to_height(agent_dir: Path, target_height: int) -> ChainState:
    """Roll back the chain to a specific block height.

    Removes all deltas above the target height. If the target is at or
    below the active epoch, the epoch is preserved and deltas are cleared.
    """
    state = load_manifest(agent_dir)

    if target_height < 0:
        raise ValueError("Target height must be non-negative.")

    # Filter deltas to only those at or below target height
    state.active_deltas = [b for b in state.active_deltas if b.height <= target_height]

    # If target is at or below the epoch, keep epoch but clear deltas
    if state.active_epoch and target_height <= state.active_epoch.height:
        state.active_deltas = []
        state.current_height = state.active_epoch.height
    else:
        state.current_height = target_height

    save_manifest(agent_dir, state)
    return state


def freeze_epoch(
    agent_dir: Path,
    new_epoch_block: Block,
    replace_deltas: bool = True,
) -> ChainState:
    """Freeze the current active_epoch into Tier 2 and set a new epoch.

    This is the brain's transition from "actively consolidating" to
    "stored cortical column".  The old epoch becomes a frozen, immutable
    adapter that is stacked at inference.  The new epoch
    takes its place as the mutable consolidation target.

    Args:
        agent_dir: Agent data directory.
        new_epoch_block: The freshly TIES-merged epoch to install.
        replace_deltas: Clear active_deltas (they were merged into new_epoch).

    Returns:
        Updated ChainState.
    """
    state = load_manifest(agent_dir)

    # Move current epoch to frozen tier (if it exists)
    if state.active_epoch is not None:
        state.frozen_epochs.append(state.active_epoch)

    # Install the new epoch
    state.active_epoch = new_epoch_block
    state.current_height = new_epoch_block.height
    if replace_deltas:
        state.active_deltas = []

    save_manifest(agent_dir, state)
    return state


def consolidate_oldest(
    agent_dir: Path,
    merged_block: Block,
    n_consumed: int = 2,
) -> ChainState:
    """SLERP-merge the N oldest frozen epochs into a consolidated adapter.

    This is the brain's natural memory fading: vivid recent memories
    (frozen epochs) gradually compress into abstract long-term echoes
    (consolidated adapters).  Detail is lost, but the gist persists.

    Call this AFTER performing the actual SLERP merge on the adapter
    files.  This function only updates the manifest state.

    Args:
        agent_dir: Agent data directory.
        merged_block: Block representing the newly created consolidated adapter.
        n_consumed: Number of oldest frozen epochs that were merged (removed).

    Returns:
        Updated ChainState.
    """
    state = load_manifest(agent_dir)

    # Remove the consumed frozen epochs (oldest first)
    state.frozen_epochs = state.frozen_epochs[n_consumed:]

    # Add the merged result to consolidated tier
    state.consolidated.append(merged_block)

    save_manifest(agent_dir, state)
    return state


def compact_consolidated(
    agent_dir: Path,
    deep_memory_block: Block,
    n_consumed: int = 2,
) -> ChainState:
    """SLERP-merge the N oldest consolidated adapters into deep memory.

    The deepest tier: only the gist of very old knowledge survives.
    Like childhood memories — you 'just know' things without
    remembering when or how you learned them.

    Args:
        agent_dir: Agent data directory.
        deep_memory_block: Block representing the deep memory adapter.
        n_consumed: Number of oldest consolidated entries merged.

    Returns:
        Updated ChainState.
    """
    state = load_manifest(agent_dir)

    # Remove consumed consolidated entries
    state.consolidated = state.consolidated[n_consumed:]

    # Insert deep memory at the front (oldest position)
    state.consolidated.insert(0, deep_memory_block)

    save_manifest(agent_dir, state)
    return state


def get_active_chain(state: ChainState) -> list[Block]:
    """Return the ordered list of blocks to load during hydration.

    This is the epoch (if any) followed by all active deltas, sorted
    by height.
    """
    chain: list[Block] = []
    if state.active_epoch:
        chain.append(state.active_epoch)
    chain.extend(sorted(state.active_deltas, key=lambda b: b.height))
    return chain


def get_full_adapter_chain(state: ChainState) -> list[Block]:
    """Return ALL adapter blocks across all memory tiers for inference loading.

    Order: consolidated (oldest first) -> frozen_epochs (oldest first) ->
    active_epoch -> active_deltas.  This is the complete "memory stack"
    that gets composed additively at inference.
    """
    chain: list[Block] = []
    # Tier 3/4: consolidated / deep memory (oldest knowledge, most faded)
    chain.extend(state.consolidated)
    # Tier 2: frozen epochs (recent, vivid)
    chain.extend(state.frozen_epochs)
    # Tier 1: active epoch + deltas (current working memory)
    if state.active_epoch:
        chain.append(state.active_epoch)
    chain.extend(sorted(state.active_deltas, key=lambda b: b.height))
    return chain
