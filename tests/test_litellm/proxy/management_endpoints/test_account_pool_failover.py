"""验证移植的错误分类不混淆额度、瞬时限流和请求拒绝。"""

import pytest

from litellm.proxy.management_endpoints.account_pool_failover import classify_failure


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
