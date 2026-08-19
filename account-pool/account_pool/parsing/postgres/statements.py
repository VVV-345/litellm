"""集中保存解析运行 PostgreSQL 仓储使用的静态参数化 SQL。"""

from typing import Final

SELECT_RUN: Final = """
SELECT parser_run_id, channel_id, parser_id, parser_version, parsed_at, status,
       content_hash, discovered_models, capabilities, unresolved_fields, evidence,
       warnings, issues, has_metered, export_status, export_attempt_count,
       export_last_attempt_at, exported_at, export_failure_code, export_failure_retryable
FROM "LiteLLM_AccountPoolParserRun"
WHERE parser_run_id = %s
"""
SELECT_SUBSCRIPTION: Final = """
SELECT subscription_snapshot_id, parser_run_id, plan_id, plan_name, status,
       starts_at, expires_at, models, balance, currency, channel_concurrency,
       model_concurrency
FROM "LiteLLM_AccountPoolSubscriptionSnapshot"
WHERE parser_run_id = %s
"""
SELECT_LIMITS: Final = """
SELECT quota_limit_id, parser_run_id, limit_order, scope, subject_id, kind,
       window_type, duration_seconds, limit_value, used_value, remaining_value,
       reset_at, source, observed_at
FROM "LiteLLM_AccountPoolQuotaLimit"
WHERE parser_run_id = %s
ORDER BY limit_order
"""
SELECT_GROUPS: Final = """
SELECT metered_group_row_id, parser_run_id, group_order, group_id, group_name, concurrency
FROM "LiteLLM_AccountPoolMeteredGroup"
WHERE parser_run_id = %s
ORDER BY group_order
"""
SELECT_PRICES: Final = """
SELECT price.metered_price_id, price.metered_group_row_id, price.price_order,
       price.provider_model_id, price.litellm_model_name, price.public_model_name,
       price.currency, price.unit, price.input_price, price.output_price,
       price.cache_read_price, price.cache_write_price, price.group_multiplier,
       price.price_calculation, price.conversion_note, price.effective_input_price,
       price.effective_output_price, price.effective_cache_read_price,
       price.effective_cache_write_price, price.normalized_input_price,
       price.normalized_output_price, price.normalized_cache_read_price,
       price.normalized_cache_write_price, price.has_normalized_prices,
       price.concurrency
FROM "LiteLLM_AccountPoolMeteredPrice" AS price
JOIN "LiteLLM_AccountPoolMeteredGroup" AS group_row
  ON group_row.metered_group_row_id = price.metered_group_row_id
WHERE group_row.parser_run_id = %s
ORDER BY group_row.group_order, price.price_order
"""
SELECT_ROUTES: Final = """
SELECT billing_route_row_id, parser_run_id, route_order, route_id,
       deployment_binding_id, mode, provider_group_id, request_parameter_ref
FROM "LiteLLM_AccountPoolBillingRoute"
WHERE parser_run_id = %s
ORDER BY route_order
"""
SELECT_EXPORTABLE: Final = """
SELECT parser_run_id
FROM "LiteLLM_AccountPoolParserRun"
WHERE export_status IN ('pending', 'retryable_failure')
ORDER BY parsed_at, parser_run_id
LIMIT %s
"""
INSERT_RUN: Final = """
INSERT INTO "LiteLLM_AccountPoolParserRun" (
    parser_run_id, channel_id, parser_id, parser_version, parsed_at, status,
    content_hash, discovered_models, capabilities, unresolved_fields, evidence,
    warnings, issues, has_metered, updated_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
    %s::jsonb, %s::jsonb, %s::jsonb, %s, CURRENT_TIMESTAMP
)
"""
INSERT_SUBSCRIPTION: Final = """
INSERT INTO "LiteLLM_AccountPoolSubscriptionSnapshot" (
    subscription_snapshot_id, parser_run_id, plan_id, plan_name, status,
    starts_at, expires_at, models, balance, currency, channel_concurrency,
    model_concurrency
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb)
"""
INSERT_LIMIT: Final = """
INSERT INTO "LiteLLM_AccountPoolQuotaLimit" (
    quota_limit_id, parser_run_id, limit_order, scope, subject_id, kind,
    window_type, duration_seconds, limit_value, used_value, remaining_value,
    reset_at, source, observed_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
INSERT_GROUP: Final = """
INSERT INTO "LiteLLM_AccountPoolMeteredGroup" (
    metered_group_row_id, parser_run_id, group_order, group_id, group_name, concurrency
) VALUES (%s, %s, %s, %s, %s, %s)
"""
INSERT_PRICE: Final = """
INSERT INTO "LiteLLM_AccountPoolMeteredPrice" (
    metered_price_id, metered_group_row_id, price_order, provider_model_id,
    litellm_model_name, public_model_name, currency, unit, input_price,
    output_price, cache_read_price, cache_write_price, group_multiplier,
    price_calculation, conversion_note, effective_input_price,
    effective_output_price, effective_cache_read_price, effective_cache_write_price,
    normalized_input_price, normalized_output_price, normalized_cache_read_price,
    normalized_cache_write_price, has_normalized_prices, concurrency
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
"""
INSERT_ROUTE: Final = """
INSERT INTO "LiteLLM_AccountPoolBillingRoute" (
    billing_route_row_id, parser_run_id, route_order, route_id,
    deployment_binding_id, mode, provider_group_id, request_parameter_ref
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""
UPDATE_EXPORT: Final = """
UPDATE "LiteLLM_AccountPoolParserRun"
SET export_status = %s,
    export_attempt_count = %s,
    export_last_attempt_at = %s,
    exported_at = %s,
    export_failure_code = %s,
    export_failure_retryable = %s,
    updated_at = CURRENT_TIMESTAMP
WHERE parser_run_id = %s
"""
