-- 本迁移将解析器来源从上游厂商标识中分离，避免添加渠道时自动选择解析器。

ALTER TABLE "LiteLLM_AccountPoolChannel"
    ADD COLUMN "parser_provider_id" TEXT;
