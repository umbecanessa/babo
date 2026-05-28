"""Chain block recording for genesis and sleep epochs."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from nls.ledger.chain_sleep import (
    ensure_genesis_block,
    record_consolidation_epoch,
    reconcile_chain_from_session_meta,
)
from nls.ledger.manifest import load_manifest
from nls.models import BlockType


def test_genesis_and_sleep_epochs():
    with tempfile.TemporaryDirectory() as tmp:
        agent_dir = Path(tmp)
        ledger = {
            "agent_id": "test-agent",
            "base_model": "test-model",
            "sovereignty_mode": "local",
            "current_height": 0,
            "genesis_hash": "a" * 64,
            "soul_hash": "b" * 64,
            "flip_threshold": 2,
            "flip_window_days": 30,
            "bridge_provider": "",
            "bridge_model": "",
            "active_epoch": None,
            "active_deltas": [],
            "frozen_epochs": [],
            "consolidated": [],
        }
        import yaml
        (agent_dir / "ledger.yaml").write_text(yaml.dump(ledger), encoding="utf-8")
        (agent_dir / "knowledge.db").touch()

        from nls.ledger.domain_db import DomainDB
        db = DomainDB(agent_dir / "knowledge.db")
        db.close()

        ensure_genesis_block(agent_dir)
        record_consolidation_epoch(
            agent_dir, sleep_index=1, aku_count=10, summary="first sleep",
        )

        state = load_manifest(agent_dir)
        assert state.current_height == 1
        assert state.consolidated[0].block_type == BlockType.GENESIS
        assert state.active_epoch is not None
        assert state.active_epoch.height == 1


def test_reconcile_from_session_meta():
    with tempfile.TemporaryDirectory() as tmp:
        agent_dir = Path(tmp)
        import yaml
        (agent_dir / "ledger.yaml").write_text(
            yaml.dump({
                "agent_id": "x",
                "base_model": "m",
                "sovereignty_mode": "local",
                "current_height": 0,
                "genesis_hash": "c" * 64,
                "soul_hash": "d" * 64,
                "flip_threshold": 2,
                "flip_window_days": 30,
                "active_epoch": None,
                "active_deltas": [],
                "frozen_epochs": [],
                "consolidated": [],
            }),
            encoding="utf-8",
        )
        (agent_dir / "session_meta.json").write_text(
            json.dumps({"sleep_count": 2}),
            encoding="utf-8",
        )
        from nls.ledger.domain_db import DomainDB
        DomainDB(agent_dir / "knowledge.db").close()

        state = reconcile_chain_from_session_meta(agent_dir)
        assert state.current_height == 2
        assert len(state.frozen_epochs) == 1
        assert state.active_epoch.height == 2
