"""验证 Manager 公开 API contract 与领域模型保持稳定的 JSON 边界。"""

from datetime import datetime, timezone
from uuid import uuid4

from account_pool.contracts import EnvironmentView, GatewayEnvironment
from account_pool.domain import CleanupProgress, EnvironmentStatus, Provider, ProxyMode, QuotaSnapshot


def test_environment_view_contract_excludes_internal_authorization_fields() -> None:
    now = datetime.now(timezone.utc)
    view = EnvironmentView(
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

    payload = view.model_dump(mode="json")

    assert "oauth_state" not in payload
    assert "oauth_state_signature" not in payload
    assert "api_key" not in payload
    assert payload["status"] == "ready"


def test_gateway_contract_keeps_internal_routing_credentials_explicit() -> None:
    gateway = GatewayEnvironment(
        id=uuid4(),
        routable=True,
        concurrency_limit=2,
        enabled_models=("gpt-5",),
        api_base="http://cliproxy.example/v1",
        api_key="gateway-key",
    )

    assert gateway.api_key == "gateway-key"
    assert gateway.model_dump(mode="json")["enabled_models"] == ["gpt-5"]
