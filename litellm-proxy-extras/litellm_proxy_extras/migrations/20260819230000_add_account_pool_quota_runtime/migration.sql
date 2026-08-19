-- 本迁移持久化额度运行代次、幂等 usage 增量和可恢复窗口快照。

CREATE TABLE "LiteLLM_AccountPoolQuotaGeneration" (
    "generation_id" TEXT NOT NULL,
    "predecessor_generation_id" TEXT,
    "status" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL,
    "activated_at" TIMESTAMPTZ(6),
    "closed_at" TIMESTAMPTZ(6),
    "failure_code" TEXT,

    CONSTRAINT "LiteLLM_AccountPoolQuotaGeneration_pkey" PRIMARY KEY ("generation_id"),
    CONSTRAINT "LiteLLM_AccountPoolQuotaGeneration_predecessor_generation_id_fkey"
        FOREIGN KEY ("predecessor_generation_id")
        REFERENCES "LiteLLM_AccountPoolQuotaGeneration"("generation_id")
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "LiteLLM_AccountPoolQuotaGeneration_status_check"
        CHECK ("status" IN ('initializing', 'active', 'retired', 'failed')),
    CONSTRAINT "LiteLLM_AccountPoolQuotaGeneration_predecessor_check"
        CHECK ("predecessor_generation_id" IS NULL OR "predecessor_generation_id" <> "generation_id"),
    CONSTRAINT "LiteLLM_AccountPoolQuotaGeneration_lifecycle_check"
        CHECK (
            ("status" = 'initializing' AND "activated_at" IS NULL AND "closed_at" IS NULL AND "failure_code" IS NULL)
            OR ("status" = 'active' AND "activated_at" IS NOT NULL AND "closed_at" IS NULL AND "failure_code" IS NULL)
            OR ("status" = 'retired' AND "closed_at" IS NOT NULL AND "failure_code" IS NULL)
            OR ("status" = 'failed' AND "closed_at" IS NOT NULL AND "failure_code" IS NOT NULL)
        )
);

CREATE UNIQUE INDEX "LiteLLM_AccountPoolQuotaGeneration_single_active_idx"
    ON "LiteLLM_AccountPoolQuotaGeneration" (("status"))
    WHERE "status" = 'active';

CREATE INDEX "LiteLLM_AccountPoolQuotaGeneration_created_at_idx"
    ON "LiteLLM_AccountPoolQuotaGeneration"("created_at" DESC);

CREATE TABLE "LiteLLM_AccountPoolQuotaUsageEvent" (
    "event_id" TEXT NOT NULL,
    "generation_id" TEXT NOT NULL,
    "channel_id" TEXT,
    "account_id" TEXT NOT NULL,
    "window_id" TEXT NOT NULL,
    "lease_id" TEXT NOT NULL,
    "request_id" TEXT NOT NULL,
    "kind" TEXT NOT NULL,
    "amount" NUMERIC(256, 36) NOT NULL,
    "occurred_at" TIMESTAMPTZ(6) NOT NULL,
    "source" TEXT NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolQuotaUsageEvent_pkey" PRIMARY KEY ("event_id"),
    CONSTRAINT "LiteLLM_AccountPoolQuotaUsageEvent_generation_id_fkey"
        FOREIGN KEY ("generation_id")
        REFERENCES "LiteLLM_AccountPoolQuotaGeneration"("generation_id")
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "LiteLLM_AccountPoolQuotaUsageEvent_kind_check"
        CHECK ("kind" IN ('requests', 'tokens', 'credits', 'currency', 'provider_units')),
    CONSTRAINT "LiteLLM_AccountPoolQuotaUsageEvent_amount_check" CHECK ("amount" > 0),
    CONSTRAINT "LiteLLM_AccountPoolQuotaUsageEvent_source_check" CHECK ("source" = 'settlement'),
    CONSTRAINT "LiteLLM_AccountPoolQuotaUsageEvent_identity_key"
        UNIQUE ("generation_id", "lease_id", "window_id")
);

