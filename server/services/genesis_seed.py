"""Seed bundled genesis templates into the runtime data directory."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from nls.ledger.genesis import BRAIN_CONFIG_FILES
from nls.ledger.merkle import compute_genesis_hash

logger = logging.getLogger(__name__)

# Product-mode BYO templates ship without a values adapter; use a stable sentinel.
_BYO_SOUL_HASH = hashlib.sha256(b"byo-product-mode-no-soul-adapter").hexdigest()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Extended configs included in standard-v1 beyond the core BRAIN_CONFIG_FILES set.
_EXTENDED_CONFIG_FILES: tuple[str, ...] = (
    "narrative_self.json",
    "theory_of_mind.json",
    "predictive_processing.json",
    "temporal_self.json",
    "ofc.json",
    "working_memory.json",
    "network_dynamics.json",
)

STANDARD_V1_CONFIG_FILES: tuple[str, ...] = tuple(
    dict.fromkeys([*BRAIN_CONFIG_FILES, *_EXTENDED_CONFIG_FILES])
)

STANDARD_V1_VERSION = "standard-v1"

_FORBIDDEN_PROMPT_MARKERS: tuple[str, ...] = (
    "nls_signal tool",
    "using the nls_signal",
    "report cognitive signals using the nls_signal",
)


def _config_source_dir() -> Path:
    return _REPO_ROOT / "nls" / "config"


def _writable_bundled_root() -> Path:
    """Directory where build/dev writes genesis_templates/."""
    return _REPO_ROOT / "genesis_templates"


def _bundled_search_roots() -> list[Path]:
    """Locations that may contain pre-built genesis_templates (dev + packaged)."""
    roots = [
        _REPO_ROOT / "genesis_templates",
        _REPO_ROOT.parent / "genesis_templates",
    ]
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def resolve_bundled_root() -> Path | None:
    """Return the first bundled root that contains standard-v1, if any."""
    for root in _bundled_search_roots():
        if (root / STANDARD_V1_VERSION / "manifest.json").exists():
            return root
    return None


def repair_byo_manifest(manifest_path: Path) -> bool:
    """Fill missing hash fields on OSS standard-v1 manifests (older builds)."""
    if not manifest_path.exists():
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False

    changed = False
    base_model = data.get("base_model") or "bring-your-own"
    if not data.get("genesis_hash"):
        data["genesis_hash"] = compute_genesis_hash(base_model)
        changed = True
    if not data.get("soul_hash"):
        data["soul_hash"] = _BYO_SOUL_HASH
        changed = True

    if changed:
        manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("Repaired genesis manifest hashes at %s", manifest_path)
    return changed


def _apply_byo_runtime_patches(runtime_cfg: Path) -> None:
    data = json.loads(runtime_cfg.read_text(encoding="utf-8"))
    inf = data.setdefault("inference", {})
    inf["engine"] = "openai_compatible"
    inf.pop("quantization", None)
    data.pop("adapters", None)
    data.pop("training", None)
    runtime_cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _validate_runtime_prompts(runtime_cfg: Path) -> None:
    """Fail fast when stale nls_signal instructions slip into runtime.json."""
    if not runtime_cfg.exists():
        return
    data = json.loads(runtime_cfg.read_text(encoding="utf-8"))
    inf = data.get("inference", {})
    for key in ("system_prompt", "system_prompt_v5"):
        text = inf.get(key) or ""
        lower = text.lower()
        for marker in _FORBIDDEN_PROMPT_MARKERS:
            if marker in lower:
                raise ValueError(
                    f"{runtime_cfg.name} {key} still contains forbidden marker "
                    f"{marker!r} — regenerate from nls/config before building"
                )


def sync_standard_v1_configs(
    target: Path,
    *,
    config_src: Path | None = None,
) -> list[str]:
    """Copy brain config JSONs from nls/config into a genesis template directory."""
    src_dir = config_src or _config_source_dir()
    config_dest = target / "config"
    config_dest.mkdir(parents=True, exist_ok=True)
    (target / "defaults").mkdir(exist_ok=True)

    copied: list[str] = []
    for name in STANDARD_V1_CONFIG_FILES:
        src = src_dir / name
        if not src.exists():
            continue
        shutil.copy2(src, config_dest / name)
        copied.append(name)

    runtime_cfg = config_dest / "runtime.json"
    if runtime_cfg.exists():
        _apply_byo_runtime_patches(runtime_cfg)
        _validate_runtime_prompts(runtime_cfg)

    return copied


def _ensure_standard_v1_manifest(target: Path) -> None:
    manifest_path = target / "manifest.json"
    if manifest_path.exists():
        repair_byo_manifest(manifest_path)
        return

    base_model = "bring-your-own"
    manifest = {
        "version": STANDARD_V1_VERSION,
        "base_model": base_model,
        "description": "Default NLS agent — brain configs only, BYO inference.",
        "minted_at": datetime.now(timezone.utc).isoformat(),
        "profile": "standard",
        "soul_hash": _BYO_SOUL_HASH,
        "genesis_hash": compute_genesis_hash(base_model),
        "adapters": [],
        "education": None,
        "moe": None,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("Created genesis manifest at %s", manifest_path)


def regenerate_standard_v1_template(*, bundled_root: Path | None = None) -> Path:
    """Refresh standard-v1 config snapshots from nls/config (build + dev)."""
    target = (bundled_root or _writable_bundled_root()) / STANDARD_V1_VERSION
    copied = sync_standard_v1_configs(target)
    _ensure_standard_v1_manifest(target)
    logger.info(
        "Regenerated genesis template %s (%d config files from %s)",
        target,
        len(copied),
        _config_source_dir(),
    )
    return target


def write_standard_v1_template() -> None:
    """Ensure bundled standard-v1 exists with configs synced from nls/config."""
    try:
        regenerate_standard_v1_template()
    except OSError as exc:
        logger.warning(
            "Could not write bundled genesis_templates under %s: %s",
            _writable_bundled_root(),
            exc,
        )


def _sync_installed_template_configs(dest: Path) -> None:
    """Refresh an installed genesis template's config/ from nls/config."""
    if not dest.is_dir():
        return
    copied = sync_standard_v1_configs(dest)
    if copied:
        logger.info(
            "Synced %d genesis config file(s) into %s from %s",
            len(copied),
            dest,
            _config_source_dir(),
        )


def ensure_bundled_genesis(genesis_dir: Path) -> None:
    """Seed runtime data/genesis/ from bundled templates; always refresh configs."""
    write_standard_v1_template()

    genesis_dir.mkdir(parents=True, exist_ok=True)
    bundled_root = resolve_bundled_root()
    if bundled_root is None:
        logger.warning(
            "No bundled genesis templates found (searched %s)",
            ", ".join(str(p) for p in _bundled_search_roots()),
        )
        dest = genesis_dir / STANDARD_V1_VERSION
        if not (dest / "manifest.json").exists():
            regenerate_standard_v1_template(bundled_root=genesis_dir)
        else:
            _sync_installed_template_configs(dest)
        return

    for template_dir in bundled_root.iterdir():
        if not template_dir.is_dir():
            continue
        dest = genesis_dir / template_dir.name
        manifest = dest / "manifest.json"
        if not dest.exists() or not manifest.exists():
            logger.info("Seeding genesis template %s -> %s", template_dir.name, dest)
            shutil.copytree(template_dir, dest, dirs_exist_ok=True)
            repair_byo_manifest(dest / "manifest.json")
        _sync_installed_template_configs(dest)
