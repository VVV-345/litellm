-- 本迁移持久化渠道同步状态、统一脱敏事件和管理操作审计事实。

CREATE TABLE "LiteLLM_AccountPoolSyncOperation" (
    "operation_id" TEXT NOT NULL,
    "idempotency_key" TEXT NOT NULL,
    "channel_id" TEXT NOT NULL,
    "action" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "delete_mode" TEXT,
    "desired_schema_version" INTEGER NOT NULL DEFAULT 1,
    "desired_payload" JSONB NOT NULL,
    "attempt_count" INTEGER NOT NULL DEFAULT 0,
    "requires_key" BOOLEAN NOT NULL DEFAULT FALSE,
    "failure_code" TEXT,
    "failure_message" TEXT,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,
    "applied_at" TIMESTAMPTZ(6),

    CONSTRAINT "LiteLLM_AccountPoolSyncOperation_pkey" PRIMARY KEY ("operation_id"),
    CONSTRAINT "LiteLLM_AccountPoolSyncOperation_desired_schema_version_check"
        CHECK ("desired_schema_version" = 1),
    CONSTRAINT "LiteLLM_AccountPoolSyncOperation_attempt_count_check"
        CHECK ("attempt_count" >= 0),
    CONSTRAINT "LiteLLM_AccountPoolSyncOperation_action_check"
        CHECK (
            "action" IN (
                'create_channel',
                'update_channel',
                'detach_channel',
                'delete_channel',
                'import_channel',
                'delete_external_deployment'
            )
        ),
    CONSTRAINT "LiteLLM_AccountPoolSyncOperation_action_status_check"
        CHECK (
            ("action" IN ('create_channel', 'import_channel') AND "status" IN ('pending_create', 'applied', 'failed'))
            OR ("action" = 'update_channel' AND "status" IN ('pending_update', 'applied', 'failed'))
            OR (
                "action" IN ('detach_channel', 'delete_channel', 'delete_external_deployment')
                AND "status" IN ('pending_delete', 'applied', 'failed')
            )
        ),
    CONSTRAINT "LiteLLM_AccountPoolSyncOperation_delete_mode_check"
        CHECK (
            ("action" = 'delete_channel' AND "delete_mode" IN ('detach_only', 'delete_managed_deployment'))
            OR ("action" <> 'delete_channel' AND "delete_mode" IS NULL)
        ),
    CONSTRAINT "LiteLLM_AccountPoolSyncOperation_failure_check"
        CHECK (
            ("status" = 'failed' AND "failure_code" IS NOT NULL AND "failure_message" IS NOT NULL)
            OR ("status" <> 'failed' AND "failure_code" IS NULL AND "failure_message" IS NULL)
        ),
    CONSTRAINT "LiteLLM_AccountPoolSyncOperation_applied_at_check"
        CHECK (("status" = 'applied') = ("applied_at" IS NOT NULL))
);

CREATE UNIQUE INDEX "LiteLLM_AccountPoolSyncOperation_idempotency_key_key"
    ON "LiteLLM_AccountPoolSyncOperation"("idempotency_key");

CREATE INDEX "LiteLLM_AccountPoolSyncOperation_channel_id_created_at_idx"
    ON "LiteLLM_AccountPoolSyncOperation"("channel_id", "created_at");

CREATE INDEX "LiteLLM_AccountPoolSyncOperation_status_created_at_idx"
    ON "LiteLLM_AccountPoolSyncOperation"("status", "created_at");

CREATE TABLE "LiteLLM_AccountPoolEvent" (
    "event_id" TEXT NOT NULL,
    "event_type" TEXT NOT NULL,
    "occurred_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "channel_id" TEXT,
    "model_id" TEXT,
    "deployment_id" TEXT,
    "request_id" TEXT,
    "lease_id" TEXT,
    "reason_code" TEXT,
    "actor_type" TEXT,
    "actor_id" TEXT,
    "safe_details_schema_version" INTEGER NOT NULL DEFAULT 1,
    "safe_details" JSONB NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolEvent_pkey" PRIMARY KEY ("event_id"),
    CONSTRAINT "LiteLLM_AccountPoolEvent_safe_details_schema_version_check"
        CHECK ("safe_details_schema_version" = 1)
);

CREATE INDEX "LiteLLM_AccountPoolEvent_occurred_at_event_id_idx"
    ON "LiteLLM_AccountPoolEvent"("occurred_at" DESC, "event_id" DESC);

CREATE INDEX "LiteLLM_AccountPoolEvent_channel_id_occurred_at_idx"
    ON "LiteLLM_AccountPoolEvent"("channel_id", "occurred_at" DESC);

CREATE INDEX "LiteLLM_AccountPoolEvent_model_id_occurred_at_idx"
    ON "LiteLLM_AccountPoolEvent"("model_id", "occurred_at" DESC);

CREATE INDEX "LiteLLM_AccountPoolEvent_request_id_occurred_at_idx"
    ON "LiteLLM_AccountPoolEvent"("request_id", "occurred_at" DESC);

CREATE INDEX "LiteLLM_AccountPoolEvent_event_type_occurred_at_idx"
    ON "LiteLLM_AccountPoolEvent"("event_type", "occurred_at" DESC);

CREATE INDEX "LiteLLM_AccountPoolEvent_reason_code_occurred_at_idx"
    ON "LiteLLM_AccountPoolEvent"("reason_code", "occurred_at" DESC);

CREATE TABLE "LiteLLM_AccountPoolAuditEvent" (
    "event_id" TEXT NOT NULL,
    "operation_id" TEXT,
    "actor_role" TEXT NOT NULL,
    "actor_action" TEXT NOT NULL,
    "actor_envelope_id" TEXT NOT NULL,
    "outcome" TEXT NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolAuditEvent_pkey" PRIMARY KEY ("event_id"),
    CONSTRAINT "LiteLLM_AccountPoolAuditEvent_event_id_fkey"
        FOREIGN KEY ("event_id") REFERENCES "LiteLLM_AccountPoolEvent"("event_id")
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "LiteLLM_AccountPoolAuditEvent_operation_id_fkey"
        FOREIGN KEY ("operation_id") REFERENCES "LiteLLM_AccountPoolSyncOperation"("operation_id")
        ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE INDEX "LiteLLM_AccountPoolAuditEvent_operation_id_idx"
    ON "LiteLLM_AccountPoolAuditEvent"("operation_id");

CREATE INDEX "LiteLLM_AccountPoolAuditEvent_actor_envelope_id_idx"
    ON "LiteLLM_AccountPoolAuditEvent"("actor_envelope_id");

ALTER TABLE "LiteLLM_AccountPoolChannel"
DROP CONSTRAINT "LiteLLM_AccountPoolChannel_administrative_state_check",
ADD CONSTRAINT "LiteLLM_AccountPoolChannel_administrative_state_check"
CHECK ("administrative_state" IN ('enabled', 'paused', 'disabled', 'pending_delete'));
