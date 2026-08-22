-- 本迁移为统一事件日志增加脱敏系统运行事实，首批覆盖解析任务终态。

CREATE TABLE "LiteLLM_AccountPoolOperationalEvent" (
    "event_id" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "operation_id" TEXT NOT NULL,
    "outcome" TEXT NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolOperationalEvent_pkey" PRIMARY KEY ("event_id"),
    CONSTRAINT "LiteLLM_AccountPoolOperationalEvent_event_id_fkey"
        FOREIGN KEY ("event_id") REFERENCES "LiteLLM_AccountPoolEvent"("event_id")
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "LiteLLM_AccountPoolOperationalEvent_source_check"
        CHECK ("source" IN ('parser_task')),
    CONSTRAINT "LiteLLM_AccountPoolOperationalEvent_outcome_check"
        CHECK ("outcome" IN ('succeeded', 'failed', 'interrupted'))
);

CREATE INDEX "LiteLLM_AccountPoolOperationalEvent_operation_id_idx"
    ON "LiteLLM_AccountPoolOperationalEvent"("operation_id");

CREATE INDEX "LiteLLM_AccountPoolOperationalEvent_source_outcome_idx"
    ON "LiteLLM_AccountPoolOperationalEvent"("source", "outcome");
