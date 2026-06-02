# Product mode

**Product mode** is the default open-source profile: bring-your-own inference and LLM-based sleep consolidation.

**Source:** `server/product_mode.py`, `server/config.py` (`NLS_PRODUCT_MODE`)

---

## Enable / disable

| Variable | Default | Effect |
|----------|---------|--------|
| `NLS_PRODUCT_MODE` | `1` | Open-source defaults apply |

```bash
# Desktop and local dev (recommended)
NLS_PRODUCT_MODE=1
```

Leave at `1` unless you maintain a private fork with non-standard model hooks.

---

## Behavioral matrix

| Area | Product mode ON (`1`) |
|------|----------------------|
| Default genesis | `standard-v1` |
| Inference | OpenAI-compatible HTTP only |
| Sleep | LLM consolidation (`SleepScheduler`) |
| GPU worker | Optional BYO (`NLS_GPU_WORKER_URL`) |

`apply_product_defaults()` runs at settings load when `product_mode` is true.

---

## Components that read product mode

| Component | File | Notes |
|-----------|------|-------|
| `DualModelManager` | `server/services/dual_model_manager.py` | Standard inference paths only |
| `SleepScheduler` | `server/services/sleep_scheduler.py` | Consolidation-only sleep |
| Startup log | `server/main.py` | Logs `Product mode: True/False` |

---

## Desktop

Electron sets `NLS_PRODUCT_MODE=1` implicitly when spawning the Python sidecar. See [Desktop configuration](desktop.md).

---

## Related

- [Environment variables](environment-variables.md)
- [Inference providers](inference-providers.md)
- [Product scope](../development/product-scope.md)
