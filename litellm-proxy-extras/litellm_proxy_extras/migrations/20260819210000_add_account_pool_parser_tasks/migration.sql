-- 本迁移保存不含凭证的解析任务所有权、心跳、结果和中断恢复状态。

CREATE TABLE "LiteLLM_AccountPoolParserTask" (
    "task_id" TEXT NOT NULL,
    "channel_id" TEXT NOT NULL,
    "parser_run_id" TEXT NOT NULL,
    "provider_id" TEXT NOT NULL,
    "explicit_parser_id" TEXT,
    "openai_compatible" BOOLEAN NOT NULL DEFAULT FALSE,
    "status" TEXT NOT NULL,
    "owner_instance_id" TEXT NOT NULL,
    "actor_id" TEXT NOT NULL,
    "actor_role" TEXT NOT NULL,
    "request_id" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL,
    "heartbeat_at" TIMESTAMPTZ(6) NOT NULL,
    "completed_at" TIMESTAMPTZ(6),
    "failure_code" TEXT,

    CONSTRAINT "LiteLLM_AccountPoolParserTask_pkey" PRIMARY KEY ("task_id"),
    CONSTRAINT "LiteLLM_AccountPoolParserTask_parser_run_id_key" UNIQUE ("parser_run_id"),
    CONSTRAINT "LiteLLM_AccountPoolParserTask_channel_id_fkey"
        FOREIGN KEY ("channel_id") REFERENCES "LiteLLM_AccountPoolChannel"("channel_id")
        ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE INDEX "LiteLLM_AccountPoolParserTask_channel_id_created_at_idx"
    ON "LiteLLM_AccountPoolParserTask"("channel_id", "created_at");

CREATE INDEX "LiteLLM_AccountPoolParserTask_status_heartbeat_at_idx"
    ON "LiteLLM_AccountPoolParserTask"("status", "heartbeat_at");
