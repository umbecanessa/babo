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
| POST | `/api/cloud/subscription/activate` | JWT (stub billing) |
| PUT | `/api/cloud/providers/inference/:provider` | JWT |
| PUT | `/api/cloud/providers/resend` | JWT |

Requires active **trial or subscription** when `BABO_CLOUD_MODE=true`.

---

## Environment

See `backend/.env.example` — `INFERENCE_UPSTREAM_URL`, `GPU_UPSTREAM_URL`, `BABO_CLOUD_MODE`, `SECRETS_ENCRYPTION_KEY`.

---

## Desktop env

`hosted_babo` / `byok_cloud` → `NLS_VLLM_BASE_URL={nestjs}/api/inference/v1`  
Hosted GPU workloads → `NLS_*_WORKER_URL={nestjs}/api/gpu`

See [Cloud deployment](../../configuration/cloud-deployment.md).
