-- Reset cloud subscriptions for credit-based billing (pre-launch wipe OK)
TRUNCATE TABLE "cloud_subscriptions";

ALTER TABLE "cloud_subscriptions" DROP COLUMN IF EXISTS "trial_ends_at";
ALTER TABLE "cloud_subscriptions" DROP COLUMN IF EXISTS "included_tokens";
ALTER TABLE "cloud_subscriptions" DROP COLUMN IF EXISTS "used_tokens";

ALTER TABLE "cloud_subscriptions" ALTER COLUMN "status" SET DEFAULT 'none';
ALTER TABLE "cloud_subscriptions" ALTER COLUMN "plan_id" SET DEFAULT 'none';

ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "first_paid_at" TIMESTAMP(3);
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "included_credit_cents" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "used_credit_cents" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "billing_exempt" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "hosted_gx10_enabled" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "granted_by_admin_id" TEXT;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "grant_note" TEXT;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "promotion_code_id" TEXT;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "referral_code" TEXT;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "referred_by_user_id" TEXT;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "affiliate_id" TEXT;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "on_demand_enabled" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "monthly_spend_cap_cents" INTEGER;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "stripe_customer_id" TEXT;
ALTER TABLE "cloud_subscriptions" ADD COLUMN IF NOT EXISTS "stripe_subscription_id" TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS "cloud_subscriptions_referral_code_key" ON "cloud_subscriptions"("referral_code");
