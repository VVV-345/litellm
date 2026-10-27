"""验证移植的错误分类不混淆额度、瞬时限流和请求拒绝。"""

import pytest

from litellm.proxy.management_endpoints.account_pool_failover import (
    capacity_request_scoped_retry_after,
    classify_failure,
    is_capacity_shed,
)


@pytest.mark.parametrize(
    "status,body,headers,kind,cooldown",
    [
        (429, {"error": {"code": "rate_limit_exceeded"}}, {}, "rate_limit", 0),
        (429, {"error": {"type": "usage_limit_reached"}}, {}, "quota_exhausted", 60),
        (
            429,
            {"error": {}},
            {"x-codex-primary-used-percent": "100", "x-codex-primary-reset-after-seconds": "123"},
            "quota_exhausted",
            123,
        ),
        (429, {"error": {}}, {"x-codex-secondary-used-percent": "99"}, "rate_limit", 0),
        (429, {"error": {"resets_in_seconds": 321}}, {}, "quota_exhausted", 321),
        (503, {"error": {"code": "server_is_overloaded"}}, {}, "upstream_unavailable", 0),
        (503, {"error": {"code": "context_length_exceeded"}}, {}, "request_rejected", 0),
        (503, {"error": {"code": "cyber_policy"}}, {}, "request_rejected", 0),
        (400, {"error": {"code": "model_not_found"}}, {}, "model_unavailable", 0),
        (400, {"error": {"code": "model_not_found", "type": "invalid_request_error"}}, {}, "model_unavailable", 0),
        (503, {"error": {"code": "server_error"}, "request": {"code": "cyber_policy"}}, {}, "upstream_unavailable", 1),
    ],
)
def test_error_envelope_controls_retry_classification(status, body, headers, kind, cooldown):
    failure = classify_failure(status, body, headers)
    assert failure.reason == kind
    assert failure.cooldown_seconds == cooldown
    assert failure.retryable is (kind != "request_rejected")


def test_capacity_shed_is_same_account_upstream_without_cooldown():
    overloaded = classify_failure(503, {"error": {"code": "server_is_overloaded"}}, {})
    ordinary = classify_failure(503, {"error": {"code": "server_error"}}, {})
    limited = classify_failure(429, {"error": {"code": "rate_limit_exceeded"}}, {})
    assert is_capacity_shed(overloaded)
    assert not is_capacity_shed(ordinary)
    assert not is_capacity_shed(limited)


@pytest.mark.parametrize(
    "headers,body,expected",
    [
        ({"Retry-After": "6"}, None, 6),
        ({"Retry-After-Ms": "6000"}, None, 6),
        ({"Retry-After": "6.2"}, None, 7),
        ({"Retry-After": "60"}, None, 8),
        ({"Retry-After": "0", "Retry-After-Ms": "1500"}, None, 2),
        ({}, {"error": {"headers": {"retry-after": "7"}}}, 7),
        ({}, {"response": {"error": {"headers": {"retry-after-ms": "7000"}}}}, 7),
        ({"Retry-After": "6"}, {"response": {"error": {"headers": {"retry-after-ms": "7000"}}}}, 6),
        ({}, None, 0),
        ({"Retry-After": "0"}, {"error": {"headers": {"retry-after": "0"}}}, 0),
    ],
)
def test_capacity_retry_after_uses_first_positive_value_and_caps_at_eight_seconds(headers, body, expected):
    assert capacity_request_scoped_retry_after(headers, body) == expected
