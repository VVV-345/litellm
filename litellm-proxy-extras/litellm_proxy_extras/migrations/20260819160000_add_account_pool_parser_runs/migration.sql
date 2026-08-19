-- 本迁移为号池解析运行、套餐、额度、按量价格和计费路由建立规范化持久化表。

CREATE TABLE "LiteLLM_AccountPoolParserRun" (
    "parser_run_id" TEXT NOT NULL,
    "channel_id" TEXT NOT NULL,
    "parser_id" TEXT NOT NULL,
    "parser_version" TEXT NOT NULL,
    "parsed_at" TIMESTAMPTZ(6) NOT NULL,
    "status" TEXT NOT NULL,
    "content_hash" TEXT NOT NULL,
    "discovered_models" JSONB NOT NULL DEFAULT '[]'::JSONB,
    "capabilities" JSONB NOT NULL DEFAULT '[]'::JSONB,
    "unresolved_fields" JSONB NOT NULL DEFAULT '[]'::JSONB,
    "evidence" JSONB NOT NULL DEFAULT '[]'::JSONB,
    "warnings" JSONB NOT NULL DEFAULT '[]'::JSONB,
    "issues" JSONB NOT NULL DEFAULT '[]'::JSONB,
    "has_metered" BOOLEAN NOT NULL DEFAULT FALSE,
    "export_status" TEXT NOT NULL DEFAULT 'pending',
    "export_attempt_count" INTEGER NOT NULL DEFAULT 0,
    "export_last_attempt_at" TIMESTAMPTZ(6),
    "exported_at" TIMESTAMPTZ(6),
    "export_failure_code" TEXT,
    "export_failure_retryable" BOOLEAN,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolParserRun_pkey" PRIMARY KEY ("parser_run_id")
);

CREATE TABLE "LiteLLM_AccountPoolSubscriptionSnapshot" (
    "subscription_snapshot_id" TEXT NOT NULL,
    "parser_run_id" TEXT NOT NULL,
    "plan_id" TEXT,
    "plan_name" TEXT,
    "status" TEXT NOT NULL,
    "starts_at" TIMESTAMPTZ(6),
    "expires_at" TIMESTAMPTZ(6),
    "models" JSONB NOT NULL DEFAULT '[]'::JSONB,
    "balance" DECIMAL(65,30),
    "currency" TEXT,
    "channel_concurrency" INTEGER,
    "model_concurrency" JSONB NOT NULL DEFAULT '[]'::JSONB,

    CONSTRAINT "LiteLLM_AccountPoolSubscriptionSnapshot_pkey" PRIMARY KEY ("subscription_snapshot_id")
);

CREATE TABLE "LiteLLM_AccountPoolQuotaLimit" (
    "quota_limit_id" TEXT NOT NULL,
    "parser_run_id" TEXT NOT NULL,
    "limit_order" INTEGER NOT NULL,
    "scope" TEXT NOT NULL,
    "subject_id" TEXT,
    "kind" TEXT NOT NULL,
    "window_type" TEXT,
    "duration_seconds" BIGINT,
    "limit_value" DECIMAL(65,30),
    "used_value" DECIMAL(65,30),
    "remaining_value" DECIMAL(65,30),
    "reset_at" TIMESTAMPTZ(6),
    "source" TEXT NOT NULL,
    "observed_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolQuotaLimit_pkey" PRIMARY KEY ("quota_limit_id")
);

CREATE TABLE "LiteLLM_AccountPoolMeteredGroup" (
    "metered_group_row_id" TEXT NOT NULL,
    "parser_run_id" TEXT NOT NULL,
    "group_order" INTEGER NOT NULL,
    "group_id" TEXT,
    "group_name" TEXT,
    "concurrency" INTEGER,

    CONSTRAINT "LiteLLM_AccountPoolMeteredGroup_pkey" PRIMARY KEY ("metered_group_row_id")
);

