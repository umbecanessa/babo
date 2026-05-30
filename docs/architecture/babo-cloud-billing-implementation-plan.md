# Babo Cloud billing: end-to-end implementation plan

**Status:** Plan — ready to execute  
**Parent doc:** [Babo Cloud personas & commercial design](babo-cloud-personas-and-commercial-design.md)  
**Goal:** 100% complete split (MIT `babo` + private `babo-operator`) and production Babo Cloud billing on `api.babo.agency`.

---

## Outcome definition (“100% done”)

| # | Criterion |
|---|-----------|
| 1 | Public **`babo`** repo runs fully self-host with **no paywall** (`BABO_CLOUD_MODE=false`, no operator). |
| 2 | **`babo-operator`** (private) implements Stripe + paid entitlements; **not** in public repo. |
| 3 | **`api.babo.agency`** deploys `babo` + `babo-operator` with production secrets. |
| 4 | User can **sign up → pay $6.99/mo → use $5 included pool → overage at 1.25×** (no free inference trial). |
| 4b | **Admin** can grant **`lifetime_comp`** → GX10 (`brain.babo.agency`) visible in UI; no Stripe, no pool debit. |
| 5 | **Usage ledger** records `upstreamCostCents` per inference request; pool debits correctly. |
| 6 | **Frontend** shows billing when `billingEnabled`; model picker shows $/1M + burn hints; hidden for self-host. |
| 7 | **Stripe webhooks** idempotent; `invoice.paid` resets period + sets `firstPaidAt`; **31-day refund** on first sub only (not overage); `payment_failed` grace → block. |
| 8 | **Docs** updated: self-host, Babo Cloud, operator deploy, env reference. |
| 9 | **E2E test checklist** passed on staging before production cutover. |

---

## Architecture (target)

```text
┌─────────────────────────────────────────────────────────────────┐
│  babo/ (MIT, GitHub public)                                      │
│  Desktop · runtime · frontend · backend/src/babo-cloud/          │
│  CloudBillingProvider (interface) + NoOpBillingProvider          │
│  InferenceUsage + upstreamCostCents writes                       │
│  CloudSubscription schema (shared DB shape)                      │
│  BABO_CLOUD_MODE=false → assertCloudAccess always passes         │
└────────────────────────────┬────────────────────────────────────┘
                             │ npm link / private registry / monorepo CI
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  babo-operator/ (private)                                        │
│  OperatorModule (Nest dynamic module)                            │
│  StripeBillingProvider · webhooks · paid entitlements          │
│  Trial cap · credit pool · overage · spend cap                   │
└────────────────────────────┬────────────────────────────────────┘
                             │ deployed together
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  api.babo.agency (Railway)                                       │
│  BABO_CLOUD_MODE=true · BILLING_PROVIDER=operator                │
│  STRIPE_* · PLATFORM_OPENROUTER_API_KEY · RESEND_* · GX10_*    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase 0 — Repos, contracts & env (1–2 days)

### 0.1 Create private `babo-operator` repository

- NestJS library or secondary app that **depends on** `babo` backend version (pin semver or git tag).
- `README`: deploy-only, not published; Babo Agency internal.
- CI: lint + unit tests; deploy artifact consumed by Railway.

### 0.2 Define `CloudBillingProvider` contract (in OSS first)

Location: `backend/src/babo-cloud/billing/cloud-billing.provider.ts`

```typescript
export interface SubscriptionView {
  billingEnabled: boolean;
  status: 'none' | 'active' | 'past_due' | 'canceled' | 'lifetime_comp';
  planId: 'cloud_basic' | 'lifetime_comp' | string;
  billingExempt: boolean;
  hostedGx10Enabled: boolean;
  includedCreditCents: number;
  usedCreditCents: number;
  usedPercent: number;
  currentPeriodEnd?: string;
  allowOverage: boolean;
  monthlySpendCapCents?: number | null;
  onDemandEnabled: boolean;
  // future: referralCode, promotionActive
}

