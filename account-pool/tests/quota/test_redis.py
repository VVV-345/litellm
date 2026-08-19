"""验证 Redis 额度编码在高精度和异常输入下不会静默截断。"""

from decimal import Decimal
from typing import Final

import account_pool.store as store_module
from account_pool.models import (
    AccountConfig,
    DeploymentConfig,
    QuotaWindowConfig,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
    SettleRequest,
)
from account_pool.quota.redis import (
    MAXIMUM_DECIMAL_SCALE,
    EncodedQuotaAmount,
    RedisQuotaCodecFailure,
    RedisQuotaConfiguration,
    RedisQuotaReservationPlan,
    RedisQuotaSettlementAmounts,
    configure_quota_script_args,
    decode_quota_amount,
    decode_quota_window,
    encode_account_quota_windows,
    encode_quota_amount,
    encode_quota_window,
    prepare_quota_reservation_plan,
    prepare_quota_settlement_amounts,
    quota_manifest_key,
    quota_usage_key,
    quota_window_hash_fields,
    quota_window_key,
)
from account_pool.quota.redis_scripts import (
    REDIS_CONFIGURE_QUOTA_WINDOW_SCRIPT,
    REDIS_QUOTA_HEARTBEAT_LUA,
    REDIS_QUOTA_RELEASE_LUA,
    REDIS_QUOTA_RESERVE_CHECK_LUA,
    REDIS_QUOTA_RESERVE_COMMIT_LUA,
    REDIS_QUOTA_RUNTIME_LUA,
    REDIS_QUOTA_SETTLE_LUA,
    REDIS_UNSIGNED_DECIMAL_LUA,
)
from account_pool.quota.runtime import RuntimeQuotaWindow


def _window(
    limit: Decimal | None = Decimal("1000.123456789123456789"),
    remaining: Decimal | None = Decimal("250.987654321987654321"),
) -> RuntimeQuotaWindow:
    return RuntimeQuotaWindow(
        config=QuotaWindowConfig(
            window_id="window:provider/model",
            scope=RuntimeQuotaScope.CHANNEL,
            kind=RuntimeQuotaKind.CURRENCY,
            window_type=RuntimeQuotaWindowType.ROLLING,
            duration_seconds=18_000,
            limit=limit,
            remaining=remaining,
            observed_at=1_000,
            source="provider-api",
            reason_code="five_hour_exhausted",
        ),
        remaining=remaining,
        retry_at=19_000,
    )


def test_amount_codec_round_trips_eighteen_decimal_places_without_int64_limit() -> None:
    value: Final = Decimal("1000.123456789123456789")

    encoded: Final = encode_quota_amount(value, scale=18)

    assert isinstance(encoded, EncodedQuotaAmount)
    assert encoded.units == "1000123456789123456789"
    assert decode_quota_amount(encoded.units, encoded.scale) == value


def test_window_codec_uses_one_exact_scale_for_all_amounts() -> None:
    encoded: Final = encode_quota_window("channel-a", _window())

    assert not isinstance(encoded, RedisQuotaCodecFailure)
    assert encoded.scale == MAXIMUM_DECIMAL_SCALE
    assert decode_quota_amount(encoded.limit_units or "", encoded.scale) == Decimal("1000.123456789123456789")
    assert decode_quota_amount(encoded.remaining_units or "", encoded.scale) == Decimal("250.987654321987654321")
    assert encoded.safety_reserve_units == "0"
    assert encoded.reserved_units == "0"


def test_stable_keys_hash_untrusted_window_identity() -> None:
    first: Final = quota_window_key("channel-a", "window:provider/model")
    repeated: Final = quota_window_key("channel-a", "window:provider/model")
    other: Final = quota_window_key("channel-a", "window:provider/other")

    assert first == repeated
    assert first != other
    assert "window:provider/model" not in first
    assert quota_usage_key("channel-a", "window:provider/model") == f"{first}:usage"
    assert quota_manifest_key("channel-a") == "pool:account:channel-a:quota:manifest"


def test_codec_rejects_precision_beyond_explicit_limit() -> None:
    value: Final = Decimal("0." + ("0" * MAXIMUM_DECIMAL_SCALE) + "1")

    encoded: Final = encode_quota_window("channel-a", _window(limit=value, remaining=value))

    assert isinstance(encoded, RedisQuotaCodecFailure)
    assert encoded.code == "scale_too_large"