CREATE TABLE "LiteLLM_AccountPoolMeteredPrice" (
    "metered_price_id" TEXT NOT NULL,
    "metered_group_row_id" TEXT NOT NULL,
    "price_order" INTEGER NOT NULL,
    "provider_model_id" TEXT NOT NULL,
    "litellm_model_name" TEXT,
    "public_model_name" TEXT,
    "currency" TEXT NOT NULL,
    "unit" TEXT NOT NULL,
    "input_price" DECIMAL(65,30),
    "output_price" DECIMAL(65,30),
    "cache_read_price" DECIMAL(65,30),
    "cache_write_price" DECIMAL(65,30),
    "group_multiplier" DECIMAL(65,30) NOT NULL,
    "price_calculation" TEXT NOT NULL,
    "conversion_note" TEXT,
    "effective_input_price" DECIMAL(65,30),
    "effective_output_price" DECIMAL(65,30),
    "effective_cache_read_price" DECIMAL(65,30),
    "effective_cache_write_price" DECIMAL(65,30),
    "normalized_input_price" DECIMAL(65,30),
    "normalized_output_price" DECIMAL(65,30),
    "normalized_cache_read_price" DECIMAL(65,30),
    "normalized_cache_write_price" DECIMAL(65,30),
    "has_normalized_prices" BOOLEAN NOT NULL DEFAULT FALSE,
    "concurrency" INTEGER,

    CONSTRAINT "LiteLLM_AccountPoolMeteredPrice_pkey" PRIMARY KEY ("metered_price_id")
);

CREATE TABLE "LiteLLM_AccountPoolBillingRoute" (
    "billing_route_row_id" TEXT NOT NULL,
    "parser_run_id" TEXT NOT NULL,
    "route_order" INTEGER NOT NULL,
    "route_id" TEXT NOT NULL,
    "deployment_binding_id" TEXT NOT NULL,
    "mode" TEXT NOT NULL,
    "provider_group_id" TEXT,
    "request_parameter_ref" TEXT,

    CONSTRAINT "LiteLLM_AccountPoolBillingRoute_pkey" PRIMARY KEY ("billing_route_row_id")
);

CREATE UNIQUE INDEX "LiteLLM_AccountPoolSubscriptionSnapshot_parser_run_id_key"
ON "LiteLLM_AccountPoolSubscriptionSnapshot"("parser_run_id");
CREATE UNIQUE INDEX "LiteLLM_AccountPoolQuotaLimit_parser_run_id_limit_order_key"
ON "LiteLLM_AccountPoolQuotaLimit"("parser_run_id", "limit_order");
CREATE INDEX "LiteLLM_AccountPoolQuotaLimit_parser_run_id_idx"
ON "LiteLLM_AccountPoolQuotaLimit"("parser_run_id");
CREATE UNIQUE INDEX "LiteLLM_AccountPoolMeteredGroup_parser_run_id_group_order_key"
ON "LiteLLM_AccountPoolMeteredGroup"("parser_run_id", "group_order");
CREATE INDEX "LiteLLM_AccountPoolMeteredGroup_parser_run_id_idx"
ON "LiteLLM_AccountPoolMeteredGroup"("parser_run_id");
CREATE UNIQUE INDEX "LiteLLM_AccountPoolMeteredPrice_metered_group_row_id_price_order_key"
ON "LiteLLM_AccountPoolMeteredPrice"("metered_group_row_id", "price_order");
CREATE INDEX "LiteLLM_AccountPoolMeteredPrice_metered_group_row_id_idx"
ON "LiteLLM_AccountPoolMeteredPrice"("metered_group_row_id");
CREATE UNIQUE INDEX "LiteLLM_AccountPoolBillingRoute_parser_run_id_route_order_key"
ON "LiteLLM_AccountPoolBillingRoute"("parser_run_id", "route_order");
CREATE UNIQUE INDEX "LiteLLM_AccountPoolBillingRoute_parser_run_id_route_id_key"
ON "LiteLLM_AccountPoolBillingRoute"("parser_run_id", "route_id");
CREATE INDEX "LiteLLM_AccountPoolBillingRoute_parser_run_id_idx"
ON "LiteLLM_AccountPoolBillingRoute"("parser_run_id");
CREATE INDEX "LiteLLM_AccountPoolParserRun_channel_id_parsed_at_idx"
ON "LiteLLM_AccountPoolParserRun"("channel_id", "parsed_at");
CREATE INDEX "LiteLLM_AccountPoolParserRun_export_status_parsed_at_idx"
ON "LiteLLM_AccountPoolParserRun"("export_status", "parsed_at");