export interface CloudBillingProvider {
  isEnabled(): boolean;
  assertCloudAccess(userId: string): Promise<void>;
  onUserRegistered(userId: string): Promise<void>;
  recordUsage(userId: string, upstreamCostCents: number): Promise<void>;
  getSubscription(userId: string): Promise<SubscriptionView>;
  createCheckoutSession(userId: string, returnUrl: string): Promise<{ url: string }>;
  createPortalSession(userId: string, returnUrl: string): Promise<{ url: string }>;
  updateSpendCap(userId: string, capCents: number | null): Promise<void>;
}
```

Token: `CLOUD_BILLING_PROVIDER` or `BILLING_PROVIDER=noop|operator`.

### 0.3 Env matrix (document in `backend/.env.example`)

| Variable | Self-host OSS | api.babo.agency |
|----------|---------------|-----------------|
| `BABO_CLOUD_MODE` | `false` | `true` |
| `BILLING_PROVIDER` | `noop` | `operator` |
| `STRIPE_*` | unset | set |
| `PLATFORM_OPENROUTER_API_KEY` | unset | set |

---

## Phase 1 — OSS refactor: billing abstraction (3–5 days)

### 1.1 Prisma migration (OSS)

Evolve `CloudSubscription`:

```prisma
includedCreditCents   Int   @default(0) @map("included_credit_cents")
usedCreditCents       Int   @default(0) @map("used_credit_cents")
billingExempt         Boolean @default(false) @map("billing_exempt")
hostedGx10Enabled     Boolean @default(false) @map("hosted_gx10_enabled")
grantedByAdminId      String? @map("granted_by_admin_id")
grantNote             String? @map("grant_note")
firstPaidAt           DateTime? @map("first_paid_at")
promotionCodeId       String? @map("promotion_code_id")
referralCode          String? @unique @map("referral_code")
referredByUserId      String? @map("referred_by_user_id")
affiliateId           String? @map("affiliate_id")
```

Add migration; **drop** `includedTokens` / `usedTokens`. **Pre-launch: DB wipe OK** — no conversion script required.

### 1.2 Implement `NoOpBillingProvider` (OSS)

- `assertCloudAccess()` → no-op (always allow).
- `isEnabled()` → `false`.
- `getSubscription()` → `{ billingEnabled: false, status: 'none', … zeros }`.
- `recordUsage()` → optional analytics only (increment nothing against pool).

### 1.3 Refactor `EntitlementsService` → thin facade

- Inject `CloudBillingProvider` (use `@Optional()` + factory).
- `assertCloudAccess` → delegate to provider.
- `ensureSubscriptionForUser` → rename to `onUserRegistered` on provider; **NoOp skips DB row** or creates minimal row without trial.
- Remove hardcoded `DEFAULT_INCLUDED_TOKENS`, `TRIAL_DAYS`, `activatePaid` from OSS — move to operator.

### 1.4 Refactor `CloudUsageService`

After `record()` upserts `InferenceUsage`:

```typescript
if (upstreamCostCents != null && upstreamCostCents > 0) {
  await this.billing.recordUsage(auth.userId, upstreamCostCents);
}
```

Stop calling `entitlements.addTokenUsage()` (token-based).

### 1.5 Wire module factory (OSS)

`BaboCloudModule`:

```typescript
{
  provide: CLOUD_BILLING_PROVIDER,
  useFactory: (config) => {
    if (config.get('BILLING_PROVIDER') === 'operator') {
      throw new Error('Operator module must register billing provider');
    }
    return new NoOpBillingProvider();
  },
}
```

When operator loads, it **overrides** `CLOUD_BILLING_PROVIDER` with `StripeBillingProvider`.

### 1.6 Update guards & auth

- `CloudAccessGuard` → unchanged (delegates to provider).
- `auth.service.ts` → `billing.onUserRegistered(userId)` instead of `ensureSubscriptionForUser`.
- `subscription.controller.ts` (OSS): read-only `GET subscription`, `GET usage`; **remove** `POST activate` stub (move to operator).

### 1.7 Platform capabilities

Extend `GET /cloud/platform-capabilities`:

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

`hostedGx10Available` = `true` only when user has `hostedGx10Enabled` (admin `lifetime_comp`).

---

## Phase 2 — OSS metering: `upstreamCostCents` (3–4 days)

### 2.1 Model price catalog

- `backend/src/babo-cloud/pricing/model-prices.ts` — map OpenRouter model id → input/output $/M (seed from OpenRouter API or static JSON refreshed weekly).
- `computeUpstreamCostCents(model, promptTokens, completionTokens): number`.

### 2.2 Inference proxy

In `inference.service.ts` after `normalizeUsage()`:

- Compute `upstreamCostCents` from model + tokens.
- Pass to `usage.record({ … upstreamCostCents })`.
- For `byok_cloud`: still record tokens + cost for analytics; **operator does not debit pool** (check `placement` in operator).

### 2.3 Inference routing by plan (OSS + operator)

Update `ProviderKeysService.resolveInferenceUpstream()`:

| User flag | Upstream |
|-----------|----------|
| `hostedGx10Enabled` | `hosted_babo` → `INFERENCE_UPSTREAM_URL` (`brain.babo.agency`) |
| Paid `cloud_basic` | `babo_resold` → OpenRouter |
| BYOK configured | `byok_cloud` |

**Pool debit (Phase 2):** only `babo_resold` on `cloud_basic`. **Skip debit** when `billingExempt` or `lifetime_comp`.

### 2.4 GPU proxy (optional v1)

- Transcribe/vision/embed: flat rate or skip pool debit in v1; document as Phase 2b.

### 2.5 Admin dashboard

- `admin.service.ts`: show `_sum.upstreamCostCents` (already partially there); add margin view when billing enabled.

---

## Phase 3 — `babo-operator`: Stripe + paid entitlements (5–8 days)

### 3.1 Operator module structure

```text
babo-operator/
  src/
    operator.module.ts          # imports StripeModule, registers provider override
    stripe/
      stripe.service.ts
      stripe-webhook.controller.ts   # raw body route
      stripe.config.ts
    billing/
      stripe-billing.provider.ts     # implements CloudBillingProvider
      entitlements.service.ts        # trial, pool, cap logic
      usage-aggregator.service.ts    # batch meter events to Stripe
    dto/
  test/
  package.json
