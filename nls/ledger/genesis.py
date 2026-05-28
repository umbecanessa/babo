"""NLS Genesis — Pre-minted template system for instant agent creation.

Two main operations:

    1. **mint_genesis()** — Creates a read-only genesis template from a fully
       bootstrapped agent's bootstrap store.  This captures the shared adapters
       (values, behavior, metacognition), a full snapshot of every brain JSON
       config, default runtime state files, and a genesis manifest into a
       versioned template directory.

    2. **create_agent_from_genesis()** — Creates a new agent in ~2 seconds by
       symlinking shared adapters from the genesis template, copying the full
       config snapshot and default state files into the agent's own directory,
       and initializing an empty ledger + DomainDB.

The genesis template is the foundation for the multi-agent platform: every
agent shares the same soul, behavior, and metacognition adapters (read-only),
and only per-agent memory (deltas, epochs, knowledge.db) diverges.

**Config-driven architecture:** Each genesis template snapshots the complete
set of brain configuration JSON files (hormones, autonomic, calibration,
drives, DMN, signals, runtime).  Each agent gets its own copy, so different
agents can be tuned independently in production — different hormonal
profiles, different sleep timing, different drive sensitivity, etc.

Template layout::

    data/genesis/{version}/
    ├── manifest.json               # GenesisManifest metadata
    ├── soul_hash                   # SHA-256 of values adapter (text file)
    ├── adapters/
    │   ├── values/adapter/         # Soul weights (shared, immutable)
    │   ├── behavior/adapter/       # Behavior adapter (shared)
    │   └── metacognition/adapter/  # Meta adapter (shared)
    ├── config/                     # Full brain config snapshot (JSON)
    │   ├── runtime.json
    │   ├── hormones.json
    │   ├── autonomic.json
    │   ├── calibration.json
    │   ├── drives.json
    │   ├── dmn.json
    │   └── signals.json
    └── defaults/                   # Initial runtime state (JSON)
        ├── hypothalamus_state.json
        ├── ans_state.json
        ├── calibration_bands.json
        ├── domain_tracker.json
        └── experience_tracker.json

Per-agent directory after creation::

    data/agents/{agent_id}/
    ├── ledger.yaml                 # Chain state (fresh, height 0)
    ├── agent_meta.json             # Agent metadata (name, genesis version, ...)
    ├── knowledge.db                # Empty DomainDB
    ├── buffer.jsonl                # Empty conversation buffer
    ├── config/                     # Agent's OWN config copy (editable!)
    │   ├── runtime.json
    │   ├── hormones.json
    │   ├── autonomic.json
    │   ├── calibration.json
    │   ├── drives.json
    │   ├── dmn.json
    │   └── signals.json
    ├── hypothalamus_state.json     # Initial hormonal state
    ├── ans_state.json              # Initial ANS state
    ├── calibration_bands.json      # Initial routing bands
    ├── domain_tracker.json         # Empty domain tracker
    ├── experience_tracker.json     # Empty experience tracker
    ├── adapters/                   # Symlinks -> genesis (read-only)
    │   ├── values -> ../../genesis/{ver}/adapters/values
    │   ├── behavior -> ...
    │   └── metacognition -> ...
    ├── epochs/                     # Empty (no merges yet)
    ├── deltas/                     # Empty (no training yet)
    └── events/                     # Empty (no logs yet)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from nls.config import settings
from nls.ledger.manifest import save_manifest
from nls.ledger.merkle import (
    GENESIS_PARENT_HASH,
    compute_genesis_hash,
    hash_adapter_dir,
)
from nls.models import ChainState, GenesisManifest, SovereigntyMode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Brain config files — the 8 JSON files that define the brain's behavior.
# These are snapshotted from nls/config/ into the genesis template at mint
# time, then copied per-agent at creation time.  This means every agent
# gets its own editable copy that can be tuned in production.
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# Every JSON config the brain needs.  Key = stem, value = filename.
BRAIN_CONFIG_FILES: list[str] = [
    "runtime.json",
    "hormones.json",
    "autonomic.json",
    "drives.json",
    "dmn.json",
    "signals.json",
    "visual_cortex.json",
]

# ---------------------------------------------------------------------------
# Default runtime state files — sensible initial values for a freshly
# created agent.  These match the reset/wake state in the runtime:
# all hormones at baseline, no signals buffered, no calibration yet.
#
# These are written to genesis/defaults/ at mint time so they can be
# edited in the template too (e.g. a "stressed" genesis that starts
# agents with high cortisol for testing).
# ---------------------------------------------------------------------------

_DEFAULT_HYPOTHALAMUS_STATE: dict[str, Any] = {
    "description": "Initial hormonal state. Levels should match baselines in hormones.json.",
    "hormones": {
        "cortisol":       {"level": 0.20, "last_update": None},
        "dopamine":       {"level": 0.50, "last_update": None},
        "norepinephrine": {"level": 0.30, "last_update": None},
        "serotonin":      {"level": 0.50, "last_update": None},
        "oxytocin":       {"level": 0.20, "last_update": None},
        "acetylcholine":  {"level": 0.30, "last_update": None},
    },
}

_DEFAULT_ANS_STATE: dict[str, Any] = {
    "description": "Initial ANS state. Awake, no signals, no sleep history.",
    "state": "awake",
    "signal_buffer": [],
    "turn_counter": 0,
    "recent_errors": [],
    "last_sleep_time": None,
    "sleep_cycle_count": 0,
}

_DEFAULT_DOMAIN_TRACKER: dict[str, Any] = {
    "description": "Empty domain encounter tracker. Updated as the agent learns.",
    "domains": {},
    "last_updated": None,
}

_DEFAULT_EXPERIENCE_TRACKER: dict[str, Any] = {
    "description": "Empty drive experience tracker. Populated as agent takes autonomous actions.",
    "experiences": {},
    "domain_cooldowns": {},
}

_DEFAULT_STATE_FILES: dict[str, dict[str, Any]] = {
    "hypothalamus_state.json": _DEFAULT_HYPOTHALAMUS_STATE,
    "ans_state.json":          _DEFAULT_ANS_STATE,
    "domain_tracker.json":     _DEFAULT_DOMAIN_TRACKER,
    "experience_tracker.json": _DEFAULT_EXPERIENCE_TRACKER,
}


# ---------------------------------------------------------------------------
# Mint Genesis
# ---------------------------------------------------------------------------


def mint_genesis(
    source_model: str,
    version: str,
    description: str = "",
    *,
    bootstrap_store_path: Path | None = None,
    config_overrides: dict[str, Path] | None = None,
    profile: str | None = None,
) -> GenesisManifest:
    """Create a pre-minted genesis template from a bootstrapped model's artifacts.

    This is a one-time operation run after bootstrap.  It packages:

    - Shared adapters from the bootstrap store (legacy: values + behavior +
      metacognition; V5: values + metacognition + signal probes)
    - A full snapshot of all 7 brain config JSON files
    - Default runtime state files (hormones at baseline, empty ANS, etc.)
    - A genesis manifest with metadata

    Once minted, the template can instantiate unlimited agents in ~2 seconds.

    Args:
        source_model: HuggingFace model name (e.g. 'unsloth/Meta-Llama-3.1-8B-Instruct').
            Used to locate the bootstrap store and compute genesis hash.
        version: Template version slug (e.g. '8b-v1'). Becomes the directory name.
        description: Human-readable description for the manifest.
        bootstrap_store_path: Override path to the bootstrap store.  If None,
            uses ``settings.bootstrap_store(source_model)``.
        config_overrides: Dict mapping config filename -> custom JSON path.
            Use this to bake non-default configs into the genesis template
            (e.g. ``{"hormones.json": Path("my_custom_hormones.json")}``).
        profile: Optional hardware/scenario profile name to apply on top of
            base configs before snapshotting.  The profile overrides are
            deep-merged into the base JSON, so the genesis captures the
            effective config.

    Returns:
        The GenesisManifest written to disk.

    Raises:
        FileNotFoundError: If the bootstrap store or required adapters don't exist.
        FileExistsError: If a genesis template with this version already exists.
    """
    # Locate bootstrap artifacts
    store = bootstrap_store_path or settings.bootstrap_store(source_model)
    if not store.exists():
        raise FileNotFoundError(
            f"Bootstrap store not found at {store}. "
            f"Run 'nls bootstrap' for model '{source_model}' first."
        )

    # Detect V5 mode: if probes/ exists in the store, this is a V5 bootstrap
    _is_v5 = (store / "probes" / "signal_probes.pt").exists()

    # Verify required adapters exist
    if _is_v5:
        required_adapters = ["values", "metacognition"]
    else:
        required_adapters = ["values", "behavior", "metacognition"]

    for name in required_adapters:
        adapter_dir = _find_adapter_dir(store, name)
        if adapter_dir is None:
            raise FileNotFoundError(
                f"Required adapter '{name}' not found in bootstrap store at {store}."
            )

    # Check version doesn't already exist
    genesis_dir = settings.genesis_template(version)
    if genesis_dir.exists():
        raise FileExistsError(
            f"Genesis template '{version}' already exists at {genesis_dir}. "
            f"Remove it first or choose a different version name."
        )

    # ── Create genesis directory structure ──
    genesis_dir.mkdir(parents=True, exist_ok=True)
    adapters_dir = genesis_dir / "adapters"
    config_dir = genesis_dir / "config"
    defaults_dir = genesis_dir / "defaults"
    adapters_dir.mkdir()
    config_dir.mkdir()
    defaults_dir.mkdir()

    # ── 1. Copy adapters ──
    for name in required_adapters:
        src = _find_adapter_dir(store, name)
        dst = adapters_dir / name / "adapter"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        logger.info("Copied adapter '%s' to genesis template", name)

    # ── 1b. Copy signal probes (V5) ──
    if _is_v5:
        probes_src = store / "probes"
        probes_dst = genesis_dir / "probes"
        probes_dst.mkdir(parents=True, exist_ok=True)
        for probe_file in probes_src.iterdir():
            if probe_file.is_file():
                shutil.copy2(str(probe_file), str(probes_dst / probe_file.name))
        logger.info("Copied signal probes to genesis template (V5 mode)")

    # ── 2. Compute hashes ──
    values_adapter_dir = adapters_dir / "values" / "adapter"
    soul_hash = hash_adapter_dir(values_adapter_dir)
    (genesis_dir / "soul_hash").write_text(soul_hash)
    genesis_hash = compute_genesis_hash(source_model)

    # ── 3. Snapshot brain config files ──
    # Load optional profile for deep-merge
    profile_data: dict[str, Any] = {}
    if profile:
        from nls.config import load_profile
        profile_data = load_profile(profile)

    overrides = config_overrides or {}
    config_count = 0

    for config_filename in BRAIN_CONFIG_FILES:
        stem = config_filename.replace(".json", "")

        if config_filename in overrides:
            # User-supplied custom config file
            src_path = overrides[config_filename]
            if not src_path.exists():
                raise FileNotFoundError(
                    f"Config override '{config_filename}' not found at {src_path}"
                )
            config_data = json.loads(src_path.read_text(encoding="utf-8"))
        else:
            # Read from the global nls/config/ directory
            base_path = _CONFIG_DIR / config_filename
            if not base_path.exists():
                logger.warning("Config file %s not found in nls/config/, skipping", config_filename)
                continue
            config_data = json.loads(base_path.read_text(encoding="utf-8"))

        # Apply profile overrides if present
        if profile_data and stem in profile_data:
            from nls.config import deep_merge
            config_data = deep_merge(config_data, profile_data[stem])

        _write_json(config_dir / config_filename, config_data)
        config_count += 1

    logger.info("Snapshotted %d brain config files", config_count)

    # ── 4. Write default runtime state files ──
    for filename, default_data in _DEFAULT_STATE_FILES.items():
        _write_json(defaults_dir / filename, default_data)

    # ── 5. Write manifest ──
    snapshotted_configs = sorted(
        f.name for f in (config_dir).iterdir() if f.suffix == ".json"
    )
    manifest = GenesisManifest(
        version=version,
        base_model=source_model,
        soul_hash=soul_hash,
        genesis_hash=genesis_hash,
        minted_at=datetime.utcnow(),
        description=description or f"Genesis template for {source_model}",
        adapters=required_adapters,
        config_files=snapshotted_configs,
        profile=profile or "",
    )
    _write_json(genesis_dir / "manifest.json", manifest.model_dump(mode="json"))

    logger.info(
        "Genesis template '%s' minted at %s (soul_hash: %s..., %d configs)",
        version, genesis_dir, soul_hash[:16], config_count,
    )

    return manifest


def promote_to_genesis(
    agent_id: str,
    version: str,
    description: str = "",
) -> GenesisManifest:
    """Promote a graduated agent's state into an educated genesis template.

    Takes a fully educated agent and packages its current state — epochs,
    knowledge.db, calibrated routing bands, education report, and updated
    defaults — into a new genesis template that can instantiate pre-educated
    agents in ~2 seconds.

    The original genesis adapters (values, behavior, metacognition) are
    preserved as symlinks to avoid duplication.

    Args:
        agent_id: The source agent to promote (must have education_report.json).
        version: Version slug for the new template (e.g. '8b-v1-scientifico').
        description: Human-readable description.

    Returns:
        The GenesisManifest written to disk.

    Raises:
        FileNotFoundError: If the agent or its genesis source doesn't exist.
        FileExistsError: If a genesis template with this version already exists.
    """
    agent_dir = settings.agent_dir(agent_id)
    if not agent_dir.exists():
        raise FileNotFoundError(f"Agent '{agent_id}' not found at {agent_dir}")

    # Load the agent's metadata to find its source genesis
    meta_path = agent_dir / "agent_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Agent metadata not found at {meta_path}")
    agent_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    source_genesis = agent_meta.get("genesis_version", "")

    # Load the source genesis manifest for base model info
    source_manifest = load_genesis_manifest(source_genesis)
    source_genesis_dir = settings.genesis_template(source_genesis)

    # Check the agent has been educated
    report_path = agent_dir / "education_report.json"
    if not report_path.exists():
        raise FileNotFoundError(
            f"Agent '{agent_id}' has no education_report.json. "
            f"Run 'nls educate' first."
        )
    education_report = json.loads(report_path.read_text(encoding="utf-8"))

    # Check target doesn't already exist
    genesis_dir = settings.genesis_template(version)
    if genesis_dir.exists():
        raise FileExistsError(
            f"Genesis template '{version}' already exists at {genesis_dir}. "
            f"Remove it first or choose a different version name."
        )

    # ── Create genesis directory structure ──
    genesis_dir.mkdir(parents=True, exist_ok=True)
    adapters_dir = genesis_dir / "adapters"
    config_dir = genesis_dir / "config"
    defaults_dir = genesis_dir / "defaults"
    adapters_dir.mkdir()
    config_dir.mkdir()
    defaults_dir.mkdir()

    # ── 1. Symlink shared adapters from the SOURCE genesis ──
    # These are immutable and shared across all agents from this lineage.
    for adapter_name in source_manifest.adapters:
        src = source_genesis_dir / "adapters" / adapter_name
        dst = adapters_dir / adapter_name
        _create_symlink(src, dst)
        logger.info("Linked adapter '%s' from source genesis", adapter_name)

    # ── 1b. Copy signal probes from source genesis (V5) ──
    source_probes = source_genesis_dir / "probes"
    if source_probes.exists():
        probes_dst = genesis_dir / "probes"
        probes_dst.mkdir(parents=True, exist_ok=True)
        for pf in source_probes.iterdir():
            if pf.is_file():
                shutil.copy2(str(pf), str(probes_dst / pf.name))
        logger.info("Copied signal probes from source genesis (V5)")

    # ── 2. Copy soul_hash from source ──
    source_soul_hash = source_genesis_dir / "soul_hash"
    if source_soul_hash.exists():
        shutil.copy2(source_soul_hash, genesis_dir / "soul_hash")

    # ── 3. Copy brain config files ──
    # Start from the SOURCE genesis template (full configs), then overlay
    # anything the agent has (weight_overrides, tuned settings).  This
    # ensures promoted templates always carry the complete runtime.json
    # (system prompt, inference settings, agency config, etc.) even when
    # the agent's own config/ only has a minimal override file.
    source_config_dir = source_genesis_dir / "config"
    if source_config_dir.exists():
        for config_file in source_config_dir.iterdir():
            if config_file.suffix == ".json":
                shutil.copy2(config_file, config_dir / config_file.name)

    agent_config_dir = agent_dir / "config"
    if agent_config_dir.exists():
        for config_file in agent_config_dir.iterdir():
            if config_file.suffix == ".json":
                dst = config_dir / config_file.name
                if dst.exists():
                    try:
                        base_data = json.loads(dst.read_text(encoding="utf-8"))
                        agent_data = json.loads(
                            config_file.read_text(encoding="utf-8"),
                        )
                        _deep_merge(base_data, agent_data)
                        _write_json(dst, base_data)
                    except (json.JSONDecodeError, OSError):
                        shutil.copy2(config_file, dst)
                else:
                    shutil.copy2(config_file, dst)

    # ── 4. Copy educated state into defaults/ ──
    # These are the post-education runtime state files that new agents
    # will inherit, giving them the educated agent's calibration, etc.
    state_files = [
        "hypothalamus_state.json",
        "ans_state.json",
        "domain_tracker.json",
        "experience_tracker.json",
    ]
    for sf in state_files:
        src = agent_dir / sf
        if src.exists():
            shutil.copy2(src, defaults_dir / sf)
        else:
            # Fall back to source genesis defaults
            fallback = source_genesis_dir / "defaults" / sf
            if fallback.exists():
                shutil.copy2(fallback, defaults_dir / sf)

    # ── 5. Copy epochs/ (merged adapters from sleep consolidation) ──
    agent_epochs = agent_dir / "epochs"
    if agent_epochs.exists() and any(agent_epochs.iterdir()):
        genesis_epochs = genesis_dir / "epochs"
        shutil.copytree(agent_epochs, genesis_epochs)
        logger.info("Copied %d epoch files", len(list(genesis_epochs.iterdir())))

    # ── 6. Copy knowledge.db (populated DomainDB) ──
    agent_db = agent_dir / "knowledge.db"
    if agent_db.exists():
        shutil.copy2(agent_db, genesis_dir / "knowledge.db")
        logger.info("Copied knowledge.db")

    # ── 7. Copy education report as provenance ──
    shutil.copy2(report_path, genesis_dir / "education_report.json")

    # ── 8. Build education metadata for the manifest ──
    # Field names differ between education runner versions; try the
    # canonical v2 names first, fall back to legacy names.
    education_meta = {
        "school": education_report.get("school_name", education_report.get("school", "")),
        "graduated": education_report.get("graduated", True),
        "graduated_at": education_report.get("end_time", ""),
        "total_facts": education_report.get("total_facts_taught", education_report.get("total_facts", 0)),
        "total_sleeps": education_report.get("total_sleep_cycles", education_report.get("total_sleeps", 0)),
        "source_agent": agent_id,
    }

    # ── 9. Write manifest ──
    snapshotted_configs = sorted(
        f.name for f in config_dir.iterdir() if f.suffix == ".json"
    )
    manifest = GenesisManifest(
        version=version,
        base_model=source_manifest.base_model,
        soul_hash=source_manifest.soul_hash,
        genesis_hash=source_manifest.genesis_hash,
        minted_at=datetime.utcnow(),
        description=description or f"Educated genesis from agent {agent_id}",
        adapters=source_manifest.adapters,
        config_files=snapshotted_configs,
        profile=source_manifest.profile,
        education=education_meta,
    )
    _write_json(genesis_dir / "manifest.json", manifest.model_dump(mode="json"))

    logger.info(
        "Educated genesis '%s' promoted from agent '%s' at %s",
        version, agent_id, genesis_dir,
    )

    return manifest


def load_genesis_manifest(version: str) -> GenesisManifest:
    """Load a genesis template manifest by version slug.

    Args:
        version: Template version (e.g. '8b-v1').

    Returns:
        GenesisManifest loaded from disk.

    Raises:
        FileNotFoundError: If the template doesn't exist.
    """
    genesis_dir = settings.genesis_template(version)
    manifest_path = genesis_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Genesis template '{version}' not found at {genesis_dir}. "
            f"Available templates: {list_genesis_templates()}"
        )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return GenesisManifest(**data)


def list_genesis_templates() -> list[str]:
    """Return version slugs of all available genesis templates."""
    root = settings.genesis_root()
    if not root.exists():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "manifest.json").exists()
    )


def list_genesis_templates_detail() -> list[dict[str, Any]]:
    """Return detailed info for all genesis templates (for the API).

    Each entry includes the manifest metadata plus computed fields:
    - ``educated``: whether this template includes pre-education state
    - ``has_epochs``: whether merged epoch adapters are bundled
    - ``facts_count``: number of facts in the bundled knowledge.db (if any)
    """
    results: list[dict[str, Any]] = []
    for version in list_genesis_templates():
        try:
            manifest = load_genesis_manifest(version)
            genesis_dir = settings.genesis_template(version)

            entry: dict[str, Any] = {
                "version": manifest.version,
                "base_model": manifest.base_model,
                "description": manifest.description,
                "minted_at": manifest.minted_at.isoformat() if manifest.minted_at else None,
                "profile": manifest.profile,
                "educated": manifest.education is not None,
                "education": manifest.education,
                "has_epochs": (genesis_dir / "epochs").exists()
                    and any((genesis_dir / "epochs").iterdir()),
            }
            results.append(entry)
        except Exception as exc:
            logger.warning("Failed to load genesis '%s': %s", version, exc)
            results.append({"version": version, "error": str(exc)})

    return results


# ---------------------------------------------------------------------------
# Create Agent from Genesis
# ---------------------------------------------------------------------------


def create_agent_from_genesis(
    genesis_version: str,
    agent_id: str | None = None,
    agent_name: str = "",
    sovereignty_mode: SovereigntyMode | None = None,
    *,
    use_symlinks: bool = True,
    skip_adapters: bool = False,
    config_overrides: dict[str, dict[str, Any]] | None = None,
    soul_wish: str = "",
) -> tuple[str, ChainState]:
    """Create a new agent from a pre-minted genesis template (~2 seconds).

    This is the fast path for agent creation on the multi-agent platform.
    Instead of running a full bootstrap, it:

    1. Generates a new agent_id (UUID) if not provided
    2. Creates the agent directory structure
    3. Symlinks (or copies) shared adapters from the genesis template
    4. Copies brain config files into the agent's own ``config/`` directory
    5. Copies default runtime state files
    6. Initializes an empty ledger.yaml and knowledge.db
    7. Agent is immediately ready for inference

    Each agent gets its **own copy** of all config files.  This means you
    can later edit an individual agent's ``config/hormones.json`` to give
    it a different personality, or change ``config/drives.json`` to make
    it more or less curious — without affecting any other agent.

    Args:
        genesis_version: Template version slug (e.g. '8b-v1').
        agent_id: Optional specific agent ID. Generates UUID if None.
        agent_name: Optional human-readable name for the agent.
        sovereignty_mode: Privacy mode. Defaults to LOCAL.
        use_symlinks: If True, symlink shared adapters (saves disk).
            If False, copy them (needed on filesystems without symlink support).
        skip_adapters: If True, skip adapter symlink/copy entirely.
            Used in desktop mode where artifacts live on a remote host and
            are managed by the GPU Worker.
        config_overrides: Optional per-agent config patches.  Dict mapping
            config stem (e.g. ``"hormones"``) to a partial dict that is
            deep-merged over the genesis config.  Example::

                config_overrides={
                    "hormones": {
                        "hormones": {
                            "cortisol": {"baseline": 0.40}
                        }
                    }
                }

            This creates an agent with elevated baseline cortisol (more anxious).

    Returns:
        Tuple of (agent_id, ChainState) for the newly created agent.

    Raises:
        FileNotFoundError: If the genesis template doesn't exist.
        FileExistsError: If an agent with this ID already exists.
    """
    # Load genesis manifest
    manifest = load_genesis_manifest(genesis_version)
    genesis_dir = settings.genesis_template(genesis_version)

    # Generate agent ID
    if agent_id is None:
        agent_id = str(uuid.uuid4())

    # Check agent doesn't already exist
    agent_dir = settings.agent_dir(agent_id)
    if agent_dir.exists():
        raise FileExistsError(f"Agent '{agent_id}' already exists at {agent_dir}")

    # ── Create directory structure ──
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "epochs").mkdir()
    (agent_dir / "deltas").mkdir()
    (agent_dir / "events").mkdir()
    (agent_dir / "buffer.jsonl").touch()

    # ── 1. Link or copy shared adapters ──
    adapters_dst = agent_dir / "adapters"
    adapters_dst.mkdir(parents=True, exist_ok=True)

    if skip_adapters:
        logger.debug(
            "Skipping adapter setup for '%s' (desktop mode -- remote artifacts only)",
            agent_id,
        )
    else:
        for adapter_name in manifest.adapters:
            src = genesis_dir / "adapters" / adapter_name
            dst = adapters_dst / adapter_name
            if use_symlinks:
                _create_symlink(src, dst)
            else:
                shutil.copytree(src, dst)
            logger.debug("Linked adapter '%s' for agent '%s'", adapter_name, agent_id)

        # V5: copy signal probes if present in genesis template
        probes_src = genesis_dir / "probes"
        if probes_src.exists():
            probes_dst = adapters_dst / "probes"
            probes_dst.mkdir(parents=True, exist_ok=True)
            for pf in probes_src.iterdir():
                if pf.is_file():
                    shutil.copy2(str(pf), str(probes_dst / pf.name))
            logger.debug("Copied signal probes for agent '%s'", agent_id)

    # ── 2. Copy brain config files (each agent gets its own copy) ──
    genesis_config_dir = genesis_dir / "config"
    agent_config_dir = agent_dir / "config"
    agent_config_dir.mkdir(exist_ok=True)

    if genesis_config_dir.exists():
        for config_file in genesis_config_dir.iterdir():
            if config_file.suffix == ".json":
                stem = config_file.stem

                # Read the genesis config
                config_data = json.loads(config_file.read_text(encoding="utf-8"))

                # Apply per-agent overrides if provided
                if config_overrides and stem in config_overrides:
                    from nls.config import deep_merge
                    config_data = deep_merge(config_data, config_overrides[stem])

                _write_json(agent_config_dir / config_file.name, config_data)

    # ── 3. Copy default runtime state files ──
    defaults_dir = genesis_dir / "defaults"
    if defaults_dir.exists():
        for state_file in defaults_dir.iterdir():
            if state_file.is_file():
                shutil.copy2(state_file, agent_dir / state_file.name)

    # Reset ANS timestamps so new agents don't inherit stale sleep timers
    # from the genesis template (which would trigger immediate periodic sleep).
    ans_state_path = agent_dir / "ans_state.json"
    if ans_state_path.exists():
        try:
            ans_data = json.loads(ans_state_path.read_text(encoding="utf-8"))
            now_iso = datetime.utcnow().isoformat()
            ans_data["last_sleep_at"] = now_iso
            ans_data["last_interaction_at"] = now_iso
            ans_data["turn_counter"] = 0
            ans_data["signal_buffer"] = []
            ans_data["recent_errors"] = []
            _write_json(ans_state_path, ans_data)
            logger.debug("Reset ANS timestamps for new agent '%s'", agent_id)
        except Exception as exc:
            logger.warning("Failed to reset ANS state for '%s': %s", agent_id, exc)

    # Reset domain tracker cycle counter so newborns begin in the
    # critical period (sponge phase).  Educated genesis templates carry
    # the cycle count from the agent they were promoted from, but a new
    # agent's developmental maturity must start at zero — the domain
    # encounter map is preserved so the thalamus knows which topics
    # the genesis was trained on.
    tracker_path = agent_dir / "domain_tracker.json"
    if tracker_path.exists():
        try:
            tracker_data = json.loads(tracker_path.read_text(encoding="utf-8"))
            tracker_data["current_cycle"] = 0
            _write_json(tracker_path, tracker_data)
            logger.debug("Reset domain tracker cycle for new agent '%s'", agent_id)
        except Exception as exc:
            logger.warning(
                "Failed to reset domain tracker for '%s': %s", agent_id, exc,
            )

    # ── 4. Copy educated state if the genesis template has it ──
    # Educated genesis templates include epochs, knowledge.db, and an
    # education report — giving new agents a pre-built mental map.
    genesis_epochs = genesis_dir / "epochs"
    if genesis_epochs.exists() and any(genesis_epochs.iterdir()):
        agent_epochs = agent_dir / "epochs"
        for epoch_file in genesis_epochs.iterdir():
            if epoch_file.is_file():
                shutil.copy2(epoch_file, agent_epochs / epoch_file.name)
        logger.debug(
            "Copied %d epoch files from genesis for agent '%s'",
            len(list(genesis_epochs.iterdir())), agent_id,
        )

    genesis_db = genesis_dir / "knowledge.db"
    if genesis_db.exists():
        # Copy the pre-populated knowledge DB instead of creating empty
        shutil.copy2(genesis_db, agent_dir / "knowledge.db")
        logger.debug("Copied pre-populated knowledge.db for agent '%s'", agent_id)
    else:
        # No educated DB — initialize empty
        from nls.ledger.domain_db import DomainDB
        db = DomainDB(agent_dir / "knowledge.db")
        db.conn  # Trigger schema creation
        db.close()

    genesis_report = genesis_dir / "education_report.json"
    if genesis_report.exists():
        shutil.copy2(genesis_report, agent_dir / "education_report.json")
        logger.debug("Copied education_report.json for agent '%s'", agent_id)

    # ── 5. Build initial chain state ──
    state = ChainState(
        agent_id=agent_id,
        base_model=manifest.base_model,
        sovereignty_mode=sovereignty_mode or settings.default_sovereignty_mode,
        current_height=0,
        genesis_hash=manifest.genesis_hash,
        soul_hash=manifest.soul_hash,
        active_epoch=None,
        active_deltas=[],
        flip_threshold=settings.flip_threshold,
        flip_window_days=settings.flip_window_days,
        bridge_provider=settings.bridge_provider,
        bridge_model=settings.bridge_model,
    )

    # Write ledger manifest
    save_manifest(agent_dir, state)

    try:
        from nls.ledger.chain_sleep import ensure_genesis_block
        ensure_genesis_block(agent_dir)
    except Exception as exc:
        logger.warning("Genesis block init failed for %s: %s", agent_id, exc)

    # ── 6. Write agent metadata ──
    agent_meta: dict[str, Any] = {
        "agent_id": agent_id,
        "agent_name": agent_name or "",
        "genesis_version": genesis_version,
        "base_model": manifest.base_model,
        "created_at": datetime.utcnow().isoformat(),
        "sovereignty_mode": state.sovereignty_mode.value,
        "soul_wish": soul_wish or "",
        "memory_slot": None,
        "max_skill_slots": 3,
        "equipped_cards": [],
    }
    _write_json(agent_dir / "agent_meta.json", agent_meta)

    # ── 7. Write default enabled tools ──
    # Only core knowledge tools are enabled at birth.
    # All others must be explicitly installed via the Tool Shop.
    _write_json(agent_dir / "enabled_tools.json", {
        "enabled": ["web_search", "wikipedia"],
    })

    logger.info(
        "Agent '%s' created from genesis '%s' (soul_hash: %s...)",
        agent_id, genesis_version, manifest.soul_hash[:16],
    )

    return agent_id, state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge *override* into *base* in-place."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


def _write_json(path: Path, data: Any) -> None:
    """Write JSON data to a file with pretty formatting."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def _find_adapter_dir(store: Path, name: str) -> Path | None:
    """Find an adapter directory in the bootstrap store.

    Handles both layouts:
      - store/name/adapter/  (standard bootstrap output)
      - store/name/          (flat layout)

    Returns the path containing the actual adapter files, or None.
    """
    nested = store / name / "adapter"
    if nested.exists():
        return nested
    flat = store / name
    if flat.exists():
        return flat
    return None


def _create_symlink(src: Path, dst: Path) -> None:
    """Create a symlink, falling back to directory junction on Windows.

    On Windows, creating symlinks requires elevated privileges or
    Developer Mode. Directory junctions work without elevation and
    behave identically for our read-only adapter access pattern.
    """
    try:
        # Try symlink first (works on Unix, and Windows with dev mode)
        dst.symlink_to(src.resolve(), target_is_directory=True)
    except OSError:
        if os.name == "nt":
            # Fall back to directory junction on Windows
            import subprocess

            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(dst), str(src.resolve())],
                check=True,
                capture_output=True,
            )
        else:
            raise
