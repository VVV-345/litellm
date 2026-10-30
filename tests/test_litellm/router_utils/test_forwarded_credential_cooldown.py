from typing import Final

import httpx
import pytest
import respx

import litellm
from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from litellm.router import Router
from litellm.types.router import AllowedFailsPolicy

_MODEL: Final = "anthropic/claude-sonnet-4-5"


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_litellm_state")
@pytest.mark.parametrize("surface", ["chat", "messages"])
@pytest.mark.parametrize("credential_kind", ["oauth", "api_key"])
async def test_anthropic_caller_rate_limit_does_not_block_other_credentials(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, surface: str, credential_kind: str
) -> None:
    monkeypatch.setattr(litellm, "disable_aiohttp_transport", True)
    monkeypatch.setattr("time.time", lambda: 1767225600.0)

    def upstream(request: httpx.Request) -> httpx.Response:
        credential: Final = request.headers.get("authorization", request.headers.get("x-api-key", ""))
        if credential.endswith("limited") and route.call_count < 2:
            return httpx.Response(
                429,
                headers={"retry-after": "168307"},
                json={"type": "error", "error": {"type": "rate_limit_error", "message": "usage limit"}},
            )
        return httpx.Response(
            200,
            json={
                "id": "msg-healthy",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": "healthy account"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 2},
            },
        )

    route: Final = respx_mock.post("https://api.anthropic.com/v1/messages").mock(side_effect=upstream)
    router: Final = Router(
        model_list=[
            {
                "model_name": "claude",
                "litellm_params": {"model": _MODEL, "api_key": "fake-gateway-key"},
                "model_info": {"id": "shared-anthropic"},
            }
        ],
        num_retries=0,
        allowed_fails_policy=AllowedFailsPolicy(RateLimitErrorAllowedFails=1),
    )
    client: Final = AsyncHTTPHandler()

    async def call(account: str | None) -> object:
        credentials: Final = (
            (
                {
                    "provider_specific_header": {
                        "custom_llm_provider": "anthropic",
                        "extra_headers": {"Authorization": f"Bearer sk-ant-oat-fake-{account}"},
                    }
                }
                if credential_kind == "oauth"
                else {"api_key": f"fake-{account}"}
            )
            if account is not None
            else {}
        )
        if surface == "messages":
            max_tokens: Final = litellm.get_max_tokens(_MODEL)
            assert max_tokens is not None
            return await router.aanthropic_messages(
                model="claude",
                messages=[{"role": "user", "content": "hello"}],
                client=client,
                max_tokens=max_tokens,
                **credentials,
            )
        return await router.acompletion(
            model="claude",
            messages=[{"role": "user", "content": "hello"}],
            client=client,
            **credentials,
        )

    try:
        for _ in range(2):
            with pytest.raises(litellm.RateLimitError):
                await call("limited")
            await GLOBAL_LOGGING_WORKER.flush()
        limited_calls: Final = len(route.calls)
        assert limited_calls == 2
        await call("healthy")
        assert len(route.calls) == limited_calls + 1
        assert route.calls[-1].request.headers.get(
            "authorization", route.calls[-1].request.headers.get("x-api-key", "")
        ) == ("Bearer sk-ant-oat-fake-healthy" if credential_kind == "oauth" else "fake-healthy")
        await call(None)
        assert len(route.calls) == limited_calls + 2
        assert route.calls[-1].request.headers["x-api-key"] == "fake-gateway-key"
        await call("limited")
        assert len(route.calls) == limited_calls + 3
    finally:
        await GLOBAL_LOGGING_WORKER.flush()
        await client.close()