def test_codec_rejects_non_finite_and_invalid_units() -> None:
    non_finite: Final = encode_quota_amount(Decimal("Infinity"), scale=18)
    invalid_units: Final = decode_quota_amount("1.5", scale=18)

    assert isinstance(non_finite, RedisQuotaCodecFailure)
    assert non_finite.code == "non_finite"
    assert isinstance(invalid_units, RedisQuotaCodecFailure)
    assert invalid_units.code == "invalid_units"


def test_codec_rejects_large_values_before_materializing_scaled_text() -> None:
    encoded: Final = encode_quota_amount(Decimal("1e1000000"), scale=18)

    assert isinstance(encoded, RedisQuotaCodecFailure)
    assert encoded.code == "value_too_large"


def test_lua_arithmetic_never_converts_complete_quota_values_to_numbers() -> None:
    assert "tonumber(left)" not in REDIS_UNSIGNED_DECIMAL_LUA
    assert "tonumber(right)" not in REDIS_UNSIGNED_DECIMAL_LUA
    assert "tonumber(value)" not in REDIS_UNSIGNED_DECIMAL_LUA
    assert "quota_compare_unsigned" in REDIS_UNSIGNED_DECIMAL_LUA
    assert "quota_add_unsigned" in REDIS_UNSIGNED_DECIMAL_LUA
    assert "quota_subtract_unsigned" in REDIS_UNSIGNED_DECIMAL_LUA


def test_configure_contract_preserves_amounts_as_strings() -> None:
    encoded: Final = encode_quota_window("channel-a", _window())
    assert not isinstance(encoded, RedisQuotaCodecFailure)

    fields: Final = quota_window_hash_fields(encoded)
    arguments: Final = configure_quota_script_args(encoded)

    assert len(arguments) == 18
    assert arguments[0] == fields["snapshot_fingerprint"]
    assert arguments[10] == fields["limit_units"]
    assert arguments[11] == fields["snapshot_remaining_units"]
    assert arguments[12] == fields["safety_reserve_units"]
    assert fields["remaining_units"] == fields["snapshot_remaining_units"]
    assert "tonumber(ARGV[11])" not in REDIS_CONFIGURE_QUOTA_WINDOW_SCRIPT
    assert "tonumber(ARGV[12])" not in REDIS_CONFIGURE_QUOTA_WINDOW_SCRIPT
    assert "quota_subtract_unsigned(calibrated_remaining, retained_units)" in REDIS_CONFIGURE_QUOTA_WINDOW_SCRIPT
    assert "reserved_units = redis.call('HGET'" in REDIS_CONFIGURE_QUOTA_WINDOW_SCRIPT


def test_account_configuration_compiles_all_windows_or_returns_one_failure() -> None:
    valid_window: Final = _window().config
    account: Final = AccountConfig(
        id="channel-a",
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=1,
        quota_windows=(valid_window,),
        deployments=(DeploymentConfig(public_model="model-a", litellm_model_id="deployment-a"),),
    )
    valid: Final = encode_account_quota_windows(account)
    invalid: Final = encode_account_quota_windows(
        account.model_copy(
            update={
                "quota_windows": (
                    valid_window.model_copy(update={"remaining": Decimal("0." + ("0" * MAXIMUM_DECIMAL_SCALE) + "1")}),
                )
            }
        )
    )

    assert isinstance(valid, RedisQuotaConfiguration)
    assert len(valid.records) == 1
    assert isinstance(invalid, RedisQuotaCodecFailure)
    assert invalid.code == "scale_too_large"


def test_hash_fields_decode_back_to_runtime_window() -> None:
    source: Final = _window()
    encoded: Final = encode_quota_window("channel-a", source)
    assert not isinstance(encoded, RedisQuotaCodecFailure)

    decoded: Final = decode_quota_window(quota_window_hash_fields(encoded))

    assert isinstance(decoded, RuntimeQuotaWindow)
    assert decoded.config == source.config
    assert decoded.remaining == source.remaining
    assert decoded.reserved == source.reserved
    assert decoded.retry_at == source.retry_at


