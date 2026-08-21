-- 本迁移持久化 Account Pool Deployment 绑定的成功请求延迟 EWMA 快照。

CREATE TABLE "LiteLLM_AccountPoolLatencyMetric" (
    "binding_id" TEXT NOT NULL,
    "ewma_ms" DOUBLE PRECISION NOT NULL,
    "sample_count" BIGINT NOT NULL,
    "observed_at" TIMESTAMPTZ(6) NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolLatencyMetric_pkey" PRIMARY KEY ("binding_id"),
    CONSTRAINT "LiteLLM_AccountPoolLatencyMetric_binding_id_fkey"
        FOREIGN KEY ("binding_id") REFERENCES "LiteLLM_AccountPoolBinding"("binding_id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "LiteLLM_AccountPoolLatencyMetric_ewma_ms_check" CHECK ("ewma_ms" > 0),
    CONSTRAINT "LiteLLM_AccountPoolLatencyMetric_sample_count_check" CHECK ("sample_count" > 0)
);

CREATE INDEX "LiteLLM_AccountPoolLatencyMetric_observed_at_idx"
ON "LiteLLM_AccountPoolLatencyMetric"("observed_at");
