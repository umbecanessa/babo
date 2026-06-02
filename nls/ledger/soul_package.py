"""Soul Package — portable agent state format.

A Soul Package is a lightweight, versioned archive that captures
everything needed to reconstruct an agent's *identity and knowledge*
on a new device — without model weights (those come from the genesis
template or remote GPU worker).

Format
------
``<agent_id>.soul.zip`` containing:

::

    soul_manifest.json    ← version, agent_meta, chain summary
    ans_state.json        ← full ANS signal buffer & state
    knowledge.db          ← SQLite domain ledger (facts, blocks, history)
    ledger.yaml           ← Merkle chain manifest
    hypothalamus_state.json
    calibration_bands.json
    domain_tracker.json
    experience_tracker.json
    tool_experience.json
    enabled_tools.json
    config/               ← per-agent runtime config
    sessions/             ← conversation history (optional, can be large)

Not included: adapters/, deltas/, epochs/ (model weights — re-trained
or pulled from cloud), events/ (research logs), workspace/ (local files).
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SOUL_PACKAGE_VERSION = "1.0"

STATE_FILES = [
    "agent_meta.json",
    "ans_state.json",
    "knowledge.db",
    "ledger.yaml",
    "hypothalamus_state.json",
    "calibration_bands.json",
    "domain_tracker.json",
    "experience_tracker.json",
    "tool_experience.json",
    "enabled_tools.json",
    "drive_cooldowns.json",
    "session_meta.json",
]

STATE_DIRS = [
    "config",
]

OPTIONAL_DIRS = [
    "sessions",
]


def export_soul(
    agent_dir: Path,
    output_path: Path | None = None,
    include_sessions: bool = False,
) -> Path:
    """Export an agent's state as a Soul Package.

    Parameters
    ----------
    agent_dir :
        Path to the agent's data directory.
    output_path :
        Where to write the .soul.zip file.  Defaults to
        ``agent_dir.parent / "{agent_id}.soul.zip"``.
    include_sessions :
        Whether to include conversation history (can be large).

    Returns
    -------
    Path
        The path to the exported .soul.zip file.
    """
    agent_id = agent_dir.name

    if output_path is None:
        output_path = agent_dir.parent / f"{agent_id}.soul.zip"

    meta_path = agent_dir / "agent_meta.json"
    agent_meta = {}
    if meta_path.exists():
        agent_meta = json.loads(meta_path.read_text(encoding="utf-8"))

    ledger_path = agent_dir / "ledger.yaml"
    chain_height = 0
    if ledger_path.exists():
        try:
            from nls.ledger.manifest import load_manifest
            state = load_manifest(agent_dir)
            chain_height = state.current_height
        except Exception:
            pass

    soul_manifest = {
        "version": SOUL_PACKAGE_VERSION,
        "agent_id": agent_id,
        "agent_meta": agent_meta,
        "chain_height": chain_height,
        "exported_at": datetime.utcnow().isoformat(),
        "include_sessions": include_sessions,
    }

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "soul_manifest.json",
            json.dumps(soul_manifest, indent=2, ensure_ascii=False),
        )

        for filename in STATE_FILES:
            file_path = agent_dir / filename
            if file_path.exists():
                zf.write(file_path, filename)

        for dirname in STATE_DIRS:
            dir_path = agent_dir / dirname
            if dir_path.is_dir():
                for child in dir_path.rglob("*"):
                    if child.is_file():
                        arcname = str(child.relative_to(agent_dir))
                        zf.write(child, arcname)

        if include_sessions:
            for dirname in OPTIONAL_DIRS:
                dir_path = agent_dir / dirname
                if dir_path.is_dir():
                    for child in dir_path.rglob("*"):
                        if child.is_file():
                            arcname = str(child.relative_to(agent_dir))
                            zf.write(child, arcname)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(
        "Soul Package exported: %s (%.1f MB, chain height %d)",
        output_path.name, size_mb, chain_height,
    )
    return output_path


def import_soul(
    package_path: Path,
    target_dir: Path,
    new_agent_id: str | None = None,
) -> dict[str, Any]:
    """Import a Soul Package into a target agent directory.

    Parameters
    ----------
    package_path :
        Path to the .soul.zip file.
    target_dir :
        The agent directory to write into (will be created if needed).
    new_agent_id :
        If provided, overrides the agent_id in the soul manifest
        (for forking an agent under a new identity).

    Returns
    -------
    dict
        The soul manifest with import metadata.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(package_path, "r") as zf:
        manifest_raw = zf.read("soul_manifest.json")
        soul_manifest = json.loads(manifest_raw)

        for member in zf.namelist():
            if member == "soul_manifest.json":
                continue
            zf.extract(member, target_dir)

    if new_agent_id:
        soul_manifest["original_agent_id"] = soul_manifest["agent_id"]
        soul_manifest["agent_id"] = new_agent_id

        meta_path = target_dir / "agent_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["original_agent_id"] = meta.get("agent_id", "")
            meta["agent_id"] = new_agent_id
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    soul_manifest["imported_at"] = datetime.utcnow().isoformat()
    soul_manifest["target_dir"] = str(target_dir)

    manifest_out = target_dir / "soul_manifest.json"
    manifest_out.write_text(
        json.dumps(soul_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.info(
        "Soul Package imported: %s -> %s (chain height %d)",
        package_path.name, target_dir, soul_manifest.get("chain_height", 0),
    )
    return soul_manifest


def get_soul_summary(package_path: Path) -> dict[str, Any]:
    """Read the soul manifest without extracting."""
    with zipfile.ZipFile(package_path, "r") as zf:
        return json.loads(zf.read("soul_manifest.json"))


def fork_at_height(
    source_dir: Path,
    target_dir: Path,
    fork_height: int,
    new_agent_id: str,
) -> dict[str, Any]:
    """Create a new agent forked from a specific point in the Merkle chain.

    This is the core "memory forking" operation:
    1. Exports a soul package from the source.
    2. Imports it into the target directory under a new agent ID.
    3. Truncates the chain to ``fork_height`` (removes blocks above).
    4. Truncates DomainDB facts above that height.

    Parameters
    ----------
    source_dir :
        The source agent's data directory.
    target_dir :
        Where the forked agent will live.
    fork_height :
        The chain height to fork at.  ``0`` means fork from genesis
        (clean knowledge, no deltas).  The block at ``fork_height``
        is included.
    new_agent_id :
        The new agent's unique identifier.

    Returns
    -------
    dict
        Fork metadata including chain_height, facts_retained, etc.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: export soul from source
    package = export_soul(source_dir, include_sessions=False)

    try:
        # Step 2: import into target
        manifest = import_soul(package, target_dir, new_agent_id=new_agent_id)

        # Step 3: truncate chain in the target
        chain_height = manifest.get("chain_height", 0)
        if fork_height < chain_height:
            _truncate_chain(target_dir, fork_height)

        # Step 4: truncate DomainDB facts above fork height
        facts_retained = _truncate_facts(target_dir, fork_height)

        # Step 5: clean up adapter files above fork height
        _clean_adapters(target_dir, fork_height)

        # Step 6: update agent_meta
        meta_path = target_dir / "agent_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["forked_from"] = source_dir.name
            meta["fork_height"] = fork_height
            meta["forked_at"] = datetime.utcnow().isoformat()
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        result = {
            "new_agent_id": new_agent_id,
            "forked_from": source_dir.name,
            "fork_height": fork_height,
            "original_height": chain_height,
            "facts_retained": facts_retained,
        }
        logger.info(
            "Memory fork: %s -> %s at height %d (original %d, %d facts)",
            source_dir.name, new_agent_id, fork_height,
            chain_height, facts_retained,
        )
        return result

    finally:
        package.unlink(missing_ok=True)


def _truncate_chain(agent_dir: Path, max_height: int) -> None:
    """Truncate the Merkle chain to a maximum height."""
    try:
        from nls.ledger.manifest import load_manifest, save_manifest
        state = load_manifest(agent_dir)

        state.active_deltas = [
            b for b in state.active_deltas if b.height <= max_height
        ]
        state.frozen_epochs = [
            b for b in state.frozen_epochs if b.height <= max_height
        ]
        state.consolidated = [
            b for b in state.consolidated if b.height <= max_height
        ]
        if state.active_epoch and state.active_epoch.height > max_height:
            state.active_epoch = None

        state.current_height = max_height
        save_manifest(agent_dir, state)
    except Exception as exc:
        logger.warning("Chain truncation failed: %s", exc)


def _truncate_facts(agent_dir: Path, max_height: int) -> int:
    """Remove DomainDB facts created above the fork height."""
    knowledge_db = agent_dir / "knowledge.db"
    if not knowledge_db.exists():
        return 0

    try:
        from nls.ledger.domain_db import DomainDB
        db = DomainDB(knowledge_db)

        all_facts = db.get_all_facts()
        retained = [f for f in all_facts if f.block_height <= max_height]

        # Delete blocks above fork height
        db.delete_blocks_above(max_height)

        return len(retained)
    except Exception as exc:
        logger.warning("Fact truncation failed: %s", exc)
        return 0


def _clean_adapters(agent_dir: Path, max_height: int) -> None:
    """Remove adapter directories for blocks above the fork height."""
    for dirname in ("deltas", "epochs"):
        dir_path = agent_dir / dirname
        if not dir_path.is_dir():
            continue
        for child in dir_path.iterdir():
            if child.is_dir():
                try:
                    height_str = child.name.split("_")[-1]
                    height = int(height_str)
                    if height > max_height:
                        shutil.rmtree(child, ignore_errors=True)
                except (ValueError, IndexError):
                    pass
