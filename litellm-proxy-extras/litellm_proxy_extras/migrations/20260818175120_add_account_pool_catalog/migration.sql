-- 本迁移创建号池渠道、模型绑定和调度策略的数据底座及约束。

-- CreateTable
CREATE TABLE "LiteLLM_AccountPoolChannel" (
    "channel_id" TEXT NOT NULL,
    "legacy_account_id" TEXT,
    "account_order" INTEGER NOT NULL,
    "display_name" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "channel_group" TEXT,
    "base_url_display" TEXT NOT NULL,
    "administrative_state" TEXT NOT NULL,
    "max_concurrency" INTEGER NOT NULL,
    "priority" INTEGER NOT NULL,
    "weight" INTEGER NOT NULL,
    "quota_unit" TEXT NOT NULL,
    "quota_total" DOUBLE PRECISION,
    "quota_five_hour" DOUBLE PRECISION,
    "quota_weekly" DOUBLE PRECISION,
    "credential_ref" TEXT,
    "key_mask" TEXT,
    "key_fingerprint" TEXT,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolChannel_pkey" PRIMARY KEY ("channel_id")
);

-- CreateTable
CREATE TABLE "LiteLLM_AccountPoolBinding" (
    "binding_id" TEXT NOT NULL,
    "channel_id" TEXT NOT NULL,
    "deployment_order" INTEGER NOT NULL,
    "public_model" TEXT NOT NULL,
    "provider_model" TEXT,
    "litellm_deployment_id" TEXT NOT NULL,
    "ownership" TEXT NOT NULL,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolBinding_pkey" PRIMARY KEY ("binding_id")
);

-- CreateTable
CREATE TABLE "LiteLLM_AccountPoolModelPolicy" (
    "model" TEXT NOT NULL,
    "policy_order" INTEGER NOT NULL,
    "strategy" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "LiteLLM_AccountPoolModelPolicy_pkey" PRIMARY KEY ("model")
);

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_AccountPoolChannel_legacy_account_id_key" ON "LiteLLM_AccountPoolChannel"("legacy_account_id");

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_AccountPoolChannel_account_order_key" ON "LiteLLM_AccountPoolChannel"("account_order");

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_AccountPoolBinding_litellm_deployment_id_key" ON "LiteLLM_AccountPoolBinding"("litellm_deployment_id");

-- CreateIndex
CREATE INDEX "LiteLLM_AccountPoolBinding_channel_id_idx" ON "LiteLLM_AccountPoolBinding"("channel_id");

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_AccountPoolBinding_channel_id_deployment_order_key" ON "LiteLLM_AccountPoolBinding"("channel_id", "deployment_order");

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_AccountPoolModelPolicy_policy_order_key" ON "LiteLLM_AccountPoolModelPolicy"("policy_order");

-- AddForeignKey
ALTER TABLE "LiteLLM_AccountPoolBinding" ADD CONSTRAINT "LiteLLM_AccountPoolBinding_channel_id_fkey" FOREIGN KEY ("channel_id") REFERENCES "LiteLLM_AccountPoolChannel"("channel_id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "LiteLLM_AccountPoolChannel"
ADD CONSTRAINT "LiteLLM_AccountPoolChannel_administrative_state_check"
CHECK ("administrative_state" IN ('enabled', 'paused', 'disabled')),
ADD CONSTRAINT "LiteLLM_AccountPoolChannel_quota_unit_check"
CHECK ("quota_unit" IN ('tokens', 'usd')),
ADD CONSTRAINT "LiteLLM_AccountPoolChannel_account_order_check"
CHECK ("account_order" >= 0),
ADD CONSTRAINT "LiteLLM_AccountPoolChannel_max_concurrency_check"
CHECK ("max_concurrency" >= 1),
ADD CONSTRAINT "LiteLLM_AccountPoolChannel_weight_check"
CHECK ("weight" BETWEEN 1 AND 100);

ALTER TABLE "LiteLLM_AccountPoolBinding"
ADD CONSTRAINT "LiteLLM_AccountPoolBinding_ownership_check"
CHECK ("ownership" IN ('pool_managed', 'externally_managed')),
ADD CONSTRAINT "LiteLLM_AccountPoolBinding_deployment_order_check"
CHECK ("deployment_order" >= 0);

ALTER TABLE "LiteLLM_AccountPoolModelPolicy"
ADD CONSTRAINT "LiteLLM_AccountPoolModelPolicy_policy_order_check"
CHECK ("policy_order" >= 0);
