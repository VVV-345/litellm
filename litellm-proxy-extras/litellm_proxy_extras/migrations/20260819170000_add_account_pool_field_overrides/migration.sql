-- 本迁移创建不可变人工覆盖事件链，保存逐字段设置、修改和撤销审计。

CREATE TABLE "LiteLLM_AccountPoolFieldOverride" (
    "override_id" TEXT NOT NULL,
    "channel_id" TEXT NOT NULL,
    "source_parser_run_id" TEXT NOT NULL,
    "field_path" TEXT NOT NULL,
    "target_kind" TEXT NOT NULL,
    "target" JSONB NOT NULL,
    "action" TEXT NOT NULL,
    "value" JSONB NOT NULL,
    "had_previous_override" BOOLEAN NOT NULL DEFAULT FALSE,
    "previous_value" JSONB NOT NULL,
    "supersedes_override_id" TEXT,
    "actor_id" TEXT NOT NULL,
    "reason" TEXT NOT NULL,
    "occurred_at" TIMESTAMPTZ(6) NOT NULL,
    "content_hash" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "LiteLLM_AccountPoolFieldOverride_pkey" PRIMARY KEY ("override_id")
);

CREATE UNIQUE INDEX "LiteLLM_AccountPoolFieldOverride_supersedes_override_id_key"
ON "LiteLLM_AccountPoolFieldOverride"("supersedes_override_id");
CREATE INDEX "LiteLLM_AccountPoolFieldOverride_channel_id_field_path_occurred_at_idx"
ON "LiteLLM_AccountPoolFieldOverride"("channel_id", "field_path", "occurred_at");
CREATE INDEX "LiteLLM_AccountPoolFieldOverride_source_parser_run_id_idx"
ON "LiteLLM_AccountPoolFieldOverride"("source_parser_run_id");

ALTER TABLE "LiteLLM_AccountPoolFieldOverride"
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_channel_id_fkey"
FOREIGN KEY ("channel_id") REFERENCES "LiteLLM_AccountPoolChannel"("channel_id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_AccountPoolFieldOverride"
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_source_parser_run_id_fkey"
FOREIGN KEY ("source_parser_run_id") REFERENCES "LiteLLM_AccountPoolParserRun"("parser_run_id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_AccountPoolFieldOverride"
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_supersedes_override_id_fkey"
FOREIGN KEY ("supersedes_override_id") REFERENCES "LiteLLM_AccountPoolFieldOverride"("override_id") ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE "LiteLLM_AccountPoolFieldOverride"
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_action_check"
CHECK ("action" IN ('set', 'revoke')),
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_target_kind_check"
CHECK ("target_kind" IN ('root_field', 'subscription_field', 'subscription_model', 'metered_group', 'metered_price', 'billing_route')),
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_field_path_check"
CHECK (length("field_path") > 1 AND left("field_path", 1) = '/'),
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_actor_reason_check"
CHECK (length("actor_id") > 0 AND length("reason") > 0),
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_predecessor_check"
CHECK ((NOT "had_previous_override" OR "supersedes_override_id" IS NOT NULL)
   AND ("action" <> 'revoke' OR "had_previous_override")),
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_revoke_value_check"
CHECK ("action" <> 'revoke' OR "value" = 'null'::JSONB),
ADD CONSTRAINT "LiteLLM_AccountPoolFieldOverride_content_hash_check"
CHECK ("content_hash" ~ '^[0-9a-f]{64}$');