```

### 3.2 Stripe setup (Dashboard)

| Item | Value |
|------|-------|
| Product | Babo Cloud Basic |
| Price | $6.99/mo recurring `price_*` |
| Meter | `babo_inference_upstream_cents` (overage) |
| Trial | **None** for resold inference — Checkout on first use |
| Promotion codes | Stripe `allow_promotion_codes: true` on Checkout |
| Comp / lifetime | Admin grant — not in Stripe |
| Webhook endpoint | `https://api.babo.agency/api/billing/stripe/webhook` |

| Customer Portal | enabled |

**Recommended flow:** Sign up → use platform (relay, settings) → **Checkout required before first resold inference** → `$5` pool active.

### 3.3a Admin: grant lifetime comp

| Method | Behaviour |
|--------|-----------|
| `POST /admin/users/:id/grant-lifetime` | Set `planId: lifetime_comp`, `billingExempt: true`, `hostedGx10Enabled: true`, `status: active` |
| `POST /admin/users/:id/revoke-lifetime` | Revert to none or require Checkout |

OSS admin module exposes routes; operator implements grant logic (or admin service in OSS with operator-only guards).

### 3.3b Commercial scaffolding (stub)

- Generate `referralCode` on user create (unused until referral program).
- Checkout: pass `client_reference_id`, `metadata.referral_code`, `metadata.affiliate_id` when present.
- Tables: `affiliates` (id, name, stripeConnectId?) — empty OK at launch.
- Webhook: log `promotion_code` on `checkout.session.completed`.

### 3.4 `StripeBillingProvider` behaviour

| Method | Behaviour |
|--------|-----------|
| `onUserRegistered` | Create row `status: none` until Checkout; generate `referralCode` |
| `assertCloudAccess` | Allow if `billingExempt` / `lifetime_comp`; else require `active` Stripe sub for resold |
| `recordUsage` | Skip if `billingExempt`; else increment pool + overage |
| `createCheckoutSession` | Stripe Checkout → `cloud_basic` price; store `stripeCustomerId` |
| `createPortalSession` | Billing Portal |
| `getSubscription` | Full `SubscriptionView` with `usedPercent`, `billingEnabled: true` |

### 3.4 Webhooks (idempotent)

| Event | Action |
|-------|--------|
| `checkout.session.completed` | Link customer; set `active`; `includedCreditCents=500`; reset `usedCreditCents=0`; set period end |
| `customer.subscription.updated` | Sync status, period end |
| `customer.subscription.deleted` | `canceled`; block access |
| `invoice.paid` | Reset `usedCreditCents=0`; set `firstPaidAt` on **first** paid invoice |
| `invoice.payment_failed` | `past_due`; start 3-day grace timer |

