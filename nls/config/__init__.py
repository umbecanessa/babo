"""NLS Configuration — Global settings, paths, and defaults.

Uses pydantic-settings for environment variable overrides. All paths
are relative to the configured data directory.

Profiles:
    Hardware/scenario profiles live in ``nls/config/profiles/<name>.json``.
    A profile is a JSON dict whose top-level keys map to base config
    filenames (``runtime``, ``drives``, ``autonomic``, ``dmn``, etc.).
    Each value is a partial dict that is *deep-merged* over the
    corresponding base config at startup.

    Usage:
        python -m nls.analytics.overnight --profile standard-v1
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings

from nls.models import SovereigntyMode


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DATA_DIR = _PROJECT_ROOT / "data"
_CONFIG_DIR = Path(__file__).resolve().parent
_PROFILES_DIR = _CONFIG_DIR / "profiles"


# ---------------------------------------------------------------------------
# Profile system
# ---------------------------------------------------------------------------


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge *overlay* into *base*, returning a new dict.

    - Dict values are merged recursively.
    - All other types in *overlay* replace the base value.
    - Neither input is mutated.
    """
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_profile(name: str) -> dict[str, Any]:
    """Load a hardware/scenario profile by name.

    Profiles live in ``nls/config/profiles/<name>.json``.  The returned
    dict has top-level keys that correspond to base config files::

        {
          "name": "standard-v1",
          "description": "...",
          "runtime":   { ... overrides for runtime.json   ... },
          "drives":    { ... overrides for drives.json    ... },
          "autonomic": { ... overrides for autonomic.json ... },
          "dmn":       { ... overrides for dmn.json       ... },
          ...
        }
    """
    path = _PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Profile '{name}' not found at {path}.  "
            f"Available profiles: {list_profiles()}"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_profiles() -> list[str]:
    """Return names of all available profiles."""
    if not _PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in _PROFILES_DIR.glob("*.json"))


