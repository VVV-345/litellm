-- 本迁移持久化额度恢复隔离截止时间，并限制同一前置代次只能存在一个初始化中的恢复代次。

ALTER TABLE "LiteLLM_AccountPoolQuotaGeneration"
    ADD COLUMN "isolation_until" TIMESTAMPTZ(6);

WITH ranked AS (
    SELECT
        "generation_id",
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE("predecessor_generation_id", '')
            ORDER BY "created_at", "generation_id"
        ) AS position
    FROM "LiteLLM_AccountPoolQuotaGeneration"
    WHERE "status" = 'initializing'
)
UPDATE "LiteLLM_AccountPoolQuotaGeneration" AS generation
SET
    "status" = 'failed',
    "closed_at" = NOW(),
    "failure_code" = 'superseded_recovery_generation'
FROM ranked
WHERE generation."generation_id" = ranked."generation_id"
  AND ranked.position > 1;

CREATE UNIQUE INDEX "LiteLLM_AccountPoolQuotaGeneration_single_initializing_successor_idx"
    ON "LiteLLM_AccountPoolQuotaGeneration" ((COALESCE("predecessor_generation_id", '')))
    WHERE "status" = 'initializing';
