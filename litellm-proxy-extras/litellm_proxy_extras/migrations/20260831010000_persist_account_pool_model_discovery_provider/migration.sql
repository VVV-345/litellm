-- 本迁移持久化渠道的模型发现厂商，且不影响独立解析器配置。

ALTER TABLE "LiteLLM_AccountPoolChannel"
    ADD COLUMN "model_discovery_provider_id" TEXT;
