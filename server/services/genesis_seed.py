"""Seed bundled genesis templates into the runtime data directory."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED = _REPO_ROOT / "genesis_templates"


def ensure_bundled_genesis(genesis_dir: Path) -> None:
    """Copy genesis_templates/* into data/genesis/ if missing."""
    if not _BUNDLED.exists():
        logger.warning("No bundled genesis templates at %s", _BUNDLED)
        return

    genesis_dir.mkdir(parents=True, exist_ok=True)
    for template_dir in _BUNDLED.iterdir():
        if not template_dir.is_dir():
            continue
        dest = genesis_dir / template_dir.name
        if dest.exists() and (dest / "manifest.json").exists():
            continue
        logger.info("Seeding genesis template %s -> %s", template_dir.name, dest)
        shutil.copytree(template_dir, dest, dirs_exist_ok=True)


def write_standard_v1_template() -> None:
    """Create genesis_templates/standard-v1 from nls/config if absent."""
    target = _BUNDLED / "standard-v1"
    if (target / "manifest.json").exists():
        return

    config_src = _REPO_ROOT / "nls" / "config"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config").mkdir(exist_ok=True)
    (target / "defaults").mkdir(exist_ok=True)

    for name in (
        "runtime.json",
        "hormones.json",
        "autonomic.json",
        "dmn.json",
        "drives.json",
        "narrative_self.json",
        "theory_of_mind.json",
        "predictive_processing.json",
        "temporal_self.json",
        "ofc.json",
        "working_memory.json",
        "visual_cortex.json",
        "network_dynamics.json",
    ):
        src = config_src / name
        if src.exists():
            shutil.copy2(src, target / "config" / name)

    runtime_cfg = target / "config" / "runtime.json"
    if runtime_cfg.exists():
        data = json.loads(runtime_cfg.read_text(encoding="utf-8"))
        inf = data.setdefault("inference", {})
        inf["engine"] = "openai_compatible"
        inf.pop("quantization", None)
        data.pop("adapters", None)
        data.pop("training", None)
        runtime_cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")

    manifest = {
        "version": "standard-v1",
        "base_model": "bring-your-own",
        "description": "Default NLS agent — brain configs only, BYO inference.",
        "minted_at": datetime.now(timezone.utc).isoformat(),
        "profile": "standard",
        "adapters": [],
        "education": None,
        "moe": None,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )
    logger.info("Created bundled genesis template at %s", target)
