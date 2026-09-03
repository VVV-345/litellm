"""验证号池错误信息脱敏规则，防止凭据进入用户可见错误。"""

from account_pool.error_safety import safe_error


def test_safe_error_redacts_url_credentials_and_oauth_parameters() -> None:
    error = RuntimeError(
        "request failed for https://user:password@example.com/oauth?code=oauth-code&state=oauth-state "
        "Bearer access-token"
    )

    result = safe_error(error)

    assert "password" not in result
    assert "oauth-code" not in result
    assert "oauth-state" not in result
    assert "access-token" not in result
    assert "example.com/oauth" in result
