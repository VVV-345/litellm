"""验证旧 YAML 配置兼容读取及渠道优先级归一规则。"""

from pathlib import Path
from typing import Final

import pytest
from account_pool.config import Settings, load_pool_config
from account_pool.models import ChannelPriority, normalize_channel_priority


def test_legacy_priority_values_are_normalized_when_loading_yaml(tmp_path: Path) -> None:
    config_path: Final = tmp_path / "accounts.yaml"
    config_path.write_text(
        """accounts:
  - id: legacy-low
    display_name: Legacy low
    provider: openai
    base_url_display: https://example.test/v1
    max_concurrency: 1
    priority: 60
    deployments:
      - public_model: model-a
        litellm_model_id: deployment-a
  - id: legacy-high
    display_name: Legacy high
    provider: openai
    base_url_display: https://example.test/v1
    max_concurrency: 1
    priority: 350
    deployments:
      - public_model: model-a
        litellm_model_id: deployment-b
policies: []
""",
        encoding="utf-8",
    )

    loaded: Final = load_pool_config(config_path)

    assert tuple(account.priority for account in loaded.accounts) == (
        ChannelPriority.LOW,
        ChannelPriority.HIGH,
    )


def test_priority_normalization_has_four_stable_bands() -> None:
    assert normalize_channel_priority(-10) == ChannelPriority.LOW
    assert normalize_channel_priority(199) == ChannelPriority.LOW
    assert normalize_channel_priority(200) == ChannelPriority.MEDIUM
    assert normalize_channel_priority(299) == ChannelPriority.MEDIUM
    assert normalize_channel_priority(300) == ChannelPriority.HIGH
    assert normalize_channel_priority(399) == ChannelPriority.HIGH
    assert normalize_channel_priority(400) == ChannelPriority.HIGHEST
    assert normalize_channel_priority(999) == ChannelPriority.HIGHEST


def test_public_metadata_worker_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_PUBLIC_METADATA_POLL_INTERVAL_SECONDS", "17")
    monkeypatch.setenv("ACCOUNT_POOL_PUBLIC_METADATA_REFRESH_INTERVAL_SECONDS", "1800")
    monkeypatch.setenv("ACCOUNT_POOL_PUBLIC_METADATA_RETRY_BASE_SECONDS", "7")
    monkeypatch.setenv("ACCOUNT_POOL_PUBLIC_METADATA_BATCH_SIZE", "4")
    monkeypatch.setenv("ACCOUNT_POOL_PUBLIC_METADATA_MAX_ATTEMPTS", "2")

    loaded: Final = Settings.from_env()

    assert loaded.public_metadata_poll_interval_seconds == 17
    assert loaded.public_metadata_refresh_interval_seconds == 1800
    assert loaded.public_metadata_retry_base_seconds == 7
    assert loaded.public_metadata_batch_size == 4
    assert loaded.public_metadata_max_attempts == 2
