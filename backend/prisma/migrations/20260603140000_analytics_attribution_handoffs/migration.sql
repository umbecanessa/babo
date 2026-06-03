CREATE TABLE "analytics_attribution_handoffs" (
    "id" TEXT NOT NULL,
    "ref" TEXT NOT NULL,
    "visitor_id" TEXT,
    "properties" JSONB NOT NULL DEFAULT '{}',
    "claimed_at" TIMESTAMP(3),
    "claimed_install_id" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expires_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "analytics_attribution_handoffs_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "analytics_attribution_handoffs_ref_key"
    ON "analytics_attribution_handoffs"("ref");

CREATE INDEX "analytics_attribution_handoffs_claimed_install_id_idx"
    ON "analytics_attribution_handoffs"("claimed_install_id");

CREATE INDEX "analytics_attribution_handoffs_created_at_idx"
    ON "analytics_attribution_handoffs"("created_at");
