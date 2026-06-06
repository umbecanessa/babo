# Vision worker (ambient perception)

Ambient desktop awareness uses a **small VLM** (Moondream-class), not the chat LLM. This document defines the **three deployment tiers**, the **HTTP contract**, and the **GX10 container** we will add after desktop onboarding is wired.

**Related:** [Capability profiles & onboarding](capability-profiles-and-onboarding.md#vision-strategy-when-is-moondream-needed)

---

## Agreed product model

| User setting | Inference (cognition) | Ambient vision (perception) |
|--------------|-------------------------|-----------------------------|
| Ambient vision **off** | GX10 / cloud / local (user choice) | **Off** — no Moondream subprocess, no LAN calls |
| Ambient vision **on** | Unchanged (e.g. Qwen3.6 on GX10) | **Small VLM** via one of the tiers below |

On-demand screenshots in chat still go to the **multimodal chat model** when available. Moondream is only for the **background loop** (Visual Cortex).

---

## Three tiers (same API, different host)

```text
                    ┌─────────────────────────────────────┐
                    │  Babo desktop (Visual Cortex)        │
                    │  • Screen capture (local)            │
                    │  • Frame diff + rate limit           │
                    │  • Calls vision backend below        │
                    └──────────────┬──────────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
  ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
  │ Tier 1       │        │ Tier 2       │        │ Tier 3       │
  │ self_local   │        │ self_lan     │        │ hosted_babo  │
  │ Subprocess   │        │ GX10 / homelab│       │ Babo cloud   │
  │ Moondream    │        │ container    │        │ vision pool  │
  │ on desktop   │        │ Moondream    │        │ (future)     │
  └──────────────┘        └──────────────┘        └──────────────┘
```

| Tier | Placement | Who | Typical user |
|------|-----------|-----|--------------|
| **1 — Local subprocess** | `dedicated_vlm_local` | Moondream in `vlm_worker` child process on PC | RTX 4080+, ambient on |
| **2 — LAN vision worker** | `dedicated_vlm_lan` | Docker on GX10 (or NUC) | Weak laptop + GX10 hub; no local PyTorch |
| **3 — Hosted** | `hosted_babo` | Babo-operated `/vision/*` | No GPU; opt-in to our remote |

Desktop **always** captures pixels locally (privacy). Only **JPEG + describe** crosses the wire for tiers 2 and 3.

---

## HTTP contract (all tiers)

Implemented today for tier 2 client: `nls/tools/visual_model.py` → `RemoteVLMBackend`.

| Method | Path | Body | Response |
|--------|------|------|----------|
| `POST` | `/vision/describe` | `image_base64` (form or JSON) | `{ "description": str, "ocr_text": str }` |
| `POST` | `/vision/ask` | `image_base64`, `question` | `{ "answer": str }` (optional) |
| `GET` | `/health` | — | `{ "ok": true, "model": "...", "device": "..." }` |

Auth (LAN + hosted):

```http
Authorization: Bearer <JWT or nlsk_ key>   # Babo Cloud /api/gpu
X-GPU-Worker-Secret: <shared secret>       # LAN homelab workers
```

When `NLS_GPU_WORKER_URL` points at Babo Cloud (`api.babo.agency`, `/api/gpu`), `RemoteVLMBackend` sends the same Bearer as chat inference. Desktop syncs JWT into `NLS_INFERENCE_API_KEY` automatically.

**LAN tier (`dedicated_vlm_lan`):** Visual Cortex does **not** spawn a local Moondream subprocess — only the remote worker is used, so weak laptops do not reserve desktop GPU VRAM for ambient vision.

Env on desktop runtime when tier 2/3:

```env
NLS_GPU_WORKER_URL=http://192.168.68.96:PORT   # base URL, no path
NLS_GPU_WORKER_SECRET=...
```

Visual Cortex: `strategy: dedicated_vlm_lan` + `enabled: true` in `visual_cortex.json`.

**Note:** Transcribe on GX10 (`:4443`) is a **separate** service today. Vision worker should use its **own port** (e.g. `8450`) so vLLM memory is not contested.

---

## Reference home setup (Umberto)

```text
Windows desktop (Babo)
  inference.self_lan  →  http://192.168.68.96:8000/v1  (vLLM Qwen3.6-35B-A3B-FP8)
  transcribe.self_local → faster-whisper on desktop
  visualCortex:
    ambient OFF     → strategy off, no Moondream
    ambient ON      → strategy dedicated_vlm_local (Moondream on 4080)
                      OR dedicated_vlm_lan once GX10 container exists

GX10 (192.168.68.96)
  :8000  vllm-dev     — chat / tools / sleep (keep as today)
  :4443  pr-whisper   — optional LAN transcribe (desktop prefers local STT)
  :8450  babo-vision  — PLANNED Moondream container (tier 2 for LAN clients)
```

When the GX10 vision container is live, you can move ambient from desktop to LAN (same MoE + centralized VLM) without changing the desktop app — only URL + strategy.

---

## Tier 1 — Local subprocess (implemented)

- **Code:** `SubprocessVLMBackend` → `nls/tools/vlm_worker.py` → `select_backend()` (Moondream if CUDA ≥ 6 GB).
- **Onboarding:** If ambient on + `vram_gb >= 6` → recommend `dedicated_vlm_local`.
- **Prefetch:** `nls/scripts/prefetch_moondream.py` during desktop venv setup.

No extra container.

---

## Tier 2 — GX10 Moondream container (planned)

### Goals

- Run **Moondream 2B** (or SmolVLM fallback) in isolation from vLLM.
- Expose `/vision/describe` + `/health` on a dedicated port.
- Leave `:8000` RAM for Qwen3.6 only.

### Planned layout

```text
deploy/gx10/
  docker-compose.vision.yml   # babo-vision service
  vision-server/              # thin FastAPI wrapper (future)
```

Sketch (not deployed yet):

```yaml
# deploy/gx10/docker-compose.vision.yml (planned)
services:
  babo-vision:
    image: babo/vision-worker:latest   # build from deploy/gx10/vision-server
    ports:
      - "8450:8450"
    environment:
      - VISION_MODEL=moondream2
      - VISION_DEVICE=cuda   # or cpu on GB10 if appropriate
      - VISION_SECRET=${BABO_VISION_SECRET}
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    # Do not share network namespace with vllm-dev
```

**GB10 note:** Validate Moondream on aarch64 + Blackwell; if problematic, container defaults to **SmolVLM** with same API.

### Discovery

Onboarding LAN probe:

```text
GET http://{gx10}:8450/health  → kind: vision
```

User maps to `visualCortex.tier = self_lan`, `url = http://192.168.68.96:8450`.

---

## Tier 3 — Babo hosted vision (planned)

Same contract as tier 2, public HTTPS endpoint, auth via NestJS-issued token or API key.

| Concern | Approach |
|---------|----------|
| Quota | Per-user rate limit (frames/min) |
| Privacy | Opt-in; clear that frames leave device |
| Fallback | If hosted down → tier 1 if GPU allows, else ambient off |

Env:

```env
NLS_GPU_WORKER_URL=https://vision.babo.example
NLS_GPU_WORKER_SECRET=<token>
```

---

## Visual Cortex config mapping

| User: ambient vision | Strategy | `visual_cortex.json` | Runtime |
|----------------------|----------|----------------------|---------|
| Off | `off` | `"enabled": false` | No VLM load |
| On, local GPU | `dedicated_vlm_local` | `"enabled": true`, `"model_preference": "auto"` | Subprocess only |
| On, LAN worker | `dedicated_vlm_lan` | `"enabled": true` | `NLS_GPU_WORKER_URL` + `RemoteVLMBackend` |
| On, hosted | `hosted_babo` (tier) | `"enabled": true` | Same env as LAN |

`RemoteVLMBackend` is tried when local is weak (SmolVLM) or user forces LAN; see `visual_cortex.py` `_skip_local` logic.

---

## Implementation phases

| Phase | Deliverable | Status |
|-------|-------------|--------|
| **A** | Document contract + tiers (this file) | Done |
| **B** | Onboarding: ambient toggle + strategy; registry multimodal | Planned |
| **C** | `capabilityProfile` → env; `VisualCortex(gpu_worker_url=...)` | Planned |
| **D** | `deploy/gx10/vision-server` FastAPI + Dockerfile | Planned |
| **E** | Run `babo-vision` on GX10 `:8450`; LAN probe in wizard | Planned |
| **F** | Babo hosted vision + NestJS auth | Future |

---

## What not to do

- Do **not** route ambient frames to vLLM Qwen on `:8000` every 1–3 s.
- Do **not** share one container for vLLM + Moondream without memory limits (OOM on 128 GB unified when LLM is full).
- Do **not** enable subprocess Moondream when ambient is off.

---

## Related

- [Transcribe & GPU worker](../configuration/transcribe-and-gpu-worker.md) — same `NLS_GPU_WORKER_*` env name; prefer **separate ports** for vision vs whisper
- [Capability profiles](capability-profiles-and-onboarding.md)
- `nls/tools/visual_model.py` — `RemoteVLMBackend`, `MoondreamBackend`
