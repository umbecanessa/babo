# Config package (`nls/config`)

Global **`NLSSettings`** (Pydantic) and static JSON defaults for brain subsystems, tool manifests, and profiles.

---

## Code

| Symbol | Role |
|--------|------|
| `NLSSettings` | Env-backed settings: `data_dir`, `serve_port`, inference URL, keys |
| `settings` | Singleton instance |
| `load_profile`, `apply_profile_to_config`, `deep_merge` | Profile overlays |

---

## JSON defaults (`nls/config/`)

| File | Subsystem |
|------|-----------|
| `runtime.json` | Runtime-wide defaults |
| `autonomic.json`, `hormones.json`, `drives.json`, `dmn.json` | Brain |
| `tool_bundles.json` | Bundled tool groups |
| `agentskills.json` | AgentSkills defaults |
| `config/tools/*.json` | Per-tool JSON registry |
| `config/profiles/*.json` | Named profiles |
| `config/recipes/**/*.json` | Recipe templates |

Per-agent copies land in `data/agents/{id}/config/` via genesis/factory.

---

## Environment

All `NLS_*` variables — see [Environment (complete)](../../reference/environment-complete.md).

`server/config.py` wraps overlapping server-specific settings (`NLS_PRODUCT_MODE`, etc.).

---

## Imported by

Nearly every `nls` package and `server/main.py` startup.

---

## Related

- [Product mode](../../configuration/product-mode.md)
- [Configuration index](../../configuration/index.md)
