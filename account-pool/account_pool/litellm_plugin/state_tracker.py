"""接收 LiteLLM 请求结果回调，并向号池幂等上报结算和释放事件。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime
from typing import Final, cast

import httpx

from litellm.integrations.custom_logger import CustomLogger


class AccountPoolStateTracker(CustomLogger):
    def __init__(self) -> None:
        super().__init__()  # pyright: ignore[reportUnknownMemberType]  # LiteLLM base initializer has untyped kwargs
        self._pool_url: Final = os.environ.get("ACCOUNT_POOL_URL", "http://127.0.0.1:4100").rstrip("/")
        self._token: Final = os.environ.get("ACCOUNT_POOL_INTERNAL_TOKEN")

    async def async_log_success_event(
        self,
        kwargs: dict[str, object],
        response_obj: object,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        lease_id: Final = _lease_id(kwargs)
        if lease_id is None:
            return
        usage: Final = getattr(response_obj, "usage", None)
        await self._settle_and_release(
            lease_id=lease_id,
            success=True,
            status_code=200,
            input_tokens=_usage_value(usage, "prompt_tokens", "input_tokens"),
            output_tokens=_usage_value(usage, "completion_tokens", "output_tokens"),
            latency_ms=(end_time - start_time).total_seconds() * 1000,
            error_type=None,
        )

    async def async_log_failure_event(
        self,
        kwargs: dict[str, object],
        response_obj: object,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        lease_id: Final = _lease_id(kwargs)
        if lease_id is None:
            return
        status_code: Final = getattr(response_obj, "status_code", None)
        error_type: Final = "provider_auth" if status_code in {401, 403} else type(response_obj).__name__
        await self._settle_and_release(
            lease_id=lease_id,
            success=False,
            status_code=status_code if isinstance(status_code, int) else None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=(end_time - start_time).total_seconds() * 1000,
            error_type=error_type,
        )

    async def _settle_and_release(
        self,
        lease_id: str,
        success: bool,
        status_code: int | None,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        error_type: str | None,
    ) -> None:
        headers: Final = {"x-account-pool-token": self._token} if self._token is not None else {}
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{self._pool_url}/internal/settle",
                headers=headers,
                json={
                    "lease_id": lease_id,
                    "success": success,
                    "status_code": status_code,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                    "error_type": error_type,
                },
            )
            await client.post(
                f"{self._pool_url}/internal/release",
                headers=headers,
                json={"lease_id": lease_id},
            )


def _lease_id(kwargs: dict[str, object]) -> str | None:
    litellm_params: Final = _mapping(kwargs.get("litellm_params"))
    if litellm_params is None:
        return None
    metadata: Final = _mapping(litellm_params.get("metadata"))
    if metadata is None:
        return None
    value: Final = metadata.get("account_pool_lease_id")
    return value if isinstance(value, str) else None


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[str, object], value)


def _usage_value(usage: object, primary: str, alternate: str) -> int:
    value: Final = getattr(usage, primary, getattr(usage, alternate, 0))
    return value if isinstance(value, int) else 0


proxy_handler_instance: Final = AccountPoolStateTracker()
