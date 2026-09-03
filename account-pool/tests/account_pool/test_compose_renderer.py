"""验证 Compose 渲染边界只生成配置，不执行运行时操作。"""

from pathlib import Path
from typing import Final
from uuid import uuid4

import yaml
from account_pool.compose_renderer import render_cli_proxy_config, render_compose
from account_pool.config import Settings
from account_pool.domain import EnvironmentRecord, EnvironmentStatus, Provider, ProxyMode, QuotaSnapshot, utc_now


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="postgresql://unused",
        data_root=tmp_path,
        manager_token="m" * 32,
        secret_seed="s" * 32,
        ssh_host="example.com",
        ssh_user="operator",
    )


def _record() -> EnvironmentRecord:
    now: Final = utc_now()
    return EnvironmentRecord(
        id=uuid4(),
        name="Test environment",
        provider=Provider.OPENAI,
        status=EnvironmentStatus.PROVISIONING,
        enabled=True,
        manual_cooldown=False,
        concurrency_limit=2,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=(),
        enabled_models=(),
        auth_file_name=None,
        auth_index=None,
        quota=QuotaSnapshot(),
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


def test_renderer_keeps_compose_identity_based_on_environment_uuid(tmp_path: Path) -> None:
    settings: Final = _settings(tmp_path)
    record: Final = _record()
    renamed: Final = record.model_copy(update={"name": "Renamed"})

    rendered: Final = yaml.safe_load(render_compose(record, settings))
    rerendered: Final = yaml.safe_load(render_compose(renamed, settings))

    assert rendered == rerendered
    assert "ports" not in rendered["services"]["cli-proxy-api"]
    assert rendered["name"] == f"account-pool-{record.id.hex}"
    assert rendered["volumes"] == {"cliproxy-data": {"name": f"account-pool-{record.id.hex}-data"}}
    assert rendered["services"]["cli-proxy-api"]["volumes"] == ["cliproxy-data:/data:rw"]
    assert rendered["networks"]["environment"]["internal"] is False


def test_renderer_generates_private_management_and_gateway_configuration() -> None:
    rendered: Final = yaml.safe_load(render_cli_proxy_config("management", "gateway"))

    assert rendered["remote-management"]["secret-key"] == "management"
    assert rendered["api-keys"] == ["gateway"]
