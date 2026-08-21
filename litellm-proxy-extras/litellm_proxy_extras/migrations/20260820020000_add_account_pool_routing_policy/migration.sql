-- 本迁移为模型调度策略增加乐观并发版本，并持久化模型级候选顺序、权重和暂停覆盖。

ALTER TABLE "LiteLLM_AccountPoolModelPolicy"
ADD COLUMN "version" INTEGER NOT NULL DEFAULT 1;

ALTER TABLE "LiteLLM_AccountPoolModelPolicy"
ADD CONSTRAINT "LiteLLM_AccountPoolModelPolicy_version_check"
CHECK ("version" >= 1);

CREATE TABLE "LiteLLM_AccountPoolModelCandidateOverride" (
    "model" TEXT NOT NULL,
    "binding_id" TEXT NOT NULL,
    "manual_order" INTEGER,
    "weight" INTEGER,
    "paused" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolModelCandidateOverride_pkey"
    PRIMARY KEY ("model", "binding_id")
);

CREATE INDEX "LiteLLM_AccountPoolModelCandidateOverride_binding_id_idx"
ON "LiteLLM_AccountPoolModelCandidateOverride"("binding_id");

CREATE UNIQUE INDEX "LiteLLM_AccountPoolModelCandidateOverride_model_manual_order_key"
ON "LiteLLM_AccountPoolModelCandidateOverride"("model", "manual_order");

ALTER TABLE "LiteLLM_AccountPoolModelCandidateOverride"
ADD CONSTRAINT "LiteLLM_AccountPoolModelCandidateOverride_model_fkey"
FOREIGN KEY ("model") REFERENCES "LiteLLM_AccountPoolModelPolicy"("model")
ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "LiteLLM_AccountPoolModelCandidateOverride"
ADD CONSTRAINT "LiteLLM_AccountPoolModelCandidateOverride_binding_id_fkey"
FOREIGN KEY ("binding_id") REFERENCES "LiteLLM_AccountPoolBinding"("binding_id")
ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "LiteLLM_AccountPoolModelCandidateOverride"
ADD CONSTRAINT "LiteLLM_AccountPoolModelCandidateOverride_manual_order_check"
CHECK ("manual_order" IS NULL OR "manual_order" >= 0),
ADD CONSTRAINT "LiteLLM_AccountPoolModelCandidateOverride_weight_check"
CHECK ("weight" IS NULL OR "weight" BETWEEN 1 AND 100);
