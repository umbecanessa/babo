# Fix analytics tables on production (P2021)

If logs show `product_analytics_events` or `analytics_attribution_handoffs` does not exist,
startup may have marked migrations as **applied** without running SQL.

## Option A — Railway shell (recommended)

In the **backend** service shell (with `DATABASE_URL` set):

```bash
cd /app
npx prisma migrate resolve --rolled-back 20260603120000_product_analytics_events
npx prisma migrate resolve --rolled-back 20260603140000_analytics_attribution_handoffs
npx prisma migrate deploy
```

If those migrations were never recorded, skip the `resolve --rolled-back` lines and only run:

```bash
npx prisma migrate deploy
```

## Option B — Run SQL in Postgres

Paste both files from `prisma/migrations/20260603120000_product_analytics_events/migration.sql`
and `prisma/migrations/20260603140000_analytics_attribution_handoffs/migration.sql`, then:

```sql
INSERT INTO "_prisma_migrations" (id, checksum, finished_at, migration_name, logs, rolled_back_at, started_at, applied_steps_count)
VALUES
  (gen_random_uuid()::text, '', NOW(), '20260603120000_product_analytics_events', NULL, NULL, NOW(), 1),
  (gen_random_uuid()::text, '', NOW(), '20260603140000_analytics_attribution_handoffs', NULL, NULL, NOW(), 1)
ON CONFLICT DO NOTHING;
```

(Only if not already in `_prisma_migrations`.)

## Verify

```bash
curl -s https://api.babo.agency/api/analytics/config
curl -s -X POST https://api.babo.agency/api/analytics/web-events \
  -H "Content-Type: application/json" \
  -d '{"events":[{"name":"landing_page_view","installId":"verify-001","platform":"web","properties":{}}]}'
```

Expect: `{"accepted":1}`.
