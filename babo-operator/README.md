# babo-operator (private)

Stripe billing operator for [Babo Cloud](https://babo.agency). Implements `CloudBillingProvider` and registers when `BILLING_PROVIDER=operator`.

## Stripe setup

1. Create product **Babo Cloud Basic** at **$6.99/mo** recurring.
2. Copy the Price ID → `STRIPE_PRICE_CLOUD_BASIC=price_…`
3. Enable **Link** in Stripe Dashboard → Settings → Payment methods (Checkout shows Link automatically).
4. Enable **Customer Portal** (Billing → Customer portal).
5. Webhook endpoint: `https://api.babo.agency/api/billing/stripe/webhook`

Events to subscribe:

- `checkout.session.completed`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

## Env (api.babo.agency)

```env
BABO_CLOUD_MODE=true
BILLING_PROVIDER=operator
STRIPE_SECRET_KEY=sk_live_…
STRIPE_WEBHOOK_SECRET=whsec_…
STRIPE_PRICE_CLOUD_BASIC=price_…
```

## Local install (sibling repos)

```bash
cd babo-operator && npm install && npm run build
cd ../babo/backend && npm install ../babo-operator
```

Set `BILLING_PROVIDER=operator` and Stripe test keys.

## Webhook local dev

```bash
stripe listen --forward-to localhost:3000/api/billing/stripe/webhook
```

Use the printed `whsec_…` as `STRIPE_WEBHOOK_SECRET`.
