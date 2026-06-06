# Babo Cloud module

**Path:** `backend/src/babo-cloud/`

Optional NestJS relay for **inference**, **GPU workers**, **BYOK provider keys**, and **subscriptions**. Enable with `BABO_CLOUD_MODE=true` when operating a hosted control plane.

Self-hosters can leave this disabled and use direct OpenAI-compatible endpoints from the desktop runtime.

---

## Routes

| Method | Path | Auth |
|--------|------|------|
| GET | `/api/inference/v1/models` | JWT or `nlsk_` |
| POST | `/api/inference/v1/chat/completions` | JWT or `nlsk_` |
| GET | `/api/gpu/health` | JWT or `nlsk_` |
| POST | `/api/gpu/transcribe` | JWT or `nlsk_` (multipart `audio`) |
| POST | `/api/gpu/vision/describe` | JWT or `nlsk_` |
| POST | `/api/gpu/embed` | JWT or `nlsk_` |
| GET | `/api/cloud/subscription` | JWT — plan + usage |
| GET | `/api/cloud/usage` | JWT — recent usage rows |
| POST | `/api/billing/checkout` | JWT — Stripe Checkout (when operator loaded) |
| POST | `/api/billing/portal` | JWT — Stripe Customer Portal |
| POST | `/api/billing/sync` | JWT — reconcile subscription after checkout |
| PUT | `/api/billing/on-demand` | JWT — enable/disable pay-as-you-go routing |
| PUT | `/api/billing/spend-cap` | JWT — monthly pay-as-you-go spend cap |
| PUT | `/api/cloud/providers/inference/:provider` | JWT |
| PUT | `/api/cloud/providers/resend` | JWT |

Requires active **subscription** when `BABO_CLOUD_MODE=true`. Platform fee is **$4.99/mo**; model usage is BYOK or optional pay-as-you-go at upstream cost (no Babo markup).

---

## Environment

See `backend/.env.example` — `INFERENCE_UPSTREAM_URL`, `GPU_UPSTREAM_URL`, `BABO_CLOUD_MODE`, `SECRETS_ENCRYPTION_KEY`.

---

## Desktop env

`hosted_babo` / `byok_cloud` → `NLS_VLLM_BASE_URL={nestjs}/api/inference/v1`  
Hosted GPU workloads → `NLS_*_WORKER_URL={nestjs}/api/gpu`

### Runtime bearer (desktop)

Nest `CloudAuthGuard` accepts **JWT** (signed-in user) or **`nlsk_`** API keys on inference and GPU routes. The Electron app sends JWT to NestJS from Angular; the **Python runtime** uses `NLS_INFERENCE_API_KEY` only.

`BaboCloudProvisionService` pushes the resolved bearer (priority: stored `nlsk_` → BYOK key → session JWT) and hot-reloads `vllm_client` via `POST /admin/hot-reload`. No manual key paste is required for normal signed-in desktop use.

See [Cloud deployment](../../configuration/cloud-deployment.md) and [Desktop configuration](../../configuration/desktop.md#babo-cloud-inference-auth).
