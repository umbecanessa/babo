# Babo Cloud: personas, placements & commercial design

**Status:** Living design document — **pricing, billing UX, and OSS/operator split locked** (2026-05-29). Routing and personas locked 2026-05-26.  
**Purpose:** Single source of truth for who runs what where, what Babo bills vs what users bring themselves, and how platform integrations (email, Google) fit the same model.

**Open source vs Babo Cloud:** The public **`babo`** repo (MIT) is the full local-first agent platform — self-hostable end-to-end. **Babo Cloud** (`api.babo.agency`) is our optional hosted service (like [Home Assistant Cloud](https://www.home-assistant.io/cloud/) by Nabu Casa): convenience, resold models, and integrations out of the box. **Payments and paid entitlements** live in a **private `babo-operator`** repo, deployed only on our infrastructure — not shipped as a turnkey SaaS kit in open source. See [Open source, operator & Babo Cloud](#open-source-operator--babo-cloud-home-assistant-model).

**Related:**

- [Capability profiles & onboarding](capability-profiles-and-onboarding.md) — four model workloads, tiers, env mapping
- [Production architecture & onboarding](production-architecture-and-onboarding.md) — release map, wizard UX
- [Deployment topologies](deployment-topologies.md) — desktop hub, NestJS relay
- [Channels & webhooks](channels-and-webhooks.md) — email, Telegram, WhatsApp
- [Auth & access](auth-and-access.md) — JWT, `nlsk_` keys, relay secret
- [Google Workspace guide](../guides/integrations/google-workspace.md) · [Email channel guide](../guides/integrations/email.md)
- **[Billing implementation plan](babo-cloud-billing-implementation-plan.md)** — end-to-end OSS/operator split, Stripe, frontend, deploy, QA

---

## Principles

1. **Local-first** — prefer this machine or my LAN server before cloud. Mission: foster local AI; long-term, families run agents on their own hardware without rent to big cloud vendors.
2. **Composable** — brain, vision, voice, and embeddings are independent choices; so are platform services (email, Google).
3. **Honest metering** — track and bill only where Babo is in the payment or credential path.
4. **Same product, many topologies** — power users, hybrid users, and cloud-only users share one desktop app; control plane is **self-hosted Nest** or **`api.babo.agency`** (Babo Cloud).
5. **Complete without cloud** — Babo works fully on your machine or LAN without Babo Cloud (Home Assistant model). Cloud is optional convenience, not lock-in.

---

## Open source, operator & Babo Cloud (Home Assistant model)

Reference: [Home Assistant](https://www.home-assistant.io/) (OSS, local-first) + [Nabu Casa](https://www.nabucasa.com/) (commercial operator: [Home Assistant Cloud](https://www.home-assistant.io/cloud/) subscription + official hardware). Babo follows the same split: **give the full product in open source**; **withhold only the operator kit** that turns Babo into a paid hosted business; **sell convenience and hardware** long-term.

### Mapping

| Home Assistant ecosystem | Babo ecosystem |
|--------------------------|----------------|
| **Home Assistant** (OSS, runs locally) | **`babo`** (MIT) — desktop, runtime, agents, self-host Nest |
| **Nabu Casa** (company behind HA Cloud + hardware) | **Babo Agency** (`babo.agency`) — commercial operator |
| **Home Assistant Cloud** (~$6.50/mo, optional) | **Babo Cloud** ($6.99/mo, optional) — `api.babo.agency` |
| **HA Green / Yellow** (plug-and-play hub) | **Babo Box** (future) — mini PC with Babo OS pre-installed |
| *“HA works fully without cloud”* | *“Babo works fully without Babo Cloud”* |

**Public message (mirror Nabu Casa):**

> Babo runs fully on your computer or home server. Babo Cloud is optional — for remote relay, hosted models, agent email, and integrations without running your own server. Subscriptions fund Babo development.

### Three repositories / deployment layers

```text
babo/                          MIT — public GitHub (this repo)
├── desktop, nls, server, frontend
├── backend/src/babo-cloud/    inference proxy, GPU proxy, usage ledger, BYOK, relay
└── billing interface only     CloudBillingProvider + NoOpBillingProvider

babo-operator/                 PRIVATE — Babo Agency only
├── stripe/                    Checkout, webhooks, Customer Portal, meters
├── entitlements-paid/         trial, credit pool, overage, spend caps
└── Nest module wired at deploy  replaces NoOp on api.babo.agency

api.babo.agency                OUR DEPLOYMENT (not a separate product codebase)
└── babo backend + babo-operator + our secrets (Stripe, PLATFORM_OPENROUTER_API_KEY, Resend, …)
```

**Competitors** may fork **`babo`** (MIT) and self-host or build their own product. They **do not** receive **`babo-operator`** — they must implement payments and paid entitlements themselves to run a Babo Cloud–style SaaS. **Trademark:** “Babo Cloud” is our hosted service name, not a license to use our operator code.

### What stays in open source (`babo`)

Everything built for the product **except** the payment/subscription operator:

| In MIT repo | Notes |
|-------------|--------|
| Desktop app + Python runtime | Local-first data plane |
| Agent loop, memory, projects, teams, integrations | Full agent platform |
| Self-host NestJS | Auth, agents, relay, channels, webhooks |
| `babo-cloud` inference + GPU proxy | BYOK, self LAN upstream, usage **recording** |
| `InferenceUsage` + `upstreamCostCents` | Analytics; operator debits pool in private repo |
| `CloudSubscription` schema + **NoOp** entitlements | `BABO_CLOUD_MODE=false` → no subscription gate |
| `CloudBillingProvider` interface | Hook for operator injection |
| Setup wizard, model picker, capability profiles | Billing UI hidden when `billingEnabled: false` |
| Docs for Raspberry Pi / mini PC / homelab | Long-term Babo Box uses same OSS stack |

### What stays private (`babo-operator`)

Only the **commercial operator kit**:

| In private repo | Notes |
|-----------------|--------|
| Stripe integration | Checkout, webhooks, Portal, metered overage |
| Paid entitlements | Trial cap, `includedCreditCents` debit, overage, spend cap |
| Subscription lifecycle | trialing → active → past_due → canceled |
| Billing UI backend | Enriched `GET /cloud/subscription`, activate flows |
| Platform resold **enforcement** (optional hard line) | Debit pool when using Babo OpenRouter key on Cloud |

Secrets (`STRIPE_*`, `PLATFORM_OPENROUTER_API_KEY`, Babo Resend) remain **env on our deployment**, never in either repo.

### OSS integration pattern

```typescript
// babo/backend — MIT
interface CloudBillingProvider {
  isEnabled(): boolean;
  assertCloudAccess(userId: string): Promise<void>;
  recordUsage(userId: string, upstreamCostCents: number): Promise<void>;
  getSubscriptionView(userId: string): Promise<SubscriptionView>;
}

// Default when babo-operator not loaded
class NoOpBillingProvider implements CloudBillingProvider {
  isEnabled() { return false; }
  async assertCloudAccess() { /* allow — self-host */ }
  // …
}
```

`api.babo.agency` Docker/Railway image: build **`babo`** backend + install/register **`babo-operator`** Nest dynamic module.

Self-hosters: `BABO_CLOUD_MODE=false` (or operator absent) → full Babo, no paywall.

### Revenue arc (same as Home Assistant)

| Phase | Offering | Role |
|-------|----------|------|
| **Now** | Babo Cloud subscription | Optional hosted control plane + resold models; funds development |
| **Mid** | More Cloud integrations | Email inbox, Google OAuth app, relay — convenience bundle |
| **Long** | **Babo Box** hardware | Mini computer with Babo OS flashed; plug-in agents for families; Cloud still optional |

Cloud subscription revenue should **fund the open-source Babo project** (future foundation or explicit “supported by Babo Cloud” messaging, like Nabu Casa → Open Home Foundation).

---

## Two planes

```text
┌─────────────────────────────────────────────────────────────────────────┐
│  CONTROL PLANE (Babo Cloud — NestJS + Postgres)                          │
│  Accounts · agents · relay · channels · API keys · settings · (future)   │
│  inference proxy · usage ledger · (operator: subscriptions & billing)   │
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

### Default recommendation: brain (Babo Cloud resold)

- **Default model:** `google/gemini-2.5-flash` — best value for agentic E2E work on Babo Cloud (see [Empirical cost data](#empirical-cost-data-openrouter-may-2026)).
- **Offer all OpenRouter models** in the picker; **sort and badge** by value (`recommended` → `standard` → `premium`).
- **Do not block** premium models (Sonnet, GPT-4o); show **$/1M input & output** and **relative burn rate** so users know Sonnet consumes included usage ~9× faster than Flash.
- **BYOK on Babo Cloud:** user pays provider directly; Babo Cloud **platform subscription still required**; included usage pool applies only to **`babo_resold`** (Babo key).

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

### Usage record shape (Prisma `InferenceUsage`)

| Field | Purpose |
|-------|---------|
| `userId`, `agentId?`, `apiKeyId?` | Attribution |
| `workload` | `inference` \| `transcribe` \| `vision` \| `embed` |
| `placement` | `hosted_babo` \| `babo_resold` \| `byok_cloud` \| … |
| `provider`, `model` | e.g. `openrouter`, `google/gemini-2.5-flash` |
| `promptTokens`, `completionTokens`, `totalTokens` | LLM (analytics + user breakdown) |
| `upstreamCostCents` | **Authoritative debit** for `babo_resold` / `hosted_babo` billing |
| `route` | e.g. `chat/completions` |

### Subscription ledger (evolve `CloudSubscription`)

| Field | Purpose |
|-------|---------|
| `includedCreditCents` | Monthly included usage pool (e.g. `500` = $5.00) |
| `usedCreditCents` | Consumed upstream cost this period |
| `monthlySpendCapCents?` | On-demand cap (Cursor-style spend limit); `null` = no cap |
| `allowOverage` | Continue after included pool when on-demand enabled |

**Billing unit:** **dollar-equivalent upstream cost** (`upstreamCostCents`), **not** raw token count. Token fields remain for transparency and dashboards; mixed models make token quotas misleading.

**Repo split:** Schema and usage writes stay in **OSS** `babo`. **Debit against `includedCreditCents`**, trial gates, and overage enforcement stay in **`babo-operator`**.

**Per-agent API keys (v1):** `ApiKey.agentId` + scopes (`inference`, `gpu`, …).  
**Rate limits:** wire `rateLimitRpm` (and future caps) in proxy on day one; tune values after business modeling.  
**Inference proxy must populate `upstreamCostCents`** on every usage write (model price table or provider-reported cost).

---

## Commercial decisions (resolved)

| Topic | Decision |
|-------|----------|
| **Routing: local vs cloud** | **`self_local` / `self_lan` → direct**, no Nest. **All `byok_cloud`** (OpenRouter, OpenAI, Anthropic, …) **+ `hosted_babo` / resold** → **Nest proxy**. Open-source users self-host the same stack on their own infra. |
| **Resold pricing** | **$6.99/mo subscription + $5 included API usage + on-demand overage** (Cursor 2026 pattern). |
| **Billing unit** | **`upstreamCostCents`** (dollar pool), not token quota. Tokens tracked for analytics/UI only. |
| **Overage** | **Pay-as-you-go** at published model rates × **1.25** markup after included pool exhausted. User can disable on-demand (hard stop at 100%). |
| **Usage data** | **Collect all fields from day one** (`upstreamCostCents`, tokens, per chunk on streams). |
| **Email BYO Resend** | User brings own Resend account → **their aliases/domains**, not `@inbox.babo.agency`. |
| **Google / email connection fee** | **No** separate charge. Included in Babo Cloud subscription. |
| **Free tier** | **No permanent free tier** — product is open source; users can self-host for $0. Babo Cloud is paid. |
| **Trial** | **No free inference trial** — **$6.99/mo from day one** for Babo Cloud resold models. **31-day refund** on **first subscription payment** only (see below). Self-host remains $0. |
| **Lifetime comp plan** | **Admin-granted only** (`lifetime_comp`) — family/friends; **no Stripe**; **GX10** via `brain.babo.agency`; hidden from public UI unless granted. |
| **Coupons / referrals / affiliates** | **Scaffold in v1** (schema + Stripe hooks); full programs later. |
| **Pricing shape** | **Monthly subscription + included API usage pool + overage** (aligned with [Cursor 2026](https://cursor.com/help/models-and-usage/usage-limits)). |
| **Composable SKUs** | **v1: single plan** (`cloud_basic`). Pro tier / add-ons deferred. |
| **Default resold model** | **`google/gemini-2.5-flash`** — recommended in UI; premium models available with cost visibility. |
| **GX10 / `brain.babo.agency`** | **Hidden from public setup** — not offered on `cloud_basic`. **Lifetime comp** users only (admin flag). Nest → `INFERENCE_UPSTREAM_URL`; no pool debit for comp users. |
| **Stripe / payments** | **Private `babo-operator` repo only** — not in public `babo` |
| **OSS license** | **MIT** for `babo`; operator proprietary (Babo Agency) |
| **Self-hosted Nest** | Full product — `BABO_CLOUD_MODE=false`, no operator module |
| **Per-agent API keys** | **Yes, v1** |
| **API key validation** | **Implement** on Nest proxy + close Python gap |
| **Stream metering** | Persist/update usage on **every stream chunk** that carries `usage` |
| **Rate limits** | **Wire in** at launch; adjust RPM/caps with business case |

---

## Babo Cloud pricing (locked v1)

### Plan: Babo Cloud Basic — **$6.99/month**

Required to use **Babo Cloud** (`api.babo.agency`): resold OpenRouter models, Babo Resend inbox, default Google OAuth app, relay, dashboard.

**No free trial** on resold inference — subscribe to start. Early adopters: pay $6.99, get $5 pool (~one Gemini E2E build; see [Empirical cost data](#empirical-cost-data-openrouter-may-2026)).

| Component | Amount | Notes |
|-----------|--------|-------|
| **Subscription** | **$6.99/mo** | Platform + integrations; no separate “connect Google/email” fee |
| **Included API usage** | **$5.00/mo** | Debited at upstream cost (`includedCreditCents: 500`) |
| **Overage** | **1.25× upstream** | On-demand; billed in arrears via Stripe |
| **Default spend cap** | **$15/mo** on-demand | User-adjustable; `null` = no cap (Cursor-style) |

**Public copy (pricing page):**

> Babo Cloud — **$6.99/month**  
> Includes **$5 of model usage** at standard API rates.  
> Additional usage billed pay-as-you-go.  
> *Recommended models go further; premium models use included usage faster.*  
> **Full refund of your first month’s subscription within 31 days.** Overage and usage beyond your included allowance are not refundable.

**Secondary copy (optional footnote):** ~14M agent tokens equivalent on Gemini 2.5 Flash; ~1.5M on Claude Sonnet. Token equivalents are illustrative — billing is dollar-based.

### What counts against the included pool (paid `cloud_basic` only)

| Placement | Who sees it | Debits included pool? |
|-----------|-------------|------------------------|
| `babo_resold` (OpenRouter) | All paid Cloud users | **Yes** — real upstream cost |
| `hosted_babo` → GX10 (`brain.babo.agency`) | **`lifetime_comp` only** (admin) | **No** — comp; not on public plans in v1 |
| `byok_cloud` | BYOK users on Cloud | **No** — user pays provider |
| `self_local` / `self_lan` | Self-host / desktop | **No** — direct inference |

### No trial (v1)

- Sign up → **Stripe Checkout $6.99/mo** to use Babo Cloud resold models.
- **`$5` included usage** starts with first paid period.
- Self-host path unchanged: **$0**, no Babo Cloud account required.

### 31-day refund (first subscription only)

Like [Home Assistant Cloud](https://www.home-assistant.io/cloud/) (Nabu Casa offers a 31-day trial/refund window), Babo Cloud offers a **31-day satisfaction guarantee on the first subscription payment**:

| Refundable | Not refundable |
|------------|----------------|
| **First `$6.99` subscription charge** (within 31 days of payment) | **On-demand overage** beyond the included `$5` pool |
| | **Any usage billed in arrears** after the included pool is exhausted |
| | **Second and later monthly invoices** |
| | **Promotion-adjusted amounts** — refund policy applies to net subscription paid; confirm in support |

**Rules:**

1. **Window:** 31 calendar days from **first successful** `cloud_basic` payment (Stripe `invoice.paid` or Checkout completion).
2. **Scope:** Refund the **subscription fee only** — not upstream model spend you already incurred on OpenRouter for their included-pool usage (that cost is sunk).
3. **Overage:** If the user enabled on-demand and exceeded the `$5` included pool, **overage charges are never refunded** under this policy.
4. **Process:** Support request or self-serve link → Stripe **full or partial refund of subscription line** → cancel subscription → revoke resold access.
5. **Copy (pricing page):** *“Try Babo Cloud risk-free: full refund of your first month’s subscription within 31 days. Additional model usage beyond your included allowance is billed separately and is not refundable.”*

Store `firstPaidAt` on `CloudSubscription` (operator) to enforce the window in admin/support tooling.

### Plan tiers & entitlements

| Plan id | How assigned | Stripe | Inference path | UI |
|---------|--------------|--------|----------------|-----|
| **`cloud_basic`** | Checkout / webhook | **Yes** $6.99/mo | **OpenRouter resold** (`babo_resold`) | Standard Babo Cloud model picker; **GX10 hidden** |
| **`lifetime_comp`** | **Admin grant only** | **No** | **GX10** via Nest → `https://brain.babo.agency` (`hosted_babo`) | Setup + picker show **“Babo Brain (GX10)”** when flag set |
| *(future)* `cloud_pro` | Checkout | Yes | Resold + higher pool | TBD |

**Admin grant (family & friends):** Operator/admin sets `planId: lifetime_comp`, `billingExempt: true`, `hostedGx10Enabled: true`. User gets full Babo Cloud platform (relay, email, Google) **without payment** and routes brain to **your GX10**, not OpenRouter — so you are not charged per token on OpenRouter for them.

**Public users never see GX10** in setup unless they have `hostedGx10Enabled` (only comp plan today).

### Commercial scaffolding (v1 schema, programs later)

Ship **fields and hooks** now; UI and automation can follow.

| Capability | v1 scaffold | Later |
|------------|-------------|-------|
| **Stripe Promotion Codes** | Checkout accepts `promotion_code`; store `promotionCodeId` on subscription | Campaigns, influencer codes |
| **Admin comp / lifetime** | Admin API: `POST /admin/users/:id/grant-lifetime` | Self-serve credits |
| **Referrals** | `User.referredByUserId`, `referralCode` (unique); metadata on Stripe Customer | Refer-a-friend credits |
| **Affiliates** | `User.affiliateId`, `Affiliate` table stub; Stripe metadata `affiliate_id` | Payouts, dashboards |
| **Coupons** | Stripe Coupons + Promotion Codes in Dashboard; webhook logs redemption | In-app coupon field |

```prisma
// CloudSubscription (evolved)
planId              String   // cloud_basic | lifetime_comp
billingExempt       Boolean  @default(false)
hostedGx10Enabled   Boolean  @default(false)
grantedByAdminId    String?
grantNote           String?
promotionCodeId     String?  // Stripe, optional
firstPaidAt         DateTime? @map("first_paid_at")  // 31-day refund window anchor
referralCode        String?  @unique  // this user's share code (future)
referredByUserId    String?  // who referred them (future)
affiliateId         String?  // future
```

**API:** extend `GET /cloud/platform-capabilities`:

```json
{
  "inference": {
    "resoldAvailable": true,
    "hostedGx10Available": false,
    "hostedGx10Label": "Babo Brain (GX10)"
  },
  "billing": { "billingEnabled": true, "trialAvailable": false }
}
```

**Routing:** `ProviderKeysService.resolveInferenceUpstream()` — if `hostedGx10Enabled` → `mode: hosted`, GX10 upstream; else paid users → `babo_resold` OpenRouter.

### Enterprise / BYO credentials

- Own Resend domain + API key → their aliases
- Own Google Cloud OAuth app
- Optional self-hosted NestJS control plane  
→ Contract pricing; same placement model.

---

## Billing UX (Cursor 2026 pattern)

Reference: [Cursor usage limits](https://cursor.com/help/models-and-usage/usage-limits) — dollar API pool, model choice affects burn rate, dashboard %, on-demand with spend cap.

### What users see

| Surface | Copy / behaviour |
|---------|------------------|
| **Pricing page** | “$5 of model usage included” — **dollars**, not token quota |
| **Settings → Billing** | Usage bar: **“42% of included usage remaining”**; resets on billing date |
| **Model picker** | Per model: **input/output $/1M**, tier badge (`Recommended` / `Premium`), **~N× included usage** vs Gemini Flash |
| **Premium model select** | Soft confirm: “Sonnet uses included usage ~9× faster than Gemini Flash” — no hard block |
| **At 100% included** | In-app notice: enable on-demand or wait for reset; link to spend cap |
| **Invoices** | Line items by model + tokens; overage as “Additional model usage” |

### Model catalog metadata (frontend + API)

Extend Babo Cloud model list with pricing fields (synced from OpenRouter catalog periodically):

```typescript
{
  id: 'google/gemini-2.5-flash',
  tier: 'recommended',       // sort first
  inputPerM: 0.30,
  outputPerM: 2.50,
  usageMultiplier: 1,        // vs reference model
}
{
  id: 'anthropic/claude-sonnet-4',
  tier: 'premium',
  inputPerM: 3.00,
  outputPerM: 15.00,
  usageMultiplier: 9,      // from empirical data; refresh quarterly
}
```

**Picker groups:** Recommended → Standard → Premium. Default selection: `google/gemini-2.5-flash`.

### Comparison to Cursor Pro (2026)

| | Cursor Pro | Babo Cloud Basic |
|--|------------|------------------|
| Price | $20/mo | **$6.99/mo** |
| Included API usage | $20 | **$5** |
| Cheap path | Auto + Composer (separate pool) | Gemini Flash (recommended) |
| Overage | API cost, pay-as-you-go | API cost × 1.25, pay-as-you-go |
| Spend cap | User-configurable | Default $15/mo on-demand |

---

## Empirical cost data (OpenRouter, May 2026)

Internal reference from Babo agentic E2E platform builds (ICF-style tasks). Use for pricing sanity checks and `usageMultiplier` defaults.

### Completed path — Gemini 2.5 Flash (May 29)

| Metric | Value |
|--------|-------|
| Full morning session | **~$6.89** upstream |
| Single heavy E2E hour | **~$5.74** / ~13.6M tokens |
| **Focused E2E burst (~22 min)** | **~$3.64** / **~7.7M tokens** |
| Blended rate | **~$0.36 / 1M tokens** |

**Rule of thumb for pricing:** one full ICF-style platform E2E (back + front + repo + sub-agent waves) ≈ **$3.50–7** and **~8–14M tokens** on Gemini Flash — so **`$5` included ≈ one build** for a typical paid user.

### Incomplete path — Claude Sonnet 4 (May 28)

| Metric | Value |
|--------|-------|
| All Sonnet attempts (never finished E2E) | **~$43.96** |
| Supporting Gemini + mini same day | **~$7.80** |
| **All-in incomplete day** | **~$52.17** |
| Best single Sonnet hour | **~$22.92** / ~7.0M tokens |
| Blended Sonnet rate | **~$3.28 / 1M tokens** (~**9×** Flash) |

### Task-level cheat sheet (for support & UI tooltips)

| Task | Gemini Flash | Claude Sonnet 4 |
|------|--------------|-----------------|
| Light chat day | $0.50–2 | $5–15 |
| Full agentic E2E build | **$5–8** | **$8–23/hr** (often incomplete) |
| Hypothetical Sonnet at Gemini token volume | — | **~$45–55** |

**Product implication:** $5 included usage ≈ **~1 full Gemini E2E** or **~20% of a Sonnet-heavy hour**. Premium models must stay available but clearly labelled.

---

## Stripe integration (Phase D — `babo-operator` private repo)

All Stripe code lives in **`babo-operator`**, not in public `babo`. OSS backend exposes `CloudBillingProvider`; operator implements paid entitlements and registers webhooks on deploy.

| Piece | Spec |
|-------|------|
| **Product** | `Babo Cloud Basic` |
| **Recurring price** | `price_*` → $6.99/mo |
| **Metered overage** | Stripe Billing Meter → report `overageCents` daily or per aggregated window |
| **Webhooks** | `checkout.session.completed`, `customer.subscription.updated/deleted`, `invoice.paid`, `invoice.payment_failed` |
| **Portal** | Customer Portal for card, invoices, cancel |
| **User fields** | `stripeCustomerId`, `stripeSubscriptionId` on `User` |
| **Period reset** | On `invoice.paid`: `usedCreditCents = 0`; refresh `includedCreditCents` from plan |

**Grace:** 3 days `past_due` before blocking inference. **Idempotent** webhook handling required.

---

## Onboarding implications

1. **Scan device + LAN** — recommend a persona, not a single radio button.
2. **Four workload cards** + optional **platform** section (email, Google) explaining Babo vs BYO.
3. **Brain card** shows three paths when cloud is relevant: My server · My API key · Babo hosted (resold OpenRouter models).
4. **Default Babo Cloud brain** to **Gemini 2.5 Flash**; explain premium models cost more per task.
5. **Test** per workload: inference `/v1/models`, 1 s transcribe, optional vision frame.
6. **Persist** `capabilityProfile` + platform credential source flags.
7. **Paid Cloud users** see Checkout before resold inference; **comp users** see GX10 brain when admin-granted.

See [capability-profiles-and-onboarding.md](capability-profiles-and-onboarding.md) for env mapping and [production-architecture-and-onboarding.md](production-architecture-and-onboarding.md) for wizard steps.

---

## Implementation roadmap (production-ready end-to-end)

Goal: ship **Phases A → D** as one coherent Babo Cloud release (not a partial proxy). Order still matters for development sequencing.

| Phase | Deliverable | Production criteria |
|-------|-------------|---------------------|
| **A** | Nest `inference-proxy` module: `chat/completions`, `models`, JWT + `nlsk_` auth, usage table, rate-limit hooks, GX10 upstream env | Desktop `hosted_babo` points at `api.babo.agency`; stream usage per chunk |
| **B** | GPU proxy routes: transcribe, vision, embed → same GX10 fleet via Nest | Hosted voice/vision/embed in capability profile |
| **C** | Resold + BYOK frontier: store user provider keys securely; route OpenAI/Anthropic through proxy; `upstreamCostCents` | Settings/UI can pick provider; credits/overage backend stub |
| **D** | **`babo-operator`:** Stripe, paid entitlements, billing UX; OSS: `CloudBillingProvider` + NoOp, dollar credits schema, `upstreamCostCents` | Operator on `api.babo.agency` only; usage bar when `billingEnabled`; model picker pricing — see [implementation plan](babo-cloud-billing-implementation-plan.md) |

**Also in v1:** `ApiKeysService.validateKey()` used by proxy; Python `validate_api_key` implemented or delegated to Nest for cloud paths.

---

## Implementation checklist (cross-reference)

| Item | Doc / code | Status |
|------|------------|--------|
| Capability schema + TS types | `nls/config/capability-profile.schema.json`, `desktop/electron/capability-types.ts` | Shipped |
| NestJS inference + GPU proxy | `backend/src/babo-cloud/` | Shipped |
| Per-agent API keys + validation | `api-keys` + `babo-cloud` guards | Shipped |
| Per-user Resend BYO | `provider-keys` + `channels` | Shipped |
| Resold + BYOK frontier routing | `provider-keys` + inference proxy | Shipped (OpenRouter) |
| Usage ledger + stream chunk writes | Prisma `InferenceUsage` | Shipped |
| Subscription trial stub | `cloud_subscriptions` + `EntitlementsService` | Shipped (token-based — **migrate to cents in OSS**; paid logic → operator) |
| `CloudBillingProvider` + `NoOpBillingProvider` | `backend/src/babo-cloud/billing/` | **Planned (OSS)** |
| `upstreamCostCents` on inference proxy | `inference.service.ts` | **Planned (OSS)** |
| Dollar credits schema | Prisma `includedCreditCents` / `usedCreditCents` | **Planned (OSS)** |
| Stripe Checkout + webhooks + Portal | **`babo-operator`** | **Planned (private)** |
| Paid entitlements + spend cap | **`babo-operator`** | **Planned (private)** |
| Billing UX: usage bar, upgrade | Frontend + operator API | **Planned** |
| Model picker: $/1M, tier, usage multiplier | `BABO_CLOUD_MODELS` + picker component | **Planned** |
| `hosted_babo` / cloud brain env | `capabilityProfileToRuntimeEnv`, setup wizard | Planned |
| Self-host docs for Nest upstream env | `docs/configuration/` | Planned |

---

## Open questions (remaining)

Minor — safe to implement without blocking:

1. **Pro tier timing** — when to add `$19.99/mo` with `$20` included usage.
2. **Annual billing** — 20% discount? Defer to post-launch.
3. **Referral / affiliate programs** — reward amounts and automation (schema ready in v1).

Record answers here when set.

---

## Revision log

| Date | Change |
|------|--------|
| 2026-05-26 | Initial design: personas, workloads, platform services, billing direction |
| 2026-05-26 | Locked pre-implementation decisions: routing, auth, GX10 relay, pricing shape, roadmap A–D |
| 2026-05-26 | Clarified BYOK vs LAN; all `byok_cloud` (incl. OpenRouter) via Nest |
| 2026-05-29 | **Locked v1 pricing:** $6.99/mo + $5 included API usage + 1.25× overage; trial $8 cap; dollar-based ledger; Cursor-aligned UX; empirical OpenRouter cost data (Gemini vs Sonnet) |
| 2026-05-29 | **Locked OSS/operator split (Home Assistant model):** MIT `babo` = full product; private **`babo-operator`** = Stripe + paid entitlements only; long-term **Babo Box** hardware vision |
| 2026-05-29 | **No inference trial;** 31-day refund on first subscription only (not overage); lifetime comp + GX10; coupon/referral/affiliate scaffolding |
