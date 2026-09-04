"""验证 Manager 公开 API contract 与领域模型保持稳定的 JSON 边界。"""

from datetime import datetime, timezone
from typing import Final
from uuid import uuid4

from account_pool.contracts import AuthorizationView, EnvironmentView, GatewayEnvironment
from account_pool.domain import (
    AuthorizationFlow,
    ChannelKind,
    CleanupProgress,
    EnvironmentRecord,
    EnvironmentStatus,
    Provider,
    ProxyMode,
    QuotaSnapshot,
    SupplierKind,
    to_view,
)


def test_environment_view_contract_excludes_internal_authorization_fields() -> None:
    now: Final = datetime.now(timezone.utc)
    view: Final = EnvironmentView(
        id=uuid4(),
        version=1,
        name="test",
        provider=Provider.OPENAI,
        status=EnvironmentStatus.READY,
        configuration_pending=False,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=1,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=("gpt-5",),
        enabled_models=("gpt-5",),
        quota=QuotaSnapshot(),
        cooldown_until=None,
        last_error=None,
        created_at=now,
        updated_at=now,
        cleanup_progress=CleanupProgress(),
    )

    payload: Final = view.model_dump(mode="json")

    assert "oauth_state" not in payload
    assert "oauth_state_signature" not in payload
    assert "api_key" not in payload
    assert payload["status"] == "ready"


def test_environment_record_restores_legacy_payload_with_channel_and_supplier_defaults() -> None:
    now: Final = datetime.now(timezone.utc)
    record: Final = EnvironmentRecord(
        id=uuid4(),
        name="test",
        provider=Provider.OPENAI,
        status=EnvironmentStatus.READY,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=1,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=("gpt-5",),
        enabled_models=("gpt-5",),
        auth_file_name="codex.json",
        auth_index=None,
        quota=QuotaSnapshot(),
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    legacy_payload: Final = record.model_dump(mode="json", exclude={"channel", "supplier"})
    restored: Final = EnvironmentRecord.model_validate(legacy_payload)

    assert restored.channel is ChannelKind.CLIPROXYAPI
    assert restored.supplier is SupplierKind.OPENAI_CODEX


def test_to_view_includes_channel_and_supplier_without_internal_authorization_fields() -> None:
    now: Final = datetime.now(timezone.utc)
    record: Final = EnvironmentRecord(
        id=uuid4(),
        name="test",
        provider=Provider.OPENAI,
        status=EnvironmentStatus.READY,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=1,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=("gpt-5",),
        enabled_models=("gpt-5",),
        auth_file_name="codex.json",
        auth_index=None,
        quota=QuotaSnapshot(),
        cooldown_until=None,
        oauth_state="state",
        oauth_expires_at=now,
        oauth_state_signature="signature",
        oauth_provider_state="provider-state",
        oauth_authorization_url="https://example.com/oauth",
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    payload: Final = to_view(record).model_dump(mode="json")

    assert payload["channel"] == "cliproxyapi"
    assert payload["supplier"] == "openai_codex"
    for field in (
        "oauth_state",
        "oauth_state_signature",
        "oauth_provider_state",
        "oauth_authorization_url",
        "auth_file_name",
        "auth_index",
    ):
        assert field not in payload


def test_authorization_view_contract_has_only_public_authorization_instructions() -> None:
    now: Final = datetime.now(timezone.utc)
    authorization: Final = AuthorizationView(
        flow=AuthorizationFlow.DEVICE_CODE,
        authorization_url="https://example.com/oauth",
        ssh_command=None,
        user_code="ABCD-EFGH",
        expires_at=now,
    )

    assert authorization.model_dump(mode="json") == {
        "flow": "device_code",
        "authorization_url": "https://example.com/oauth",
        "ssh_command": None,
        "user_code": "ABCD-EFGH",
        "expires_at": now.isoformat().replace("+00:00", "Z"),
    }


def test_gateway_contract_keeps_internal_routing_credentials_explicit() -> None:
    gateway: Final = GatewayEnvironment(
        id=uuid4(),
        routable=True,
        concurrency_limit=2,
        enabled_models=("gpt-5",),
        api_base="http://cliproxy.example/v1",
        api_key="gateway-key",
    )

    assert gateway.api_key == "gateway-key"
    assert gateway.model_dump(mode="json")["enabled_models"] == ["gpt-5"]
