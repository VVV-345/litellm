-- 本迁移为人工覆盖审计补充已验证的操作者角色和请求关联标识。

ALTER TABLE "LiteLLM_AccountPoolFieldOverride"
ADD COLUMN "actor_role" TEXT,
ADD COLUMN "request_id" TEXT;

ALTER TABLE "LiteLLM_AccountPoolFieldOverride"
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_actor_context_check"
CHECK (("actor_role" IS NULL) = ("request_id" IS NULL)
   AND ("actor_role" IS NULL OR length("actor_role") > 0)
   AND ("request_id" IS NULL OR "request_id" ~ '^[A-Za-z0-9._:-]{1,128}$'));

CREATE INDEX "LiteLLM_AccountPoolFieldOverride_request_id_occurred_at_idx"
ON "LiteLLM_AccountPoolFieldOverride"("request_id", "occurred_at");