def apply_profile_to_config(
    base_config: dict[str, Any],
    profile: dict[str, Any],
    config_key: str,
) -> dict[str, Any]:
    """Return *base_config* deep-merged with profile overrides for *config_key*.

    If the profile has no overrides for this config, the original is
    returned unchanged (no copy overhead).
    """
    overrides = profile.get(config_key)
    if not overrides:
        return base_config
    return deep_merge(base_config, overrides)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class NLSSettings(BaseSettings):
    """Global NLS configuration.

    Values can be overridden via environment variables prefixed with ``NLS_``.
    Example: ``NLS_DATA_DIR=/mnt/ssd/nls`` overrides the data directory.
    """

    model_config = {"env_prefix": "NLS_"}

    # --- Paths ---
    data_dir: Path = Field(
        default=_DEFAULT_DATA_DIR,
        description="Root directory for all agent data.",
    )

    # --- Default agent settings ---
    default_sovereignty_mode: SovereigntyMode = Field(
        default=SovereigntyMode.LOCAL,
        description="Default privacy mode for new agents.",
    )
    default_base_model: str = Field(
        default="",
        description="Default path/name of the GGUF base model.",
    )

    # --- Inference (llama-cpp-python) ---
    n_ctx: int = Field(
        default=8192,
        description="Context window size for inference.",
    )
    n_gpu_layers: int = Field(
        default=-1,
        description="Number of layers to offload to GPU (-1 = all).",
    )
    n_threads: int = Field(
        default=0,
        description="Number of CPU threads (0 = auto-detect).",
    )

    # --- Consolidation replay (product sleep; weights are not trained locally) ---
    mining_learning_rate: float = Field(
        default=2e-4,
        description="Legacy replay rate field (unused in BYO inference mode).",
    )
    sleep_learning_rate: float = Field(
        default=5e-5,
        description="Legacy replay rate field (unused in BYO inference mode).",
    )
    mining_epochs: int = Field(default=1, description="Legacy field (unused).")
    mining_batch_size: int = Field(default=4, description="Legacy field (unused).")

    # --- Conflict resolution ---
    omega_positive: float = Field(
        default=2.0,
        description="Loss weight for corrective facts (gradient descent).",
    )
    omega_negative: float = Field(
        default=0.5,
        description="Loss weight for erasure of old facts (gradient ascent).",
    )
    omega_feedback: float = Field(
        default=3.0,
        description=(
            "Loss weight for user feedback and user-edited facts. "
            "Applied to Feedback.* domain signals and source='user_edit' signals."
        ),
    )
    flip_threshold: int = Field(
        default=2,
        description="Number of value changes before a fact is marked fluid.",
    )
    flip_window_days: int = Field(
        default=30,
        description="Rolling window (days) for the flip counter.",
    )

    # --- Merging ---
    merge_every_n_blocks: int = Field(
        default=5,
        description="Trigger TIES epoch merge after this many delta blocks.",
    )
    ties_density: float = Field(
        default=0.1,
        description="Fraction of parameters to keep during TIES density trimming (top 10%).",
    )

    # --- Tiered Memory Consolidation ---
    max_frozen_epochs: int = Field(
        default=10,
        description="Max frozen epochs (Tier 2) before oldest are SLERP-merged "
        "into consolidated adapters (Tier 3). Mimics cortical column capacity.",
    )
    max_consolidated: int = Field(
        default=3,
        description="Max consolidated adapters (Tier 3) before oldest are "
        "SLERP-merged into a single deep_memory adapter (Tier 4).",
    )
    consolidation_slerp_t: float = Field(
        default=0.5,
        description="SLERP interpolation factor when merging tiers "
        "(0.5 = equal blend of the two oldest adapters).",
    )

    # --- Hippocampal Replay ---
    replay_fraction: float = Field(
        default=0.15,
        description="Fraction of old facts (from knowledge.db) to replay "
        "during each sleep consolidation cycle.  Set to 0.15 to "
        "match the biological ~15%% replay coverage observed in SWS.",
    )
    replay_min: int = Field(
        default=5,
        description="Minimum replay entries per sleep cycle (floor).  "
        "Even with a small DB, always replay at least this many.",
    )
    replay_ceiling: int = Field(
        default=50,
        description="Maximum replay entries per sleep cycle (ceiling).  "
        "Even with a huge DB, don't exceed this — mimics the brain's "
        "fixed SWS window constraining total replay volume.",
    )
    replay_weight: float = Field(
        default=0.3,
        description="Training loss weight for replay entries "
        "(lighter than new learning — gentle refresh).",
    )

    # --- SVD Synaptic Pruning ---
    svd_prune_fraction: float = Field(
        default=0.1,
        description="Bottom fraction of singular values to prune from merged "
        "adapters (removes intruder dimensions / synaptic noise).",
    )

    # --- EWC (Elastic Weight Consolidation) ---
    ewc_lambda: float = Field(
        default=1000.0,
        description="EWC regularization strength. Higher = more protection "
        "for important parameters from prior epochs.",
    )
    ewc_hormone_modulation: bool = Field(
        default=True,
        description="Use ANS hormonal fingerprints to modulate Fisher "
        "importance (dopamine ↑ importance, cortisol ↓ importance).",
    )

    # --- Semantic Drift Detection ---
    benchmark_after_merge: bool = Field(
        default=True,
        description="Run semantic drift benchmark after every epoch merge.",
    )
    quality_floor: float = Field(
        default=0.6,
        description="Minimum benchmark score (0.0-1.0) before drift is flagged. "
        "Default 0.6 means 4/6 core tests must pass.",
    )

    # --- Genesis Templates ---
    genesis_dir: str = Field(
        default="",
        description="Root directory for pre-minted genesis templates. "
        "Defaults to data_dir / 'genesis'. Each template lives in a "
        "subdirectory named by its version slug (e.g. '8b-v1').",
    )

    # --- Server ---
    serve_port: int = Field(
        default=8443,
        description="Port for the FastAPI inference server.",
    )
    serve_host: str = Field(
        default="0.0.0.0",
        description="Host to bind the FastAPI inference server to.",
    )

    # --- Cloud Bridge ---
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key (for Masked/Full bridge modes).",
    )
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key (alternative cloud provider).",
    )
    bridge_provider: str = Field(
        default="anthropic",
        description="Cloud provider for the bridge: 'anthropic' or 'openai'.",
    )
    bridge_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Cloud model used as the Synaptic Teacher.",
    )

    # --- Helpers ---

    def genesis_root(self) -> Path:
        """Return the root directory for all genesis templates.

        Uses ``genesis_dir`` if set, otherwise ``data_dir / 'genesis'``.
        """
        if self.genesis_dir:
            return Path(self.genesis_dir)
        return self.data_dir / "genesis"

    def genesis_template(self, version: str) -> Path:
        """Return the directory for a specific genesis template version.

        Example: ``genesis_template('8b-v1')`` -> ``data/genesis/8b-v1/``
        """
        return self.genesis_root() / version

    def agent_dir(self, agent_id: str) -> Path:
        """Return the data directory for a specific agent."""
        return self.data_dir / "agents" / agent_id

    def agents_root(self) -> Path:
        """Return the root directory containing all agent identities."""
        return self.data_dir / "agents"

    @staticmethod
    def model_slug(hf_model_name: str) -> str:
        """Convert a HuggingFace model name to a filesystem-safe slug.

        Example: 'unsloth/Meta-Llama-3.1-8B-Instruct' -> 'unsloth--meta-llama-3.1-8b-instruct'
        """
        return hf_model_name.replace("/", "--").lower()

    def bootstrap_store(self, hf_model_name: str) -> Path:
        """Return the central bootstrap artifact directory for a base model.

        Bootstrap adapters (values, behavior, metacognition) are model-specific,
        not agent-specific.  They are trained once per base model and reused
        by every agent running on that model.

        Layout:
            data/bootstrap/{model-slug}/
            ├── values/adapter/
            ├── behavior/adapter/
            └── metacognition/adapter/
        """
        return self.data_dir / "bootstrap" / self.model_slug(hf_model_name)

    def bootstrap_store_root(self) -> Path:
        """Return the root directory for all bootstrap artifacts."""
        return self.data_dir / "bootstrap"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

settings = NLSSettings()
