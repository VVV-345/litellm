-- 本迁移把历史渠道优先级归一为最高、高、中、低四档，并限制后续写入值。

UPDATE "LiteLLM_AccountPoolChannel"
SET "priority" = CASE
    WHEN "priority" >= 400 THEN 400
    WHEN "priority" >= 300 THEN 300
    WHEN "priority" >= 200 THEN 200
    ELSE 100
END;

UPDATE "LiteLLM_AccountPoolSyncOperation"
SET "desired_payload" = jsonb_set(
    "desired_payload",
    '{priority}',
    to_jsonb(
        CASE
            WHEN "desired_payload"->>'priority' ~ '^-?[0-9]+([.][0-9]+)?$'
                AND ("desired_payload"->>'priority')::NUMERIC >= 400 THEN 400
            WHEN "desired_payload"->>'priority' ~ '^-?[0-9]+([.][0-9]+)?$'
                AND ("desired_payload"->>'priority')::NUMERIC >= 300 THEN 300
            WHEN "desired_payload"->>'priority' ~ '^-?[0-9]+([.][0-9]+)?$'
                AND ("desired_payload"->>'priority')::NUMERIC >= 200 THEN 200
            ELSE 100
        END
    )
)
WHERE "desired_payload" ? 'priority';

ALTER TABLE "LiteLLM_AccountPoolChannel"
ADD CONSTRAINT "LiteLLM_AccountPoolChannel_priority_check"
CHECK ("priority" IN (100, 200, 300, 400));