ALTER TABLE "LiteLLM_AccountPoolParserRun"
ADD CONSTRAINT "LiteLLM_AccountPoolParserRun_channel_id_fkey"
FOREIGN KEY ("channel_id") REFERENCES "LiteLLM_AccountPoolChannel"("channel_id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_AccountPoolSubscriptionSnapshot"
ADD CONSTRAINT "LiteLLM_AccountPoolSubscriptionSnapshot_parser_run_id_fkey"
FOREIGN KEY ("parser_run_id") REFERENCES "LiteLLM_AccountPoolParserRun"("parser_run_id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_AccountPoolQuotaLimit"
ADD CONSTRAINT "LiteLLM_AccountPoolQuotaLimit_parser_run_id_fkey"
FOREIGN KEY ("parser_run_id") REFERENCES "LiteLLM_AccountPoolParserRun"("parser_run_id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_AccountPoolMeteredGroup"
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredGroup_parser_run_id_fkey"
FOREIGN KEY ("parser_run_id") REFERENCES "LiteLLM_AccountPoolParserRun"("parser_run_id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_AccountPoolMeteredPrice"
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredPrice_metered_group_row_id_fkey"
FOREIGN KEY ("metered_group_row_id") REFERENCES "LiteLLM_AccountPoolMeteredGroup"("metered_group_row_id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_AccountPoolBillingRoute"
ADD CONSTRAINT "LiteLLM_AccountPoolBillingRoute_parser_run_id_fkey"
FOREIGN KEY ("parser_run_id") REFERENCES "LiteLLM_AccountPoolParserRun"("parser_run_id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "LiteLLM_AccountPoolParserRun"
ADD CONSTRAINT "LiteLLM_AccountPoolParserRun_status_check"
CHECK ("status" IN ('success', 'partial', 'unsupported', 'authentication_failed', 'transport_failed', 'invalid_response', 'manual_required')),
ADD CONSTRAINT "LiteLLM_AccountPoolParserRun_export_status_check"
CHECK ("export_status" IN ('pending', 'succeeded', 'retryable_failure', 'permanent_failure')),
ADD CONSTRAINT "LiteLLM_AccountPoolParserRun_export_attempt_count_check"
CHECK ("export_attempt_count" >= 0),
ADD CONSTRAINT "LiteLLM_AccountPoolParserRun_export_state_check"
CHECK (
    ("export_status" = 'pending'
        AND "export_attempt_count" = 0
        AND "export_last_attempt_at" IS NULL
        AND "exported_at" IS NULL
        AND "export_failure_code" IS NULL
        AND "export_failure_retryable" IS NULL)
 OR ("export_status" = 'succeeded'
        AND "export_attempt_count" >= 1
        AND "export_last_attempt_at" IS NOT NULL
        AND "exported_at" IS NOT NULL
        AND "export_failure_code" IS NULL
        AND "export_failure_retryable" IS NULL)
 OR ("export_status" IN ('retryable_failure', 'permanent_failure')
        AND "export_attempt_count" >= 1
        AND "export_last_attempt_at" IS NOT NULL
        AND "exported_at" IS NULL
        AND "export_failure_code" IS NOT NULL
        AND "export_failure_retryable" IS NOT NULL
        AND "export_failure_retryable" = ("export_status" = 'retryable_failure'))
),
ADD CONSTRAINT "LiteLLM_AccountPoolParserRun_content_hash_check"
CHECK ("content_hash" ~ '^[0-9a-f]{64}$');

ALTER TABLE "LiteLLM_AccountPoolSubscriptionSnapshot"
ADD CONSTRAINT "LiteLLM_AccountPoolSubscriptionSnapshot_status_check"
CHECK ("status" IN ('active', 'trial', 'expired', 'suspended', 'unknown')),
ADD CONSTRAINT "LiteLLM_AccountPoolSubscriptionSnapshot_balance_check"
CHECK ("balance" IS NULL OR "balance" >= 0),
ADD CONSTRAINT "LiteLLM_AccountPoolSubscriptionSnapshot_channel_concurrency_check"
CHECK ("channel_concurrency" IS NULL OR "channel_concurrency" >= 1);

ALTER TABLE "LiteLLM_AccountPoolQuotaLimit"
ADD CONSTRAINT "LiteLLM_AccountPoolQuotaLimit_limit_order_check" CHECK ("limit_order" >= 0),
ADD CONSTRAINT "LiteLLM_AccountPoolQuotaLimit_scope_check" CHECK ("scope" IN ('channel', 'model', 'group')),
ADD CONSTRAINT "LiteLLM_AccountPoolQuotaLimit_kind_check"
CHECK ("kind" IN ('requests', 'tokens', 'credits', 'currency', 'provider_units')),
ADD CONSTRAINT "LiteLLM_AccountPoolQuotaLimit_window_type_check"
CHECK ("window_type" IS NULL OR "window_type" IN ('rolling', 'fixed', 'reset_at', 'lifetime')),
ADD CONSTRAINT "LiteLLM_AccountPoolQuotaLimit_duration_check"
CHECK ("duration_seconds" IS NULL OR "duration_seconds" >= 1),
ADD CONSTRAINT "LiteLLM_AccountPoolQuotaLimit_values_check"
CHECK (("limit_value" IS NULL OR "limit_value" >= 0)
   AND ("used_value" IS NULL OR "used_value" >= 0)
   AND ("remaining_value" IS NULL OR "remaining_value" >= 0)),
ADD CONSTRAINT "LiteLLM_AccountPoolQuotaLimit_window_fields_check"
CHECK (("window_type" <> 'rolling' OR "duration_seconds" IS NOT NULL)
   AND ("window_type" <> 'reset_at' OR "reset_at" IS NOT NULL));

ALTER TABLE "LiteLLM_AccountPoolMeteredGroup"
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredGroup_group_order_check" CHECK ("group_order" >= 0),
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredGroup_concurrency_check"
CHECK ("concurrency" IS NULL OR "concurrency" >= 1);

ALTER TABLE "LiteLLM_AccountPoolMeteredPrice"
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredPrice_price_order_check" CHECK ("price_order" >= 0),
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredPrice_price_calculation_check"
CHECK ("price_calculation" IN ('multiplier', 'provider_normalized')),
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredPrice_conversion_note_check"
CHECK ("price_calculation" <> 'provider_normalized' OR "conversion_note" IS NOT NULL),
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredPrice_group_multiplier_check" CHECK ("group_multiplier" > 0),
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredPrice_concurrency_check"
CHECK ("concurrency" IS NULL OR "concurrency" >= 1),
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredPrice_values_check"
CHECK (("input_price" IS NULL OR "input_price" >= 0)
   AND ("output_price" IS NULL OR "output_price" >= 0)
   AND ("cache_read_price" IS NULL OR "cache_read_price" >= 0)
   AND ("cache_write_price" IS NULL OR "cache_write_price" >= 0)
   AND ("effective_input_price" IS NULL OR "effective_input_price" >= 0)
   AND ("effective_output_price" IS NULL OR "effective_output_price" >= 0)
   AND ("effective_cache_read_price" IS NULL OR "effective_cache_read_price" >= 0)
   AND ("effective_cache_write_price" IS NULL OR "effective_cache_write_price" >= 0)
   AND ("normalized_input_price" IS NULL OR "normalized_input_price" >= 0)
   AND ("normalized_output_price" IS NULL OR "normalized_output_price" >= 0)
   AND ("normalized_cache_read_price" IS NULL OR "normalized_cache_read_price" >= 0)
   AND ("normalized_cache_write_price" IS NULL OR "normalized_cache_write_price" >= 0)),
ADD CONSTRAINT "LiteLLM_AccountPoolMeteredPrice_normalized_presence_check"
CHECK ("has_normalized_prices"
    OR ("normalized_input_price" IS NULL
        AND "normalized_output_price" IS NULL
        AND "normalized_cache_read_price" IS NULL
        AND "normalized_cache_write_price" IS NULL));

ALTER TABLE "LiteLLM_AccountPoolBillingRoute"
ADD CONSTRAINT "LiteLLM_AccountPoolBillingRoute_route_order_check" CHECK ("route_order" >= 0),
ADD CONSTRAINT "LiteLLM_AccountPoolBillingRoute_mode_check" CHECK ("mode" IN ('subscription', 'metered'));
