-- Inference proxy: per-agent API keys + usage ledger

ALTER TABLE "api_keys" ADD COLUMN IF NOT EXISTS "agent_id" TEXT;
ALTER TABLE "api_keys" ADD COLUMN IF NOT EXISTS "scopes" TEXT[] DEFAULT ARRAY['inference']::TEXT[];

CREATE INDEX IF NOT EXISTS "api_keys_key_prefix_idx" ON "api_keys"("key_prefix");
CREATE INDEX IF NOT EXISTS "api_keys_user_id_is_active_idx" ON "api_keys"("user_id", "is_active");

ALTER TABLE "api_keys"
  ADD CONSTRAINT "api_keys_agent_id_fkey"
  FOREIGN KEY ("agent_id") REFERENCES "agents"("id") ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE IF NOT EXISTS "inference_usage" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "api_key_id" TEXT,
    "agent_id" TEXT,
    "workload" TEXT NOT NULL DEFAULT 'inference',
    "placement" TEXT NOT NULL,
    "provider" TEXT,
    "model" TEXT NOT NULL,
    "route" TEXT NOT NULL,
    "prompt_tokens" INTEGER NOT NULL DEFAULT 0,
    "completion_tokens" INTEGER NOT NULL DEFAULT 0,
    "total_tokens" INTEGER NOT NULL DEFAULT 0,
    "upstream_cost_cents" INTEGER,
    "request_id" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "inference_usage_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "inference_usage_user_id_created_at_idx" ON "inference_usage"("user_id", "created_at");
CREATE INDEX IF NOT EXISTS "inference_usage_request_id_idx" ON "inference_usage"("request_id");

ALTER TABLE "inference_usage"
  ADD CONSTRAINT "inference_usage_user_id_fkey"
  FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
