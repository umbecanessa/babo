# Capability profiles & onboarding

Babo’s “full experience” (chat, agentic loop, voice, desktop awareness, codebase search, sleep) is built from **several independent model workloads**. Onboarding should **scan**, **recommend**, and let users **mix placements** per workload—without forcing one all-local or all-cloud bundle.

---

## Design goals

| Goal | Meaning |
|------|---------|
| **Local-first** | Prefer running on the user’s machine or their own LAN server (GX10, homelab vLLM, Ollama on NAS). |
| **Composable** | Inference, screen VLM, Whisper, and embeddings are **separate choices**. |
| **Honest tiers** | Recommend smaller local models when VRAM/RAM is tight; offer hosted upgrade when local is insufficient. |
| **Full experience** | Every feature has a defined minimum placement; onboarding never silently drops voice/vision without explaining. |
| **BYOK + hosted** | Users can use their API keys (OpenRouter, OpenAI) or Babo-hosted endpoints when we offer them. |

**“Local”** is not only the laptop: **`self_lan`** (e.g. `http://192.168.68.96:8000`) counts as user-controlled local infrastructure.

---

## The four workloads

| Workload | User-facing feature | Typical stack | Config surface |
|----------|---------------------|---------------|----------------|
| **inference** | Chat, tools, sleep consolidation | OpenAI-compatible `/v1/chat/completions` | `NLS_VLLM_BASE_URL`, `NLS_HF_MODEL`, `NLS_INFERENCE_API_KEY` |
| **visual_cortex** | Ambient desktop awareness, `eyes`, `screenshot` | Moondream / SmolVLM / FastVLM subprocess, or remote `/vision/describe` | `data/agents/{id}/visual_cortex.json`, `NLS_GPU_WORKER_URL` |
| **transcribe** | Voice input in chat | Whisper (`faster-whisper`) or remote `/transcribe` | `NLS_GPU_WORKER_URL` (proxy) |
| **embeddings** | `semantic_search` | `nomic-embed-code` local or remote `/embed` | GPU worker / optional `sentence-transformers` |