Store processed event ids in `stripe_webhook_events` table (operator migration or OSS table — prefer OSS schema, operator writes).

### 3.5 Overage billing

- When `usedCreditCents > includedCreditCents` and `onDemandEnabled`:
  - Report `(used - included) * 1.25` increment to Stripe Billing Meter (daily batch or threshold).
- Respect `monthlySpendCapCents` — block inference at cap with 402 + clear message.

### 3.6 Operator HTTP routes (private module)

| Method | Path | Auth |
|--------|------|------|
| POST | `/api/billing/checkout` | JWT |
| POST | `/api/billing/portal` | JWT |
| PUT | `/api/billing/spend-cap` | JWT |
| PUT | `/api/billing/on-demand` | JWT |
| POST | `/api/billing/stripe/webhook` | Stripe signature |

### 3.7 Deploy wiring

Railway Dockerfile / start script:

```bash
npm install @babo/operator@private
# or COPY from CI artifact
node dist/main.js  # main imports OperatorModule when BILLING_PROVIDER=operator
```

OSS `main.ts` or `AppModule`:

```typescript
if (process.env.BILLING_PROVIDER === 'operator') {
  const { OperatorModule } = await import('@babo/operator');
  imports.push(OperatorModule.forRoot());
}
```

---

## Phase 4 — Frontend billing & model UX (4–6 days)

### 4.1 API service

Add to `api.service.ts`:

- `getCloudSubscription()`
- `getCloudUsage(limit?)`
- `createBillingCheckout(returnUrl)`
- `createBillingPortal(returnUrl)`
- `updateSpendCap(cents | null)`
- `setOnDemandEnabled(boolean)`

### 4.2 Billing service / signals

- `BillingService` with `subscription`, `billingEnabled`, `usedPercent` signals.
- Poll or refresh after agent runs (optional SSE later).

### 4.3 Settings → Billing panel (new)

Show when `platformCapabilities.billingEnabled`:

- Plan name, status, renewal date
- Usage bar: “X% of included usage remaining”
- **Subscribe** CTA when not active (no trial banner)
- Links: Manage subscription (Portal), Set spend cap
- On-demand toggle
- **Hidden** for `lifetime_comp` / `billingExempt` (show “Lifetime access” badge instead)

Hide entirely for self-host / `billingEnabled: false`.

### 4.4 Paywall UX

- `402` from resold inference → toast + route to Billing / Checkout
- `lifetime_comp` + GX10: no 402 on hosted path
- Settings / pricing footnote: **31-day refund on first month’s subscription**; overage not refundable

### 4.5 Setup wizard & GX10 visibility

- Default paid path: Babo Cloud → **OpenRouter resold** only.
- If `platformCapabilities.inference.hostedGx10Available`:
  - Show brain card **“Babo Brain (GX10)”** → sets `hosted_babo` → Nest → `brain.babo.agency`.
  - Hidden for all other users.
- Copy: **$6.99/mo**, **$5 included usage**, no free model trial.

### 4.6 Model picker enhancements

Extend `BABO_CLOUD_MODELS` / fetch from API:

- `tier`, `inputPerM`, `outputPerM`, `usageMultiplier`
- Group: Recommended / Standard / Premium
- Premium select → confirm dialog (~N× usage)
- Only when `billingEnabled` and not on GX10-only comp path

---

## Phase 5 — Deploy & infra (2–3 days)

### 5.1 Staging environment

- `staging.api.babo.agency` + Stripe test mode
- Test cards; webhook CLI forward for local dev

### 5.2 Production `api.babo.agency`

- Railway env: all secrets
- `BABO_CLOUD_MODE=true`, `BILLING_PROVIDER=operator`
- Prisma migrate on deploy
- Stripe live webhook registered

### 5.3 Desktop app

- Default backend URL for production builds → `https://api.babo.agency`
- Dev remains configurable

### 5.4 Remove / gate OSS stubs

- Delete `POST /cloud/subscription/activate` stub from OSS once operator ships
- **DB wipe OK** on deploy (no token→cents migration)

---

## Phase 6 — Edge cases & admin (2–3 days)

