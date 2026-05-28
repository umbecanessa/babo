CREATE TABLE IF NOT EXISTS "cloud_subscriptions" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'trialing',
    "plan_id" TEXT NOT NULL DEFAULT 'cloud_basic',
    "trial_ends_at" TIMESTAMP(3),
    "current_period_end" TIMESTAMP(3),
    "included_tokens" INTEGER NOT NULL DEFAULT 500000,
    "used_tokens" INTEGER NOT NULL DEFAULT 0,
    "allow_overage" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "cloud_subscriptions_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "cloud_subscriptions_user_id_key" ON "cloud_subscriptions"("user_id");

ALTER TABLE "cloud_subscriptions"
  ADD CONSTRAINT "cloud_subscriptions_user_id_fkey"
  FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
