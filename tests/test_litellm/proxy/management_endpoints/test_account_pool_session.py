"""验证客户端会话标识的协议兼容、优先级和无效输入处理。"""

from uuid import uuid4

from starlette.datastructures import Headers

from litellm.proxy.management_endpoints.account_pool_session import session_identifier


def test_session_identity_accepts_native_protocols_without_using_per_request_ids():
    session = str(uuid4())
    assert session_identifier(Headers(), {"metadata": {"user_id": '{"session_id":"' + session + '"}'}}) == session
    assert session_identifier(Headers(), {"metadata": {"user_id": "user_account_session_" + session}}) == session
    assert session_identifier(Headers(), {"prompt_cache_key": "codex-session"}) == "codex-session"
    assert session_identifier(Headers({"X-Session-Id": "header"}), {"prompt_cache_key": "body"}) == "header"
    assert session_identifier(Headers({"X-Client-Request-Id": "unique"}), {}) is None
    assert session_identifier(Headers(), {"metadata": {"user_id": "{invalid"}}) is None
    assert session_identifier(Headers(), {"prompt_cache_key": "bad\nvalue"}) is None
    assert session_identifier(Headers(), {"prompt_cache_key": "x" * 513}) is None