| Case | Handling |
|------|----------|
| BYOK on Babo Cloud | Platform sub required; usage not debited from pool |
| `allowOverage=false` | Hard stop at 100% included |
| `lifetime_comp` | No Stripe; GX10 upstream; no pool debit; GX10 in UI |
| Admin comp / lifetime | `POST /admin/users/:id/grant-lifetime` |
| Refunds | **31-day:** refund **first `$6.99` subscription only** via Stripe; **never** refund on-demand overage lines; cancel sub on refund |
| User deletes account | Stripe cancel sub; GDPR delete user row |
| Webhook replay | Idempotency table |

Admin console (`admin/`): optional billing column — MRR, usage cost, margin per user.

---

## Phase 7 — Documentation (1–2 days)

| Doc | Content |
|-----|---------|
| [babo-cloud-personas-and-commercial-design.md](babo-cloud-personas-and-commercial-design.md) | Already locked — link this plan |
| `docs/configuration/babo-cloud-operator-deploy.md` | **New** — private deploy runbook (internal) |
| `docs/configuration/self-hosting.md` | `BABO_CLOUD_MODE=false`, no billing |
| `backend/.env.example` | All billing vars commented |
| Website / pricing page copy | $6.99 + $5 included usage |

---

## Phase 8 — QA & acceptance (3–5 days)

### 8.1 Automated tests

**OSS:**

- `NoOpBillingProvider` — access always allowed
- `computeUpstreamCostCents` — known model/token fixtures
- `CloudUsageService.record` — writes cents

**Operator:**

- Webhook signature verification
- Trial expiry blocks 402
- Pool debit + overage multiplier math
- Spend cap blocks

### 8.2 Manual E2E checklist

- [ ] Self-host: register, inference BYOK, no billing UI
- [ ] Babo Cloud: register → Checkout → active → resold inference debits pool
- [ ] Admin grant lifetime → GX10 card appears → inference via brain.babo.agency, no billing UI
- [ ] Paid user never sees GX10 card
- [ ] Checkout → active sub → $5 pool reset
- [ ] Sonnet request — faster burn visible in picker + usage
- [ ] Overage with cap — stops at cap
- [ ] Portal — cancel — access blocked at period end
- [ ] Webhook idempotency — replay same event, no double credit

---

## Execution order (critical path)

```text
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 5 (staging)
                │                         │
                └──── Phase 4 (parallel after 1.7 + 3.6 stubs)
                                          └──► Phase 6 ──► Phase 8 ──► Phase 5 (prod)
Phase 7 ── throughout
```

**Parallel tracks:**

- **Track A (OSS):** Phases 1 → 2  
- **Track B (Operator):** Phase 0 repo → Phase 3 (starts after 0.2 contract frozen)  
- **Track C (Frontend):** Phase 4 (starts after subscription API shape stable)  

---

## Rough timeline (solo + Cursor)

| Phase | Duration |
|-------|----------|
| 0 | 1–2 days |
| 1 | 3–5 days |
| 2 | 3–4 days |
| 3 | 5–8 days |
| 4 | 4–6 days |
| 5 | 2–3 days |
| 6 | 2–3 days |
| 7 | 1–2 days |
| 8 | 3–5 days |
| **Total** | **~4–6 weeks** |

---

## Migration: current code → target

| Current | Action |
|---------|--------|
| `EntitlementsService` with trial + `includedTokens` | Split: NoOp in OSS; paid logic → operator |
| `POST /cloud/subscription/activate` stub | Remove from OSS; operator only |
| `addTokenUsage()` | Replace with `billing.recordUsage(cents)` |
| No `upstreamCostCents` in proxy | Phase 2 |
| No frontend billing | Phase 4 |
| Stripe TBD in checklist | Phase 3 |

---

## Open decisions (resolved)

1. **Operator packaging:** private npm `@babo/operator` ✓  
2. **Trial:** **None** on resold inference; **31-day refund on first `$6.99` only** (not overage) ✓  
3. **GX10:** Hidden from public; **`lifetime_comp` admin grant only**; pool debit N/A for comp ✓  
4. **DB migration:** **Wipe OK**; drop token columns, use cents ✓  
5. **Referrals / affiliates / coupons:** Schema + Stripe hooks in v1; programs later ✓  

---

## Revision log

| Date | Change |
|------|--------|
| 2026-05-29 | Initial end-to-end plan: OSS/operator split, 8 phases, acceptance criteria |
| 2026-05-29 | No trial; 31-day first-subscription refund (not overage); lifetime_comp + GX10; commercial scaffolding |
