# Babo Cloud: Railway (Nest) ↔ GX10 (GPU workers)

Railway runs the **control plane** (NestJS + Postgres). The GX10 runs **GPU/CPU workers** that Railway reaches over the **public internet** (not `192.168.x.x`).

## What you already have

| Service | GX10 port | Public URL (example) |
|---------|-----------|----------------------|
| vLLM (`vllm-dev`) | `8000` | `https://brain.babo.agency` |
| Whisper (`pr-whisper`) | `4443` | `http://stadionweg.mercusysddns.com:4443` |
| Vision (`babo-vision`) | `8443` | `https://brain.babo.agency:8443` |

Local checks on the GX10:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:4443/health
curl -s http://127.0.0.1:8443/health
```

## Railway variables to add (Babo Cloud relay)

Keep secrets only in Railway — never commit them.

| Variable | Purpose |
|----------|---------|
| `BABO_CLOUD_MODE` | `true` — enables entitlements + relay |
| `SECRETS_ENCRYPTION_KEY` | 32+ char random string for BYOK provider keys |
| `INFERENCE_UPSTREAM_URL` | Public vLLM base, e.g. `https://brain.babo.agency` |
| `INFERENCE_UPSTREAM_API_KEY` | Only if vLLM/nginx requires a bearer key |
| `GPU_TRANSCRIBE_UPSTREAM_URL` | Public Whisper base, e.g. `http://stadionweg.mercusysddns.com:4443` |
| `GPU_VISION_UPSTREAM_URL` | `https://brain.babo.agency:8443` |
| `GPU_UPSTREAM_SECRET` | Same value as `BABO_VISION_SECRET` on GX10 (and optional on Whisper if you add auth) |

Optional fallbacks (if all GPU paths share one host):

| Variable | Purpose |
|----------|---------|
| `GPU_UPSTREAM_URL` | Default GPU host when transcribe/vision URLs are empty |

Existing variables (keep as-is):

- `DATABASE_URL` — Railway internal Postgres URL is correct **inside** Railway.
- `JWT_*`, `RESEND_*`, `PORT=3000`
- `RUNTIME_SHARED_SECRET` — shared with **desktop Python runtime**, not vLLM.

### `RUNTIME_URL` warning

`RUNTIME_URL` must point at the **NLS Python agent runtime** (typically `http://<host>:9222`), **not** Whisper (`4443`) or vLLM (`8000`). If agents fail to connect, fix this first.

## GX10: start vision worker

From this repo on the GX10:

```bash
cd deploy/gx10
export BABO_VISION_SECRET='<same-as-GPU_UPSTREAM_SECRET-on-Railway>'
docker compose -f docker-compose.vision.yml up -d --build
```

Vision uses **TCP 8443** on `brain.babo.agency` (same hostname as vLLM, different port).

## Verify end-to-end

From your laptop (or Railway shell):

```bash
# Direct workers
curl -s https://brain.babo.agency/v1/models
curl -s http://stadionweg.mercusysddns.com:4443/health
curl -s https://brain.babo.agency:8443/health -H "X-GPU-Worker-Secret: <secret>"

# Via Nest (after deploy + API key or JWT)
curl -s https://api.babo.agency/api/gpu/health -H "Authorization: Bearer <nlsk_...>"
curl -s https://api.babo.agency/api/inference/v1/models -H "Authorization: Bearer <nlsk_...>"
```

## Desktop (LAN vs hosted)

| Mode | Brain URL | GPU URL |
|------|-----------|---------|
| LAN | `http://192.168.68.96:8000/v1` | `http://192.168.68.96:8443` (vision), `:4443` (whisper) |
| Hosted Babo Cloud | `https://api.babo.agency/api/inference/v1` | `https://api.babo.agency/api/gpu` |

## Security

If database passwords, Resend keys, or JWT secrets were pasted into chat or tickets, **rotate them in Railway** and update GX10 secrets to match.
