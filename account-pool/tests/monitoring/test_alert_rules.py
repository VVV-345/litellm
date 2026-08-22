"""验证 Prometheus 告警规则覆盖服务和 Worker 的关键故障。"""

from pathlib import Path
from typing import Final, cast

import yaml
from pydantic import TypeAdapter

_RULES_PATH: Final = Path(__file__).resolve().parents[2] / "deploy" / "prometheus" / "account-pool-alerts.yml"
_RULE_DOCUMENT: Final = TypeAdapter(dict[str, object])


def test_alert_rules_cover_service_and_worker_failures() -> None:
    document: Final = _RULE_DOCUMENT.validate_python(yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8")))
    groups: Final = TypeAdapter(list[dict[str, object]]).validate_python(document["groups"])
    rules: Final = tuple(
        rule
        for group in groups
        for rule in TypeAdapter(list[dict[str, object]]).validate_python(group["rules"])
    )
    names: Final = frozenset(cast(str, rule["alert"]) for rule in rules)
    expressions: Final = "\n".join(cast(str, rule["expr"]) for rule in rules)

    assert names == {
        "AccountPoolMetricsUnavailable",
        "AccountPoolWorkerUnavailable",
        "AccountPoolWorkerRepeatedFailures",
        "AccountPoolWorkerNeverSucceeded",
        "AccountPoolWorkerSuccessStale",
    }
    assert "account_pool_worker_expected_interval_seconds" in expressions
    assert "account_pool_worker_last_success_timestamp_seconds" in expressions
    assert "api_key" not in _RULES_PATH.read_text(encoding="utf-8").casefold()