CREATE INDEX "LiteLLM_AccountPoolQuotaUsageEvent_window_occurred_at_idx"
    ON "LiteLLM_AccountPoolQuotaUsageEvent"("generation_id", "account_id", "window_id", "occurred_at");

CREATE INDEX "LiteLLM_AccountPoolQuotaUsageEvent_request_id_idx"
    ON "LiteLLM_AccountPoolQuotaUsageEvent"("request_id");

CREATE TABLE "LiteLLM_AccountPoolQuotaRuntimeSnapshot" (
    "generation_id" TEXT NOT NULL,
    "channel_id" TEXT,
    "account_id" TEXT NOT NULL,
    "window_id" TEXT NOT NULL,
    "scope" TEXT NOT NULL,
    "subject_id" TEXT,
    "kind" TEXT NOT NULL,
    "window_type" TEXT,
    "duration_seconds" INTEGER,
    "limit_value" NUMERIC(256, 36),
    "provider_remaining_value" NUMERIC(256, 36),
    "remaining_value" NUMERIC(256, 36),
    "reserved_value" NUMERIC(256, 36) NOT NULL DEFAULT 0,
    "safety_reserve_value" NUMERIC(256, 36) NOT NULL DEFAULT 0,
    "retry_at" TIMESTAMPTZ(6),
    "provider_reset_at" TIMESTAMPTZ(6),
    "provider_observed_at" TIMESTAMPTZ(6) NOT NULL,
    "provider_fingerprint" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "reason_code" TEXT NOT NULL,
    "captured_at" TIMESTAMPTZ(6) NOT NULL,
    "reservation_expires_at" TIMESTAMPTZ(6),

    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_pkey"
        PRIMARY KEY ("generation_id", "account_id", "window_id"),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_generation_id_fkey"
        FOREIGN KEY ("generation_id")
        REFERENCES "LiteLLM_AccountPoolQuotaGeneration"("generation_id")
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_scope_check"
        CHECK ("scope" IN ('channel', 'model', 'billing_route')),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_scope_subject_check"
        CHECK (("scope" = 'channel') = ("subject_id" IS NULL)),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_kind_check"
        CHECK ("kind" IN ('requests', 'tokens', 'credits', 'currency', 'provider_units')),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_window_type_check"
        CHECK ("window_type" IS NULL OR "window_type" IN ('rolling', 'fixed', 'reset_at', 'lifetime')),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_duration_check"
        CHECK ("duration_seconds" IS NULL OR "duration_seconds" > 0),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_rolling_duration_check"
        CHECK ("window_type" <> 'rolling' OR "duration_seconds" IS NOT NULL),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_reset_at_check"
        CHECK ("window_type" <> 'reset_at' OR "provider_reset_at" IS NOT NULL),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_amounts_check"
        CHECK (
            ("limit_value" IS NULL OR "limit_value" >= 0)
            AND ("provider_remaining_value" IS NULL OR "provider_remaining_value" >= 0)
            AND ("remaining_value" IS NULL OR "remaining_value" >= 0)
            AND "reserved_value" >= 0
            AND "safety_reserve_value" >= 0
        ),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_reservation_expiry_check"
        CHECK ("reserved_value" = 0 OR "reservation_expires_at" IS NOT NULL),
    CONSTRAINT "LiteLLM_AccountPoolQuotaRuntimeSnapshot_provider_fingerprint_check"
        CHECK ("provider_fingerprint" ~ '^[0-9a-f]{64}$')
);

CREATE INDEX "LiteLLM_AccountPoolQuotaRuntimeSnapshot_channel_id_idx"
    ON "LiteLLM_AccountPoolQuotaRuntimeSnapshot"("channel_id");

CREATE INDEX "LiteLLM_AccountPoolQuotaRuntimeSnapshot_captured_at_idx"
    ON "LiteLLM_AccountPoolQuotaRuntimeSnapshot"("generation_id", "captured_at" DESC);
