"""验证静态渠道和供应商注册表只公开受支持的不可变定义。"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Final, get_type_hints

import pytest

from account_pool.channels.base import ChannelDefinition, SupplierResolver
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.registry import ChannelRegistry, UnsupportedChannelError
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.domain import AuthorizationFlow, ChannelKind, SupplierKind
from account_pool.quota import QuotaObservation


@pytest.mark.parametrize(
    (
        "kind",
        "authorization_flow",
        "authorization_path",
        "callback_provider_key",
        "auth_file_provider_key",
        "excluded_models_key",
        "callback_port",
        "callback_path",
    ),
    (
        (
            SupplierKind.OPENAI_CODEX,
            AuthorizationFlow.BROWSER_OAUTH,
            "/v0/management/codex-auth-url",
            "codex",
            "codex",
            "codex",
            1455,
            "/auth/callback",
        ),
        (
            SupplierKind.ANTHROPIC_CLAUDE,
            AuthorizationFlow.BROWSER_OAUTH,
            "/v0/management/anthropic-auth-url",
            "anthropic",
            "claude",
            "claude",
            54545,
            "/callback",
        ),
        (
            SupplierKind.GOOGLE_ANTIGRAVITY,
            AuthorizationFlow.BROWSER_OAUTH,
            "/v0/management/antigravity-auth-url",
            "antigravity",
            "antigravity",
            "antigravity",
            51121,
            "/oauth-callback",
        ),
        (
            SupplierKind.KIMI,
            AuthorizationFlow.DEVICE_CODE,
            "/v0/management/kimi-auth-url",
            "kimi",
            "kimi",
            "kimi",
            None,
            None,
        ),
        (
            SupplierKind.XAI,
            AuthorizationFlow.DEVICE_CODE,
            "/v0/management/xai-auth-url",
            "xai",
            "xai",
            "xai",
            None,
            None,
        ),
    ),
)
def test_cliproxyapi_supplier_definitions_match_verified_management_contract(
    kind: SupplierKind,
    authorization_flow: AuthorizationFlow,
    authorization_path: str,
    callback_provider_key: str,
    auth_file_provider_key: str,
    excluded_models_key: str,
    callback_port: int | None,
    callback_path: str | None,
) -> None:
    registry: Final = SupplierRegistry.default()

    definition: Final = registry.get(kind)

    assert (
        definition.kind,
        definition.authorization_flow,
        definition.authorization_path,
        definition.callback_provider_key,
        definition.auth_file_provider_key,
        definition.excluded_models_key,
        definition.callback_port,
        definition.callback_path,
    ) == (
        kind,
        authorization_flow,
        authorization_path,
        callback_provider_key,
        auth_file_provider_key,
        excluded_models_key,
        callback_port,
        callback_path,
    )
    with pytest.raises(FrozenInstanceError):
        setattr(definition, "authorization_path", "/invalid")


def test_cliproxyapi_channel_supports_every_registered_supplier() -> None:
    registry: Final = ChannelRegistry.default()
    channel: Final = registry.get(ChannelKind.CLIPROXYAPI)
    supplier_registry: Final = SupplierRegistry.default()

    assert channel.suppliers == tuple(supplier_registry.definitions)


def test_cliproxyapi_channel_rejects_a_supplier_missing_from_its_registry() -> None:
    suppliers: Final = SupplierRegistry.default()
    channel: Final = ChannelDefinition(
        kind=ChannelKind.CLIPROXYAPI,
        suppliers=(SupplierKind.XAI,),
        supplier_registry=SupplierRegistry(definitions=MappingProxyType({})),
    )

    with pytest.raises(UnsupportedChannelError, match="^cliproxyapi does not support xai$"):
        channel.supplier(SupplierKind.XAI)

    assert SupplierKind.XAI in suppliers.definitions


@pytest.mark.parametrize("kind", tuple(SupplierKind))
def test_supplier_quota_parser_preserves_only_unstructured_observation_metadata(kind: SupplierKind) -> None:
    observation_time: Final = datetime(2026, 9, 4, tzinfo=timezone.utc)
    observation: Final = QuotaObservation(
        observed_at=observation_time,
        signals={
            "x-codex-plan-type": "pro",
            "x-codex-five-hour-used-percent": "25",
            "x-codex-five-hour-window-minutes": "300",
        },
    )
    definition: Final = (
        ChannelRegistry.default().get(ChannelKind.FREEBUFF2API).supplier(kind)
        if kind is SupplierKind.FREEBUFF
        else SupplierRegistry.default().get(kind)
    )

    snapshot: Final = definition.quota_parser(observation)

    if kind is SupplierKind.OPENAI_CODEX:
        assert snapshot.plan_type == "pro"
        assert len(snapshot.windows) == 1
        assert snapshot.windows[0].remaining_percent == 75
    else:
        assert snapshot.observed_at == observation_time
        assert snapshot.plan_type is None
        assert snapshot.windows == ()


def test_channel_supplier_annotations_resolve_to_supplier_definition() -> None:
    channel_hints: Final = get_type_hints(ChannelDefinition.supplier)
    resolver_hints: Final = get_type_hints(SupplierResolver.get)

    assert channel_hints["return"] is SupplierDefinition
    assert resolver_hints["return"] is SupplierDefinition
    registry: Final = ChannelRegistry.default()
    cliproxy: Final = registry.get(ChannelKind.CLIPROXYAPI)
    freebuff: Final = registry.get(ChannelKind.FREEBUFF2API)

    assert freebuff.suppliers == (SupplierKind.FREEBUFF,)
    with pytest.raises(UnsupportedChannelError, match="^freebuff2api does not support kimi$"):
        freebuff.supplier(SupplierKind.KIMI)
    assert cliproxy.supplier(SupplierKind.OPENAI_CODEX).kind is SupplierKind.OPENAI_CODEX


def test_freebuff2api_channel_supports_only_freebuff_supplier() -> None:
    registry: Final = ChannelRegistry.default()

    freebuff: Final = registry.get(ChannelKind.FREEBUFF2API)
    definition: Final = freebuff.supplier(SupplierKind.FREEBUFF)

    assert definition.kind is SupplierKind.FREEBUFF
    assert definition.authorization_flow is AuthorizationFlow.DEVICE_CODE
    assert definition.authorization_path == "/api/auth/cli/code"
    assert definition.callback_port is None
    assert definition.callback_path is None
    for kind in tuple(SupplierKind):
        if kind is not SupplierKind.FREEBUFF:
            with pytest.raises(UnsupportedChannelError):
                freebuff.supplier(kind)
