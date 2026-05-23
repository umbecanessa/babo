# Product mode

**Product mode** is the default OSS profile: bring-your-own inference, consolidation sleep, and no lab-only training pipelines.

**Source:** `server/product_mode.py`, `server/config.py` (`NLS_PRODUCT_MODE`)

---

## Enable / disable

| Variable | Default | Effect |
|----------|---------|--------|
| `NLS_PRODUCT_MODE` | `1` | When true, OSS-friendly defaults apply |

```bash
# Desktop and local dev (recommended)
NLS_PRODUCT_MODE=1
```

Set to `0` only if you maintain a custom lab fork with training hooks restored.

---

## Behavioral matrix

| Area | Product mode ON (`1`) | Product mode OFF |
|------|----------------------|------------------|
| Default genesis | `standard-v1` (not `moe-v1` / `32b-v5`) | Uses `default_genesis` as configured |
| Inference | OpenAI-compatible HTTP only | May enable custom model paths in forks |
| Sleep | LLM consolidation (`SleepScheduler`) | Same API; lab forks may add weight training |
| Training / curricula | Not shipped in this repo | Out of scope — see [Product scope](../development/product-scope.md) |
| GPU worker | Optional BYO (`NLS_GPU_WORKER_URL`) | Same — optional acceleration |

`apply_product_defaults()` runs at settings load when `product_mode` is true.

---

## Components that read product mode

| Component | File | Notes |
|-----------|------|-------|
| `DualModelManager` | `server/services/dual_model_manager.py` | Skips lab model loading paths |
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
