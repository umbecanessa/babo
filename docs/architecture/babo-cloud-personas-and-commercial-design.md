# Babo Cloud: personas, placements & commercial design

**Status:** Living design document — **pre-implementation decisions locked** (2026-05-26).  
**Purpose:** Single source of truth for who runs what where, what Babo bills vs what users bring themselves, and how platform integrations (email, Google) fit the same model.

**Open source vs Babo Cloud:** The repo is self-hostable (same stack as we run). **Babo Cloud** (`api.babo.agency`) is the hosted control plane + optional hosted/resold models. Using Babo-operated services requires a **paid subscription** (no permanent free tier; trial instead — see [Commercial decisions](#commercial-decisions-resolved)).

**Related:**

- [Capability profiles & onboarding](capability-profiles-and-onboarding.md) — four model workloads, tiers, env mapping
- [Production architecture & onboarding](production-architecture-and-onboarding.md) — release map, wizard UX
- [Deployment topologies](deployment-topologies.md) — desktop hub, NestJS relay
- [Channels & webhooks](channels-and-webhooks.md) — email, Telegram, WhatsApp
- [Auth & access](auth-and-access.md) — JWT, `nlsk_` keys, relay secret
- [Google Workspace guide](../guides/integrations/google-workspace.md) · [Email channel guide](../guides/integrations/email.md)

---

## Principles

1. **Local-first** — prefer this machine or my LAN server before cloud.
2. **Composable** — brain, vision, voice, and embeddings are independent choices; so are platform services (email, Google).
3. **Honest metering** — track and bill only where Babo is in the payment or credential path.
4. **Same product, many topologies** — power users, hybrid users, and cloud-only users share one desktop app and one control plane (`api.babo.agency`).

---

## Two planes

```text
┌─────────────────────────────────────────────────────────────────────────┐
│  CONTROL PLANE (Babo Cloud — NestJS + Postgres)                          │
│  Accounts · agents · relay · channels · API keys · settings · (future)   │
│  inference proxy · usage ledger · subscriptions                          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ HTTPS / relay WS
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  DATA PLANE (user machine — Babo Desktop + Python runtime)               │
│  Agent loop · memory · tools · skills · optional local/LAN models        │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ direct HTTP only for self_local / self_lan
                                ▼
                    User GPU · homelab · (Babo Cloud → Nest → GX10 upstream)
```

**Control plane** is always relevant for sign-in, dashboard, relay, and Babo-operated channels.  
**Data plane** stays on the desktop for the default product model; cloud-only users still run the runtime locally but offload model workloads to Babo.

---

## Model workloads (composable)

| # | Workload | User-facing label | Typical stack |
|---|----------|-------------------|---------------|
| ① | `inference` | **Brain (chat model)** | OpenAI-compatible `/v1/chat/completions` |
| ② | `visual_cortex` | **Ambient desktop vision** | Moondream-class VLM or LAN/hosted `/vision/describe` |
| ③ | `transcribe` | **Voice input** | Whisper local, LAN, or hosted `/transcribe` |
| ④ | `embeddings` | **Code search (semantic)** | Local, LAN, or hosted `/embed` |

**On-demand images in chat** use ① when the brain is multimodal; ② is only for background screen capture (`eyes`, ring buffer).

### Placement options (each workload card)

| Tier id | UI label | Meaning |
|---------|----------|---------|
| `self_local` | **This computer** | Runs on the machine with Babo Desktop |
| `self_lan` | **My server (LAN)** | User’s machine on the home network — e.g. GX10 vLLM `:8000`, vision `:8450` (not a cloud API key) |
| `byok_cloud` | **My API key (cloud)** | User’s key for a **hosted API on the internet** — OpenRouter, OpenAI, Anthropic, Groq, etc. **Not** “my LAN box.” |
| `hosted_babo` | **Babo hosted** | Babo-operated endpoint via NestJS (metered) |
| `off` | **Off** | Feature disabled |

**Future tier (brain only):** `babo_resold` or hosted brain with `provider=openai|anthropic|…` — Babo’s provider key, user billed with markup (Cursor-style). Documented below as **resold frontier**.

### Default recommendation: voice

- **Prefer `self_local` for Whisper** when hardware allows (latency, privacy, no marginal cost to Babo).
- Do **not** hard-require local voice: weak devices and cloud-only personas use `self_lan` or `hosted_babo`.

---

## Platform services (non-model)

These are **not** the four workloads but follow the same **Babo-provided vs bring-your-own** pattern.

| Service | What it does | Babo-provided (default) | Bring-your-own (BYO) |
|---------|--------------|-------------------------|----------------------|
| **Email channel** | Agent inbox, send/receive via Resend | NestJS `RESEND_API_KEY` + `RESEND_INBOUND_DOMAIN`; `@inbox.*` aliases per agent (included in Babo Cloud sub) | User’s Resend account → **their domain/aliases** (not Babo inbox domain) |
| **Google Workspace** | Gmail, Calendar, Drive, Sheets via OAuth | Built-in OAuth app (Babo `client_id` / `client_secret` in skill) | User saves own Google Cloud OAuth credentials (`save_credentials` on skill) |
| **Control plane** | Auth, agents, relay, dashboard | `https://api.babo.agency` | Self-hosted NestJS (Railway, etc.) — same code paths, user’s deployment |
| **Messaging channels** | Telegram, WhatsApp, … | Webhooks hit Babo Cloud; delivery via desktop relay | Same; user supplies provider tokens in skill setup |

**Design rule:** For any row, product and billing must know whether the request used **Babo credentials** (billable / quota) or **user credentials** (pass-through or flat subscription only).

### Email (Resend)

- **Today:** Email channel availability is tied to server env (`RESEND_API_KEY`, `RESEND_INBOUND_DOMAIN`) — see [Channels & webhooks](channels-and-webhooks.md).
- **Target:** Per-user Resend BYO — user configures their API key/domain; agent aliases live on **their** infrastructure, not `@inbox.babo.agency`.

### Google Workspace

- **Today:** Skill ships default OAuth app; users connect via UI without creating a Google Cloud project — see [Google Workspace guide](../guides/integrations/google-workspace.md).
- **Target:** Advanced users override `client_id` / `client_secret` on the skill (already described in skill metadata). Metering is **not** per Google API call for v1; cost is dominated by **brain** usage when the agent reads/summarizes mail. Optional: track API call volume for abuse limits.

---

## Three personas (archetypes)

Real users mix placements; these are **defaults** for onboarding recommendations.

### Persona A — Power user (“I own the stack”)

**Profile:** Chunky desktop **or** homelab (e.g. ASUS GX10 with vLLM on LAN).

| Workload | Typical placement |
|----------|-------------------|
| Brain | `self_local` or `self_lan` |
| Vision | `self_local` or `self_lan` (or **off** if multimodal brain + no ambient eyes) |
| Voice | `self_local` (recommended) |
| Embeddings | `self_local`, `self_lan`, or `off` |

| Platform | Typical |
|----------|---------|
| Control plane | Babo Cloud and/or self-hosted NestJS |
| Email / Google | Babo OAuth app or BYO credentials — user choice |
| Inference path | **Direct to LAN/local** — no Nest inference proxy required |

**Babo revenue:** Optional subscription for control plane, channels, support — **not** token margin on local inference.

---

### Persona B — Mid power (“hybrid”)

**Profile:** Enough GPU/RAM for Moondream + Whisper (+ maybe embeddings); **not** for a strong chat model.

| Workload | Typical placement |
|----------|-------------------|
| Brain | `byok_cloud` **or** `hosted_babo` **or** (future) **resold frontier** |
| Vision | `self_local` or `self_lan` |
| Voice | `self_local` |
| Embeddings | `self_local` or `off` |

| Platform | Typical |
|----------|---------|
| Brain billing | User pays OpenRouter/etc. (BYOK) **or** Babo (hosted/resold) |
| Vision/voice | Usually **no** Babo GPU charge |

**Babo revenue:** Hosted/resold brain + optional platform fee; local workloads are a differentiator (privacy, cost) without token billing.

---

### Persona C — Cloud-only (“buy from Babo”)

**Profile:** Thin laptop; no local models.

| Workload | Typical placement |
|----------|-------------------|
| Brain | `hosted_babo` and/or **resold frontier** (OpenAI, Anthropic, …) |
| Vision | `hosted_babo` or `off` |
| Voice | `hosted_babo` |
| Embeddings | `hosted_babo` or `off` |

| Platform | Typical |
|----------|---------|
| Email | Babo Resend (agent `@inbox.*`) unless BYO Resend configured |
| Google | Babo OAuth app unless BYO Google project |
| All hosted model traffic | **Must** go through NestJS proxy (auth, quota, usage rows) |

**Babo revenue:** Primary token/usage margin + subscription; highest metering requirements.

---

## Persona × placement matrix

```text
                 Brain          Vision         Voice          Embeddings
Power (A)        local/LAN      local/LAN/off  local (pref)   local/LAN/off
Mid (B)          BYOK/hosted    local/LAN      local          local/off
Cloud-only (C)   hosted/resold  hosted/off     hosted         hosted/off
```

---

## Brain: four commercial paths

| Path | Tier | User provides | Babo provides | Track usage | Bill user |
|------|------|---------------|-------------|-------------|-----------|
| **Self-hosted** | `self_local`, `self_lan` | URL, model, optional LAN secret | Control plane only | Optional analytics | No inference margin |
| **BYOK frontier** | `byok_cloud` | Provider API key (stored for proxy) | Nest proxy → OpenAI/Anthropic/… | **Required** (collect all usage day one) | User pays provider; Babo may charge platform sub only |
| **Babo inference** | `hosted_babo` | Model id / tier label | Nest proxy → GX10 (single upstream) | **Required** | Yes (credits + overage) |
| **Resold frontier** | `babo_resold` (same proxy) | Model choice in UI | Nest proxy → provider via **Babo** key | **Required** + `upstreamCostCents` | Subscription credits + overage |

**Product stance:** Offer **both BYOK and resold** for major providers (like Cursor): privacy/spend control vs one-click and single invoice.

**Technical stance (resolved):**

| Placement | `NLS_VLLM_BASE_URL` | Through Nest inference proxy? |
|-----------|---------------------|-------------------------------|
| `self_local`, `self_lan` | User’s Ollama / vLLM / GX10 on **their** network | **No** — direct from desktop runtime |
| `hosted_babo` | `https://api.babo.agency/api/inference/v1` | **Yes** → Nest → GX10 |
| `byok_cloud` (OpenAI, Anthropic, …) | Same Babo Cloud inference base URL | **Yes** — user key applied server-side on proxy |
| `babo_resold` | Same | **Yes** — Babo key + credits/overage |

Open-source/self-host users who run **their own NestJS** use the same proxy module pointed at **their** upstream (local GX10, etc.); clients still do not bypass Nest when the deployment is configured for proxied cloud providers.

### BYOK vs LAN (do not confuse)

| Tier | What the user runs | Example | Nest for inference? |
|------|--------------------|---------|---------------------|
| `self_local` | Model on **this PC** | Ollama `127.0.0.1:11434` | **No** — direct |
| `self_lan` | Model on **their server** on the network | Your GX10 `http://192.168.68.96:8000` | **No** — direct |
| `byok_cloud` | **No local model** — they pay a **cloud company** with an API key | OpenRouter, OpenAI, Anthropic | **Yes** — via Nest (default) |
| `hosted_babo` | Babo runs the model | Babo → GX10 | **Yes** |

**OpenRouter** is **not** LAN. It is a website (`openrouter.ai`) that exposes an OpenAI-compatible API; the user pastes `sk-or-v1-...` in setup. That is **`byok_cloud`**, same category as OpenAI/Anthropic, not `self_lan`.

**Resolved:** All `byok_cloud` providers (OpenRouter included) go **through Nest** unless the user chose `self_local` / `self_lan`. There is no special case for OpenRouter.

---

## What must go through NestJS (metering & secrets)

| Traffic | Through Nest? | Why |
|---------|---------------|-----|
| Hosted brain (`hosted_babo`) | **Yes** | Nest → single GX10 upstream; hide infra URL |
| Resold + BYOK frontier (OpenAI, Anthropic, …) | **Yes** | Keys, credits, usage ledger |
| Hosted GPU workers (vision, transcribe, embed) | **Yes** | Nest → GX10 (or same fleet); no direct client → GX10 domain |
| Self/LAN/local brain | **No** | User runs open-source stack; own inference server |
| Desktop relay (`/api/channels/relay`, `/api/rt`) | **Yes** | Already implemented |
| Email send (Resend) | **Yes** | Already via `POST /api/channels/email/send` |
| Google API calls | Desktop/runtime + user OAuth token | Included in Babo Cloud sub; no per-connection fee |

### Upstream topology (Babo Cloud)

```text
Desktop / runtime  ──►  api.babo.agency (Nest)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         GX10 (vLLM)    OpenAI/Anthropic   (future providers)
         inference +     (BYOK user key or
         vision workers   Babo resold key)
```

- **One GX10 fleet URL** for Babo-hosted inference and vision (GX10 may have its own domain today; **clients must not use it directly** for cloud users).
- Nest is the **relayer** for all Babo Cloud model traffic, mirroring the existing channel relay pattern but for OpenAI-compatible HTTP.

### Inference proxy auth: JWT vs `nlsk_`

Support **both** on `/api/inference/*`:

| Auth | Typical caller | Why |
|------|----------------|-----|
| **`Authorization: Bearer nlsk_...`** | Desktop Python runtime (`VLLMInferenceClient`), scripts, **per-agent API keys** | Long-lived automation; works headless; matches existing API key product |
| **JWT (access token)** | Angular web app, setup “Test connection”, settings when user is logged in | User already has session; no need to paste key in browser; good for “dad on full cloud” managing account in UI |

Rules:

- Same user; JWT resolves `userId`; `nlsk_` resolves `userId` + optional `agentId` from key row.
- **Per-agent API keys in v1** — `ApiKey.agentId` required when key is agent-scoped.
- Implement **Postgres validation** for `nlsk_` on Nest proxy; fix Python `validate_api_key` gap for any direct runtime use of keys.

---

## Usage tracking (design)

### Billable events

| Category | Examples | Bill when |
|----------|----------|-----------|
| **Inference** | `chat/completions` — record on **every stream chunk** that includes `usage` (max accuracy) | `hosted_babo`, `babo_resold`, BYOK via Nest |
| **GPU workers** | transcribe, vision/describe, embed | `hosted_babo` for that workload |
| **Platform** | (future) Resend send volume on Babo key | Babo Resend, not BYO Resend |

### Non-billable (v1)

| Category | Examples |
|----------|----------|
| Self/LAN model calls | Direct to Ollama, user’s GX10 |
| Open-source self-host (own Nest + own upstream) | Their metering optional |
| Relay chat / webhooks | Control plane; may be included in subscription |
| Google/telegram/etc. | No per-API micro-billing v1; brain cost dominates |

### Suggested usage record shape (future Prisma)

| Field | Purpose |
|-------|---------|
| `userId`, `agentId?`, `apiKeyId?` | Attribution |
| `workload` | `inference` \| `transcribe` \| `vision` \| `embed` |
| `placement` | `hosted_babo` \| `babo_resold` \| … |
| `provider`, `model` | e.g. `openai`, `gpt-4o` |
| `promptTokens`, `completionTokens`, `totalTokens` | LLM |
| `upstreamCostCents?` | Internal margin for resold |
| `route` | e.g. `chat/completions` |

**Per-agent API keys (v1):** `ApiKey.agentId` + scopes (`inference`, `gpu`, …).  
**Rate limits:** wire `rateLimitRpm` (and future caps) in proxy on day one; tune values after business modeling.  
**Collect everything from day one** — including `upstreamCostCents` for resold — even if pricing UI comes later.

---

## Commercial decisions (resolved)

| Topic | Decision |
|-------|----------|
| **Routing: local vs cloud** | **`self_local` / `self_lan` → direct**, no Nest. **All `byok_cloud`** (OpenRouter, OpenAI, Anthropic, …) **+ `hosted_babo` / resold** → **Nest proxy**. Open-source users self-host the same stack on their own infra. |
| **Resold pricing** | **Subscription credits + overage** (easiest UX). Exact price points later. |
| **Usage data** | **Collect all fields from day one** (`upstreamCostCents`, tokens, per chunk on streams). Decide how to bill later. |
| **Email BYO Resend** | User brings own Resend account → **their aliases/domains**, not `@inbox.babo.agency`. |
| **Google / email connection fee** | **No** separate charge for connecting Google Workspace or email. Included in **minimum Babo Cloud subscription** (low monthly fee TBD, e.g. ~$2.99 — not final). |
| **Free tier** | **No permanent free tier** — product is open source; users can self-host for $0. Babo Cloud is paid. |
| **Trial** | **Classic ~1 month trial** preferred over a capped free tier (simpler than ongoing abuse limits). |
| **Pricing shape** | **Monthly subscription + overage** on hosted/resold usage (credits included in sub, then overage). |
| **Composable SKUs** | Start simple (one low base sub includes platform + connections); composable add-ons can come later. |
| **GX10** | **Single upstream** for Babo-hosted inference/vision; Nest relays — **no per-customer GX10 URL**. |
| **Self-hosted Nest** | Supported — user runs Nest on laptop or VPS with their own `INFERENCE_UPSTREAM_*` (same code as Babo Cloud). |
| **Per-agent API keys** | **Yes, v1** |
| **API key validation** | **Implement** on Nest proxy + close Python gap |
| **Stream metering** | Persist/update usage on **every stream chunk** that carries `usage` |
| **Rate limits** | **Wire in** at launch; adjust RPM/caps with business case |

---

## Babo Cloud pricing (structure — amounts TBD)

### Minimum subscription (monthly)

Required to use **Babo Cloud** services (hosted models, Babo Resend inbox, default Google OAuth app, relay, dashboard):

- Low entry price (team to set; ~$2.99 discussed as order-of-magnitude only)
- Includes platform access; **not** a separate fee to “connect” Google or email

### Usage (hosted + resold)

- **Included credits** per billing period (subscription)
- **Overage** when credits exhausted
- BYOK frontier: user pays provider directly; Babo still meters through Nest for quota/abuse (platform sub still required for Babo Cloud)

### Trial

- ~**1 month** full or partial access — exact entitlements TBD
- No always-free hosted tier

### Enterprise / BYO credentials

- Own Resend domain + API key → their aliases
- Own Google Cloud OAuth app
- Optional self-hosted NestJS control plane  
→ Contract pricing; same placement model.

---

## Onboarding implications

1. **Scan device + LAN** — recommend a persona, not a single radio button.
2. **Four workload cards** + optional **platform** section (email, Google) explaining Babo vs BYO.
3. **Brain card** shows three paths when cloud is relevant: My server · My API key · Babo hosted (and later Babo + OpenAI/Anthropic).
4. **Test** per workload: inference `/v1/models`, 1 s transcribe, optional vision frame.
5. **Persist** `capabilityProfile` + platform credential source flags.

See [capability-profiles-and-onboarding.md](capability-profiles-and-onboarding.md) for env mapping and [production-architecture-and-onboarding.md](production-architecture-and-onboarding.md) for wizard steps.

---

## Implementation roadmap (production-ready end-to-end)

Goal: ship **Phases A → D** as one coherent Babo Cloud release (not a partial proxy). Order still matters for development sequencing.

| Phase | Deliverable | Production criteria |
|-------|-------------|---------------------|
| **A** | Nest `inference-proxy` module: `chat/completions`, `models`, JWT + `nlsk_` auth, usage table, rate-limit hooks, GX10 upstream env | Desktop `hosted_babo` points at `api.babo.agency`; stream usage per chunk |
| **B** | GPU proxy routes: transcribe, vision, embed → same GX10 fleet via Nest | Hosted voice/vision/embed in capability profile |
| **C** | Resold + BYOK frontier: store user provider keys securely; route OpenAI/Anthropic through proxy; `upstreamCostCents` | Settings/UI can pick provider; credits/overage backend stub |
| **D** | Per-agent API keys, per-user Resend BYO, trial + subscription (Stripe), entitlements | Email BYO aliases; trial gate; min sub enforced for cloud features |

**Also in v1:** `ApiKeysService.validateKey()` used by proxy; Python `validate_api_key` implemented or delegated to Nest for cloud paths.

---

## Implementation checklist (cross-reference)

| Item | Doc / code | Status |
|------|------------|--------|
| Capability schema + TS types | `nls/config/capability-profile.schema.json`, `desktop/electron/capability-types.ts` | Shipped |
| NestJS inference + GPU proxy | `backend/src/babo-cloud/` | **Shipped** on `feature/babo-cloud-inference-proxy` |
| Per-agent API keys + validation | `api-keys` + `babo-cloud` guards | **Shipped** |
| Per-user Resend BYO | `provider-keys` + `channels` | **Shipped** |
| Resold + BYOK frontier routing | `provider-keys` + inference proxy | **Shipped** (OpenAI, OpenRouter) |
| Subscription trial + token ledger | `cloud_subscriptions` + `inference_usage` | **Shipped** (Stripe webhook TBD) |
| Usage ledger + stream chunk writes | Prisma `InferenceUsage` (TBD) | Planned |
| `hosted_babo` / cloud brain env | `capabilityProfileToRuntimeEnv`, setup wizard | Planned |
| Per-agent API keys + validation | `api-keys` module, `auth.py` | Planned |
| Per-user Resend BYO | Channels / settings | Planned (D) |
| Resold + BYOK frontier routing | Inference proxy | Planned (C) |
| Subscription, trial, credits/overage | Stripe + entitlements | Planned (D) |
| Self-host docs for Nest upstream env | `docs/configuration/` | Planned |

---

## Open questions (remaining)

Minor — safe to implement without blocking:

1. **Trial entitlements** — full credits vs reduced caps during trial month?
2. **Exact subscription price** and included credit amounts.
3. **Composable add-on SKUs** — when to split vision/embed vs all-in-one sub?

Record answers in [Commercial decisions](#commercial-decisions-resolved) when set.

---

## Revision log

| Date | Change |
|------|--------|
| 2026-05-26 | Initial design: personas, workloads, platform services, billing direction |
| 2026-05-26 | Locked pre-implementation decisions: routing, auth, GX10 relay, pricing shape, roadmap A–D |
| 2026-05-26 | Clarified BYOK vs LAN; all `byok_cloud` (incl. OpenRouter) via Nest |
