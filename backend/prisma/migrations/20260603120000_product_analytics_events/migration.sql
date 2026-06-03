CREATE TABLE "product_analytics_events" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "install_id" TEXT,
    "user_id" TEXT,
    "platform" TEXT,
    "app_version" TEXT,
    "properties" JSONB NOT NULL DEFAULT '{}',
    "occurred_at" TIMESTAMP(3) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "product_analytics_events_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "product_analytics_events_name_occurred_at_idx"
    ON "product_analytics_events"("name", "occurred_at");

CREATE INDEX "product_analytics_events_install_id_occurred_at_idx"
    ON "product_analytics_events"("install_id", "occurred_at");

CREATE INDEX "product_analytics_events_user_id_occurred_at_idx"
    ON "product_analytics_events"("user_id", "occurred_at");
