"""验证静态渠道和供应商注册表只公开受支持的不可变定义。"""

from dataclasses import FrozenInstanceError
from typing import Final

import pytest

from account_pool.channels.registry import ChannelRegistry, UnsupportedChannelError
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.domain import AuthorizationFlow, ChannelKind, SupplierKind


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

    assert definition == SupplierDefinition(
        kind=kind,
        authorization_flow=authorization_flow,
        authorization_path=authorization_path,
        callback_provider_key=callback_provider_key,
        auth_file_provider_key=auth_file_provider_key,
        excluded_models_key=excluded_models_key,
        callback_port=callback_port,
        callback_path=callback_path,
        quota_parser=definition.quota_parser,
    )
    with pytest.raises(FrozenInstanceError):
        setattr(definition, "authorization_path", "/invalid")


def test_cliproxyapi_channel_supports_every_defined_supplier() -> None:
    registry: Final = ChannelRegistry.default()
    channel: Final = registry.get(ChannelKind.CLIPROXYAPI)

    assert tuple(channel.suppliers) == tuple(SupplierKind)


def test_freebuff2api_rejects_every_supplier_before_runtime_resolution() -> None:
    registry: Final = ChannelRegistry.default()
    channel: Final = registry.get(ChannelKind.FREEBUFF2API)

    for supplier in SupplierKind:
        with pytest.raises(UnsupportedChannelError, match="^FreeBuff2API is not implemented$"):
            channel.supplier(supplier)
