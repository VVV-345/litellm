"""验证旧版渠道和 Deployment 的确定性 UUID 生成规则。"""

from datetime import datetime
from typing import Final
from uuid import UUID

import pytest
from account_pool.catalog.identity import legacy_binding_id, legacy_channel_id
from account_pool.catalog.models import AdministrativeState, ChannelRecord
from account_pool.models import QuotaConfig
from pydantic import ValidationError


def test_legacy_ids_are_stable_and_namespaced() -> None:
    channel: Final = legacy_channel_id("primary-east")

    assert isinstance(channel, UUID)
    assert channel == legacy_channel_id("primary-east")
    assert channel != legacy_channel_id("backup-west")
    assert channel.version == 5
    assert legacy_binding_id(channel, "deployment-a") == legacy_binding_id(channel, "deployment-a")
    assert legacy_binding_id(channel, "deployment-a") != legacy_binding_id(channel, "deployment-b")


def test_channel_requires_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        ChannelRecord(
            channel_id=legacy_channel_id("primary-east"),
            legacy_account_id="primary-east",
            account_order=0,
            display_name="Primary",
            provider="openai",
            base_url_display="https://api.openai.com",
            administrative_state=AdministrativeState.ENABLED,
            max_concurrency=1,
            priority=0,
            weight=1,
            quotas=QuotaConfig(),
            created_at=datetime(2026, 8, 19),
            updated_at=datetime(2026, 8, 19),
        )
