-- 本迁移创建无凭证公开元数据任务队列，并扩展对应的统一运行事件来源。

CREATE TABLE "LiteLLM_AccountPoolPublicMetadataTask" (
    "task_id" TEXT NOT NULL,
    "channel_id" TEXT NOT NULL,
    "parser_run_id" TEXT NOT NULL,
    "provider_id" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "attempt_count" INTEGER NOT NULL DEFAULT 0,
    "max_attempts" INTEGER NOT NULL DEFAULT 3,
    "owner_instance_id" TEXT,
    "next_attempt_at" TIMESTAMPTZ(6) NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,
    "started_at" TIMESTAMPTZ(6),
    "completed_at" TIMESTAMPTZ(6),
    "failure_code" TEXT,

    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_pkey" PRIMARY KEY ("task_id"),
    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_parser_run_id_key" UNIQUE ("parser_run_id"),
    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_channel_id_fkey"
        FOREIGN KEY ("channel_id") REFERENCES "LiteLLM_AccountPoolChannel"("channel_id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_status_check"
        CHECK ("status" IN ('queued', 'running', 'retry_wait', 'completed', 'failed')),
    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_attempt_count_check"
        CHECK ("attempt_count" >= 0 AND "max_attempts" BETWEEN 1 AND 10 AND "attempt_count" <= "max_attempts"),
    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_owner_check"
        CHECK (("status" = 'running') = ("owner_instance_id" IS NOT NULL)),
    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_started_check"
        CHECK (("status" = 'queued') = ("started_at" IS NULL)),
    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_running_attempt_check"
        CHECK ("status" <> 'running' OR "attempt_count" > 0),
    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_completion_check"
        CHECK (("status" IN ('completed', 'failed')) = ("completed_at" IS NOT NULL)),
    CONSTRAINT "LiteLLM_AccountPoolPublicMetadataTask_failure_check"
        CHECK (("status" IN ('retry_wait', 'failed')) = ("failure_code" IS NOT NULL))
);

CREATE INDEX "LiteLLM_AccountPoolPublicMetadataTask_channel_id_updated_at_idx"
    ON "LiteLLM_AccountPoolPublicMetadataTask"("channel_id", "updated_at" DESC);

CREATE INDEX "LiteLLM_AccountPoolPublicMetadataTask_status_next_attempt_at_created_at_idx"
    ON "LiteLLM_AccountPoolPublicMetadataTask"("status", "next_attempt_at", "created_at");

CREATE UNIQUE INDEX "LiteLLM_AccountPoolPublicMetadataTask_active_channel_key"
    ON "LiteLLM_AccountPoolPublicMetadataTask"("channel_id")
    WHERE "status" IN ('queued', 'running', 'retry_wait');

ALTER TABLE "LiteLLM_AccountPoolOperationalEvent"
    DROP CONSTRAINT "LiteLLM_AccountPoolOperationalEvent_source_check";

ALTER TABLE "LiteLLM_AccountPoolOperationalEvent"
    ADD CONSTRAINT "LiteLLM_AccountPoolOperationalEvent_source_check"
    CHECK (
        "source" IN (
            'parser_task',
            'parser_snapshot_export',
            'sync_reconcile',
            'request_lifecycle',
            'eligibility_transition',
            'public_metadata_task'
        )
    );