def test_hash_decoder_rejects_missing_or_wrong_scale_state() -> None:
    missing: Final = decode_quota_window({})
    encoded: Final = encode_quota_window("channel-a", _window())
    assert not isinstance(encoded, RedisQuotaCodecFailure)
    wrong_scale_fields: Final = {**quota_window_hash_fields(encoded), "scale": "18"}
    wrong_scale: Final = decode_quota_window(wrong_scale_fields)

    assert isinstance(missing, RedisQuotaCodecFailure)
    assert missing.code == "invalid_state"
    assert isinstance(wrong_scale, RedisQuotaCodecFailure)
    assert wrong_scale.code == "invalid_state"


def test_reservation_plan_matches_scope_and_encodes_request_units() -> None:
    channel: Final = _window().config.model_copy(update={"kind": RuntimeQuotaKind.REQUESTS})
    model: Final = channel.model_copy(
        update={
            "window_id": "model-window",
            "scope": RuntimeQuotaScope.MODEL,
            "subject_id": "model-a",
            "kind": RuntimeQuotaKind.TOKENS,
        }
    )
    other_model: Final = model.model_copy(update={"window_id": "other-model-window", "subject_id": "model-b"})
    route: Final = model.model_copy(
        update={
            "window_id": "route-window",
            "scope": RuntimeQuotaScope.BILLING_ROUTE,
            "subject_id": "route-a",
        }
    )
    account: Final = AccountConfig(
        id="channel-a",
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=1,
        quota_windows=(channel, model, other_model, route),
        deployments=(
            DeploymentConfig(
                public_model="model-a",
                litellm_model_id="deployment-a",
                billing_route_id="route-a",
            ),
        ),
    )

    plan: Final = prepare_quota_reservation_plan(
        account=account,
        public_model="model-a",
        billing_route_id="route-a",
        estimated_tokens=125,
    )

    assert isinstance(plan, RedisQuotaReservationPlan)
    assert len(plan.reservations) == 3
    decoded_amounts: Final = tuple(
        decode_quota_amount(reservation.amount_units, MAXIMUM_DECIMAL_SCALE) for reservation in plan.reservations
    )
    assert decoded_amounts == (Decimal("1"), Decimal("125"), Decimal("125"))
    assert len(plan.keys) == 6
    assert len(plan.arguments) == 3


def test_settlement_amounts_encode_only_trusted_usage_dimensions() -> None:
    amounts: Final = prepare_quota_settlement_amounts(
        SettleRequest(
            lease_id="lease-a",
            success=True,
            input_tokens=25,
            output_tokens=5,
            cost_usd=1.25,
        )
    )

    assert isinstance(amounts, RedisQuotaSettlementAmounts)
    assert decode_quota_amount(amounts.request_units, MAXIMUM_DECIMAL_SCALE) == Decimal("1")
    assert decode_quota_amount(amounts.token_units, MAXIMUM_DECIMAL_SCALE) == Decimal("30")
    assert decode_quota_amount(amounts.currency_units, MAXIMUM_DECIMAL_SCALE) == Decimal("1.25")


def test_lifecycle_scripts_preserve_atomic_reservation_contract() -> None:
    assert REDIS_QUOTA_RESERVE_CHECK_LUA in store_module._RESERVE_SCRIPT
    assert REDIS_QUOTA_RESERVE_COMMIT_LUA in store_module._RESERVE_SCRIPT
    assert REDIS_QUOTA_RELEASE_LUA in store_module._RELEASE_SCRIPT
    assert REDIS_QUOTA_HEARTBEAT_LUA in store_module._HEARTBEAT_SCRIPT
    assert REDIS_QUOTA_SETTLE_LUA in store_module._SETTLE_SCRIPT
    assert REDIS_QUOTA_RUNTIME_LUA in store_module._RESERVE_SCRIPT
    assert "settled ~= '1'" in REDIS_QUOTA_RELEASE_LUA
    assert "quota_probe_key" in REDIS_QUOTA_HEARTBEAT_LUA
    assert "quota_next_fixed_retry" in REDIS_QUOTA_RUNTIME_LUA
    assert "tonumber(ARGV[10])" not in REDIS_QUOTA_SETTLE_LUA
    assert "tonumber(ARGV[11])" not in REDIS_QUOTA_SETTLE_LUA
    assert "tonumber(ARGV[12])" not in REDIS_QUOTA_SETTLE_LUA
