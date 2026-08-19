-- 本迁移持久化脱敏健康事实，并按 Deployment 维护最近请求、成功、失败和探测时间。

CREATE TABLE "LiteLLM_AccountPoolHealthEvent" (
    "event_id" TEXT NOT NULL,
    "account_id" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "outcome" TEXT NOT NULL,
    "transition" TEXT NOT NULL,
    "scope" TEXT NOT NULL,
    "retry_at" TIMESTAMPTZ(6),
    "probe_trigger" TEXT,

    CONSTRAINT "LiteLLM_AccountPoolHealthEvent_pkey" PRIMARY KEY ("event_id"),
    CONSTRAINT "LiteLLM_AccountPoolHealthEvent_event_id_fkey"
        FOREIGN KEY ("event_id") REFERENCES "LiteLLM_AccountPoolEvent"("event_id")
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "LiteLLM_AccountPoolHealthEvent_source_check"
        CHECK ("source" IN ('passive_request', 'active_probe')),
    CONSTRAINT "LiteLLM_AccountPoolHealthEvent_outcome_check"
        CHECK ("outcome" IN ('succeeded', 'failed')),
    CONSTRAINT "LiteLLM_AccountPoolHealthEvent_transition_check"
        CHECK ("transition" IN ('success', 'disable', 'cooldown', 'observe', 'transient_failure')),
    CONSTRAINT "LiteLLM_AccountPoolHealthEvent_scope_check"
        CHECK ("scope" IN ('channel', 'model', 'deployment', 'billing_route')),
    CONSTRAINT "LiteLLM_AccountPoolHealthEvent_probe_trigger_check"
        CHECK (
            ("source" = 'active_probe' AND "probe_trigger" IN ('manual', 'initial', 'half_open', 'idle'))
            OR ("source" = 'passive_request' AND "probe_trigger" IS NULL)
        )
);

CREATE INDEX "LiteLLM_AccountPoolHealthEvent_account_id_idx"
    ON "LiteLLM_AccountPoolHealthEvent"("account_id");

CREATE INDEX "LiteLLM_AccountPoolHealthEvent_source_outcome_idx"
    ON "LiteLLM_AccountPoolHealthEvent"("source", "outcome");

CREATE TABLE "LiteLLM_AccountPoolHealthActivity" (
    "account_id" TEXT NOT NULL,
    "deployment_id" TEXT NOT NULL,
    "channel_id" TEXT,
    "model_id" TEXT NOT NULL,
    "last_request_at" TIMESTAMPTZ(6),
    "last_success_at" TIMESTAMPTZ(6),
    "last_failure_at" TIMESTAMPTZ(6),
    "last_probe_at" TIMESTAMPTZ(6),
    "last_probe_success_at" TIMESTAMPTZ(6),
    "last_probe_failure_at" TIMESTAMPTZ(6),
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolHealthActivity_pkey"
        PRIMARY KEY ("account_id", "deployment_id"),
    CONSTRAINT "LiteLLM_AccountPoolHealthActivity_request_bounds_check"
        CHECK (
            ("last_success_at" IS NULL OR "last_request_at" IS NULL OR "last_success_at" <= "last_request_at")
            AND ("last_failure_at" IS NULL OR "last_request_at" IS NULL OR "last_failure_at" <= "last_request_at")
        ),
    CONSTRAINT "LiteLLM_AccountPoolHealthActivity_probe_bounds_check"
        CHECK (
            ("last_probe_success_at" IS NULL OR "last_probe_at" IS NULL OR "last_probe_success_at" <= "last_probe_at")
            AND ("last_probe_failure_at" IS NULL OR "last_probe_at" IS NULL OR "last_probe_failure_at" <= "last_probe_at")
        )
);

CREATE INDEX "LiteLLM_AccountPoolHealthActivity_channel_id_idx"
    ON "LiteLLM_AccountPoolHealthActivity"("channel_id");

CREATE INDEX "LiteLLM_AccountPoolHealthActivity_idle_idx"
    ON "LiteLLM_AccountPoolHealthActivity"("last_request_at", "last_probe_at");
