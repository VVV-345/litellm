"""验证渠道三类服务角色在期望状态中保持独立且可安全更新。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from account_pool.catalog.models import AdministrativeState, BindingOwnership, ChannelRecord
from account_pool.models import ChannelPriority, QuotaConfig
from account_pool.sync.contracts import ChannelBindingMutation, ChannelMutation
from account_pool.sync.desired_state import build_desired_state

_NOW: Final = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_OPERATION_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_BINDINGS: Final = (
    ChannelBindingMutation(
        public_model="public-model",
        provider_model="provider-model",
        ownership=BindingOwnership.POOL_MANAGED,
    ),
)


def _existing_channel() -> ChannelRecord:
    return ChannelRecord(
        channel_id=_CHANNEL_ID,
        legacy_account_id="legacy-channel",
        account_order=0,
        display_name="Existing",
        provider="openai",
        model_discovery_provider_id="existing-discovery",
        parser_provider_id="existing-parser",
        group=None,
        base_url_display="https://provider.example/v1",
        administrative_state=AdministrativeState.ENABLED,
        max_concurrency=1,
        priority=ChannelPriority.MEDIUM,
        weight=1,
        quotas=QuotaConfig(),
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_forwarding_discovery_and_parser_roles_are_persisted_independently() -> None:
    request: Final = ChannelMutation(
        display_name="Independent roles",
        provider="anthropic",
        model_discovery_provider_id="openai",
        parser_provider_id="generic",
        base_url_display="https://provider.example/v1",
        bindings=_BINDINGS,
    )

    desired: Final = build_desired_state(
        request,
        _CHANNEL_ID,
        0,
        _NOW,
        None,
        (),
        _OPERATION_ID,
    )

    assert (
        desired.provider,
        desired.model_discovery_provider_id,
        desired.parser_provider_id,
    ) == ("anthropic", "openai", "generic")


def test_optional_roles_preserve_when_omitted_and_clear_when_explicitly_null() -> None:
    existing: Final = _existing_channel()
    preserved_request: Final = ChannelMutation(
        display_name="Updated",
        provider="azure",
        base_url_display="https://provider.example/v1",
        bindings=_BINDINGS,
    )
    cleared_request: Final = ChannelMutation(
        display_name="Updated",
        provider="azure",
        model_discovery_provider_id=None,
        parser_provider_id=None,
        base_url_display="https://provider.example/v1",
        bindings=_BINDINGS,
    )

    preserved: Final = build_desired_state(
        preserved_request,
        _CHANNEL_ID,
        existing.account_order,
        _NOW,
        existing,
        (),
        _OPERATION_ID,
    )
    cleared: Final = build_desired_state(
        cleared_request,
        _CHANNEL_ID,
        existing.account_order,
        _NOW,
        existing,
        (),
        _OPERATION_ID,
    )

    assert (preserved.model_discovery_provider_id, preserved.parser_provider_id) == (
        "existing-discovery",
        "existing-parser",
    )
    assert (cleared.model_discovery_provider_id, cleared.parser_provider_id) == (None, None)