**Chat-model vision** (images in messages to a multimodal MoE) uses **inference**, not Visual Cortex. See [Vision strategy](#vision-strategy-when-is-moondream-needed) below — **do not recommend Moondream when the chat model already covers on-demand vision and the user does not need ambient desktop capture.**

---

## Vision strategy: when is Moondream needed?

### Two different jobs

| Job | Mechanism | User trigger | Typical model |
|-----|-----------|--------------|---------------|
| **On-demand vision** | Image attached in chat, `vision` tool on a file, user asks “what’s in this screenshot?” | Explicit | **Chat LLM** (multimodal) or small VLM / vision worker |
| **Ambient desktop awareness** | Background capture → description + OCR → ring buffer → `eyes` / thalamus | Automatic (polling) | **Visual Cortex** (Moondream, SmolVLM, FastVLM) **or** LAN vision worker **or** *future* batched calls to multimodal LLM |

If the served chat model is multimodal (image → text, OCR-quality), it **replaces Moondream for on-demand work**. It does **not** automatically replace Visual Cortex unless you deliberately route ambient frames to that same endpoint (usually too heavy for a 30B MoE every second).

**Rule of thumb:** Multimodal LLM ⇒ default Visual Cortex **off**. Enable VC only if the user wants **proactive desktop watching** or the chat model is **not** multimodal.

### Why keep a small VLM (Moondream-class) at all?

| Reason | Explanation |
|--------|-------------|
| **Non-multimodal LLM** | Text-only chat (many local 7B, some API models). Screen understanding still needs a VLM. |
| **Ambient loop cost** | Firing Qwen3.6-35B-A3B every 1–3 s on every screen change is prohibitive on VRAM and latency. Moondream ~2B (or SmolVLM 256M) is the right **perception** tier. |
| **Separation of concerns** | Perception (cheap, always on) vs cognition (expensive, on demand). |
| **LAN hub pattern** | GX10 runs big LLM; desktop runs Moondream **or** sends JPEGs to a **LAN vision worker** (same or different box). |
| **User choice** | Some users want VC on + multimodal chat for different tasks (e.g. OCR via Moondream, reasoning via MoE). |

When **inference is multimodal** and user does **not** need ambient capture: **Moondream is redundant** — turn VC off.

### Visual Cortex modes (product enum)

Onboarding and `visual_cortex.json` should use an explicit **strategy**, not only `enabled: true/false`:

| Mode | Behavior | When to use |
|------|----------|-------------|
| **`off`** | No background capture; no Moondream subprocess | Multimodal LLM + user only cares about on-demand images |
| **`on_demand_inference`** | No VC loop; `screenshot` / `eyes look` send frame to **chat model** or `vision` tool | Multimodal LLM; occasional screen help |
| **`dedicated_vlm_local`** | Subprocess Moondream / SmolVLM / FastVLM (today’s default) | Text-only LLM, or user wants cheap ambient eyes |
| **`dedicated_vlm_lan`** | `POST /vision/describe` on GPU worker | Weak desktop + LAN vision service |
| **`ambient_via_inference`** | *Future:* rate-limited frame → multimodal LLM on LAN | User insists one model only; accept cost cap (e.g. 1 frame / 30 s) |

Default recommendation logic:

```text
IF inference.multimodal == known_true AND NOT user_wants_ambient_eyes:
    visualCortex.mode = off  (or on_demand_inference)
ELSE IF lan_vision_worker_healthy:
    visualCortex.mode = dedicated_vlm_lan
ELSE IF desktop_can_run_moondream:
    visualCortex.mode = dedicated_vlm_local
ELSE IF inference.multimodal == known_true:
    visualCortex.mode = on_demand_inference
ELSE:
    visualCortex.mode = off + offer hosted / upgrade
```

### We cannot know every model — capability discovery

OpenAI-compatible `/v1/models` often **does not** declare vision. Use a **layered** approach:

| Layer | Source | Confidence |
|-------|--------|------------|
| **1. Curated registry** | Babo ships `nls/config/model-capabilities.json` (patterns + exact ids): `Qwen/Qwen3.6-35B-A3B-FP8` → `{ multimodal: true }`, `llama3.2` → false | High when matched |
| **2. User declaration** | Onboarding: “Does your model accept images in chat?” Yes / No / Not sure | High if user knows |
| **3. Probe request** | One minimal `chat/completions` with a 1×1 test image (or provider-specific models API); parse success vs 4xx | Medium; costs one call |
| **4. Unknown** | Treat as **text-only** for recommendations; show both paths in UI | Safe default |

Store on the profile:

```json
"inferenceCapabilities": {
  "multimodal": "true" | "false" | "unknown",
  "source": "registry" | "user" | "probe" | "default"
}
```

Recommendations **must not** load Moondream when `multimodal: true` unless user checks **“Keep desktop awareness (background capture)”**.

### Combination matrix (inference × visual cortex)

| Chat LLM | User wants ambient desktop? | Recommended VC |
|----------|----------------------------|----------------|
| Multimodal (Qwen-VL, GPT-4o, your GX10 MoE) | No | **off** — use chat + `screenshot` → inference |
| Multimodal | Yes | **dedicated_vlm_local** or **lan** (do **not** spam full MoE per frame) |
| Text-only | No | **off**; file images via none unless they add a VLM |
| Text-only | Yes | **dedicated_vlm_local** or **lan** |
| Multimodal | Yes, one model only (advanced) | **ambient_via_inference** (future, rate-limited) |

### LAN vision worker vs Moondream on desktop

Same **contract** (`/vision/describe`): implementation can be Moondream, SmolVLM, or a small VL on GX10. Desktop does not care — only URL + secret. That unifies “my server” tier for users who do not want PyTorch on the laptop.

**Deployment design (three tiers):** local subprocess → GX10 Docker (`:8450`) → Babo hosted. See **[Vision worker](vision-worker.md)**.

**Agreed default for multimodal + ambient:**

- Inference on GX10 (Qwen3.6); ambient **off** by default.
- Ambient **on** → Moondream **locally** on desktop (`dedicated_vlm_local`) until GX10 `babo-vision` container exists; then optional `dedicated_vlm_lan`.
- Everyone else without GPU → **hosted_babo** vision (future).

### Offering hosted vision

When user enables ambient but has no local GPU and no LAN worker:

- **hosted_babo** `/vision/describe` (future), or  
- upsell multimodal inference for on-demand-only (`on_demand_inference`).

---

## Placement model (per workload)

Each workload has a **placement** with three fields:

```text
placement = {
  tier:   self_local | self_lan | hosted_babo | byok_cloud | off
  url?:   base URL when tier needs one
  model?: model id when tier needs one
}
```

| Tier | Description | Who pays / runs |
|------|-------------|-----------------|
| **`self_local`** | Process on the Babo desktop runtime machine (subprocess VLM, local Whisper, Ollama on same PC). | User hardware |
| **`self_lan`** | User’s other machine on the network (GX10, NUC, Docker host). | User hardware |
| **`hosted_babo`** | Babo-operated endpoint (future: proxied inference, vision, STT). | Babo subscription / quota |
| **`byok_cloud`** | User’s cloud API key (OpenRouter, OpenAI, Azure, etc.). | User provider bill |
| **`off`** | Feature disabled (with UI explanation). | — |

**Priority when recommending:** `self_local` → `self_lan` (if probe succeeds) → `byok_cloud` (if key present) → `hosted_babo` (if entitled) → suggest `off` only with explicit consent.

---

## Hardware tiers (desktop scan)

Scan inputs (Electron / Python): `platform`, `ram_gb`, `vram_gb`, `gpu_name`, `has_cuda`, `has_mps`, `has_mlx_vlm`, optional LAN probes.

| Tier | Typical hardware | Inference (local) | Visual Cortex (local) | Whisper (local) | Embeddings (local) |
|------|------------------|-------------------|------------------------|-----------------|---------------------|
| **A — Workstation** | ≥24 GB VRAM or ≥64 GB RAM + strong GPU | Large MoE / 32B+ on LAN or local vLLM | **Moondream** (CUDA ≥6 GB) | CUDA **faster-whisper** | Yes |
| **B — Enthusiast** | 12–16 GB VRAM (e.g. RTX 4080), 32–64 GB RAM | 7B–14B local or **LAN hub** | **Moondream** or SmolVLM | CUDA or CPU whisper | Yes (CPU ok) |
| **C — Thin GPU** | 6–8 GB VRAM | 7B–8B local | **SmolVLM** | CPU whisper | Optional |
| **D — Apple Silicon** | M1/M2/M3 unified memory | Ollama / MLX / LAN | **FastVLM** (mlx) or SmolVLM | CPU whisper | Optional |
| **E — CPU only** | No usable GPU | **Not recommended** local LLM | SmolVLM (slow) or **off** | CPU whisper (slow) | Off or remote |
| **F — Hub only** | Weak laptop + **GX10 / homelab** | **`self_lan`** MoE | **off** or LAN vision worker | **self_local** on laptop (mic latency) | **`self_lan`** |

Tier **F** matches a common pattern: weak UI machine + powerful LAN inference box.

---

## Recommendation rules (v1)

### Inference

| Condition | Recommend |
|-----------|-----------|
| LAN probe finds vLLM/Ollama (`GET /v1/models`) | **`self_lan`** + discovered model id |
| `vram_gb >= 24` | **`self_local`** medium/large quant or LAN |
| `vram_gb >= 12` | **`self_local`** 7B–14B **or** `byok_cloud` small + “upgrade for 32B+” |
| `vram_gb < 8` or tier E | **`byok_cloud`** or **`hosted_babo`**; optional tiny Ollama with consent |
| User has API key only | **`byok_cloud`** |

Always run **Test Connection** (latency + model list).

### Visual Cortex

| Condition | Recommend |
|-----------|-----------|
| `inferenceCapabilities.multimodal === true` and user did **not** opt into ambient eyes | **`off`** or **`on_demand_inference`** — **do not** install Moondream |
| Multimodal **`self_lan`** MoE (e.g. GX10 Qwen3.6) + no ambient eyes | **`off`** (your home-lab default) |
| Multimodal + user wants ambient eyes | **`dedicated_vlm_local`** or **`dedicated_vlm_lan`** — small VLM, not full MoE per frame |
| `inferenceCapabilities.multimodal === false` + `vram_gb >= 6` + CUDA | **`dedicated_vlm_local`** Moondream |
| `inferenceCapabilities.multimodal === false` + thin GPU | SmolVLM |
| MPS + mlx-vlm | FastVLM |
| `multimodal === unknown` | Ask user; default **`off`** until they confirm |
| Tier E or battery saver | **`off`** or LAN/hosted vision worker |

### Transcribe

| Condition | Recommend |
|-----------|-----------|
| Default | **`self_local`** `faster-whisper` (privacy + latency for mic) |
| `vram_gb >= 6` | **`self_local`** CUDA |
| Very weak CPU | Offer **`self_lan`** or **`hosted_babo`** `/transcribe` |
| User opts out of local models | **`self_lan`** (e.g. GX10 `:4443`) or **`hosted_babo`** |

### Embeddings

| Condition | Recommend |
|-----------|-----------|
| Developer / large repo | **`self_local`** if `sentence-transformers` installed |
| Otherwise | **`off`** until needed, or **`self_lan`** `/embed` |
| No local + indexing wanted | **`hosted_babo`** or **`self_lan`** |

---

## Full experience checklist

| Feature | Requires | Minimum placement |
|---------|----------|-------------------|
| Chat + tools | inference ≠ off | any tier except off |
| Sleep consolidation | inference | same as chat |
| Voice input | transcribe ≠ off | local or remote worker |
| Desktop “eyes” / screenshot tool | visual_cortex ≠ off **or** inference multimodal + manual screenshot path | VC local/LAN/hosted |
| Image file analysis | inference multimodal **or** vision tool + worker | inference or GPU worker routes |
| Semantic codebase search | embeddings ≠ off | local or `/embed` worker |

Onboarding UI should show **green / amber / off** per row after scan.

---

## Example profiles (reference)

### P1 — Home hub (GX10 + Windows desktop)

**Scan:** Desktop RTX 4080 16 GB, 64 GB RAM; GX10 `Qwen3.6-35B-A3B-FP8` on `:8000`; Whisper on GX10 `:4443` optional.

| Workload | Placement | Notes |
|----------|-----------|-------|
| inference | **`self_lan`** `http://192.168.68.96:8000/v1` | Primary MoE; on-demand images via chat |
| visual_cortex | **`off`** default; **`dedicated_vlm_local`** if ambient on | Moondream on 4080 — **not** Qwen every 1–3 s; LAN `:8450` later |
| transcribe | **`self_local`** | Mic on desktop; keep GX10 RAM for LLM |
| embeddings | **`off`** or **`self_lan`** | Optional |

### P2 — All-in-one desktop (4080, no LAN server)

| Workload | Placement |
|----------|-----------|
| inference | **`self_local`** Ollama 7B–14B **or** **`byok_cloud`** for quality |
| visual_cortex | **`self_local`** Moondream |
| transcribe | **`self_local`** CUDA |
| embeddings | **`self_local`** |

### P3 — Mac Mini M1 8 GB

| Workload | Placement |
|----------|-----------|
| inference | **`byok_cloud`** or **`self_lan`** |
| visual_cortex | **`off`** or SmolVLM |
| transcribe | **`self_local`** CPU |
| embeddings | **`off`** |

### P4 — Cloud-only user

| Workload | Placement |
|----------|-----------|
| inference | **`byok_cloud`** or **`hosted_babo`** |
| visual_cortex | **`hosted_babo`** or **`off`** |
| transcribe | **`hosted_babo`** |
| embeddings | **`hosted_babo`** or off |

### P5 — Hybrid quality (your upsell pattern)

| Workload | Placement |
|----------|-----------|
| inference | **`hosted_babo`** large model **or** **`byok_cloud`** |
| visual_cortex | **`self_local`** Moondream (user has 16 GB VRAM) |
| transcribe | **`self_local`** |
| embeddings | **`self_local`** |

---

## LAN auto-discovery (onboarding probe)

Suggested probe sequence from desktop:

```text
1. GET {candidate}/v1/models     → inference candidate
2. GET {candidate}/health       → generic worker
3. GET {candidate}/transcribe     → OPTIONS/404 vs OpenAPI on /openapi.json
4. POST {candidate}/vision/describe → vision worker (optional)
5. POST {candidate}/embed         → embedding worker (optional)
```

Store results in `CapabilityScan` (see schema). User picks which discovered URLs map to which workload.

**GX10 example (192.168.68.96):**

| Port | Service | Maps to |
|------|---------|---------|
| 8000 | vLLM OpenAI API | `inference.self_lan` |
| 4443 | pr-whisper `/transcribe` | `transcribe.self_lan` (optional; desktop local preferred) |

---

## Persisted configuration

### Desktop (`nls-config.json` — target shape)

Extend config with a **`capabilityProfile`** block (see `desktop/electron/capability-types.ts` and `nls/config/capability-profile.schema.json`).

`ConfigManager.getRuntimeEnv()` should map placements to env vars:

| Placement | Env vars |
|-----------|----------|
| inference | `NLS_VLLM_BASE_URL`, `NLS_HF_MODEL`, `NLS_INFERENCE_API_KEY` |
| transcribe + visual remote | `NLS_GPU_WORKER_URL`, `NLS_GPU_WORKER_SECRET` |
| visual_cortex off | agent `visual_cortex.json` → `"enabled": false` |
| visual_cortex local | default; `model_preference` from tier |

### Agent-level

`visual_cortex.json` remains per-agent (privacy, fps). Global profile sets defaults for new agents.

---

## Hosted Babo offerings (future)

For personas, BYO vs hosted brain, email/Google credentials, and pricing direction, see **[Babo Cloud personas & commercial design](babo-cloud-personas-and-commercial-design.md)**.

When `hosted_babo` is selected:

| Endpoint | Purpose |
|----------|---------|
| `https://inference.babo.example/v1` | Proxied models (tiers: fast / standard / large) |
| `https://gpu.babo.example` | `/transcribe`, `/vision/describe`, `/embed` |
| API key or session from NestJS | Auth + quota |

Same placement enum keeps BYOK and hosted parallel—user swaps tier without reinstalling.

---

## Onboarding UX (recommended steps)

1. **Scan this device** — RAM, GPU, CUDA/MPS, disk, optional mic permission.
2. **Scan network** — user enters IP/hostname or mDNS; probe GX10-like services.
3. **Recommend profile** — show matrix with toggles (not a single radio).
4. **Per workload card** — Local / My server / Babo hosted / My API key / Off.
5. **Test** — inference ping, 1 s transcribe, optional VC frame.
6. **Apply** — write config + env + default `visual_cortex.json`.

Copy example: *“Your RTX 4080 can run Moondream locally for desktop awareness while using your GX10 for chat. Or turn off desktop vision and send screenshots to your Qwen model.”*

---

## Implementation checklist (engineering)

| Item | Status |
|------|--------|
| Capability schema + TS types | See `nls/config/capability-profile.schema.json`, `desktop/electron/capability-types.ts` |
| `getRuntimeEnv()` maps `gpuWorkerUrl`, placements | TODO |
| `VisualCortex(..., gpu_worker_url=)` from env in `factory.py` | TODO |
| `scripts/probe-capabilities` (Win + optional SSH) | TODO |
| Setup wizard workload cards | TODO |
| NestJS hosted inference/vision routes | Future |
| Vision worker on GX10 (if not using MoE for screen) | User ops / future |

---

## Related

- [Deployment topologies](deployment-topologies.md)
- [Inference providers](../configuration/inference-providers.md)
- [Transcribe & GPU worker](../configuration/transcribe-and-gpu-worker.md)
- [Desktop configuration](../configuration/desktop.md)
