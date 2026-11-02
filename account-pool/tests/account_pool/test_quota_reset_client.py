"""验证重置传输只使用选定凭据及固定端点，401 不触发令牌替换或刷新。"""

import json
from uuid import uuid4

import httpx
import pytest
from account_pool.channels.cliproxyapi.quota_reset import CodexResetClient
from account_pool.credential_ownership import credential_identity
from account_pool.quota_reset_models import ResetObservation, ResetOperation
from account_pool.secrets import EnvironmentSecretDeriver
from test_cliproxy_supplier_client import _record


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 401])
async def test_consume_uses_literal_token_without_refresh_or_cross_card_calls(status):
    secrets = EnvironmentSecretDeriver("fixture-secret-not-real")
    credential = json.dumps({"access_token": "fixture-access", "refresh_token": "fixture-refresh"}).encode()
    record = _record().model_copy(
        update={
            "auth_file_name": "fixture.json",
            "auth_index": "only-auth",
            "credential_fingerprints": credential_identity(credential, "openai_codex", secrets).fingerprints,
        }
    )
    seen = []

    def handle(request):
        seen.append(request)
        assert request.url.host == f"cliproxy-{record.id.hex}"
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=credential)
        if request.url.path.endswith("/auth-files"):
            return httpx.Response(200, json={"files": [{"name": "fixture.json", "auth_index": "only-auth"}]})
        payload = json.loads(request.content)
        assert payload["auth_index"] == "only-auth"
        assert payload["header"]["Authorization"] == "Bearer fixture-access"
        assert "$TOKEN$" not in request.content.decode()
        assert "proxy_url" not in payload
        assert payload["url"] == "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume"
        assert json.loads(payload["data"]) == {"redeem_request_id": str(operation.request_id), "credit_id": "credit-1"}
        return httpx.Response(
            200, json={"status_code": status, "body": json.dumps({"code": "reset", "windows_reset": 1})}
        )

    operation = ResetOperation(
        request_id=uuid4(),
        environment_id=record.id,
        credit_id="credit-1",
        credential_fingerprints=record.credential_fingerprints,
        before=ResetObservation(available=2, credit_id="credit-1", eligible=True),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = CodexResetClient(http, secrets)
        if status == 401:
            with pytest.raises(ValueError, match="认证失败"):
                await client.consume(record, operation)
        else:
            assert (await client.consume(record, operation)).windows_reset == 1
    assert sum(request.url.path.endswith("/api-call") for request in seen) == 1
    assert all(request.method == "GET" or request.url.path.endswith("/api-call") for request in seen)
