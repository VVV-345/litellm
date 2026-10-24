"""验证 Manager startup 失败时释放应用管理 clients。"""

from __future__ import annotations

from types import TracebackType
from typing import Final

import httpx
import pytest
from account_pool.app import (
    _close_cockpit_clients,  # pyright: ignore[reportPrivateUsage]  # test the lifecycle boundary directly.
)
from fastapi import FastAPI


class _FailingLifespan:
    async def __aenter__(self) -> None:
        raise RuntimeError("fixture startup failure")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False


def _failing_lifespan(_: FastAPI) -> _FailingLifespan:
    return _FailingLifespan()


@pytest.mark.asyncio
async def test_cockpit_clients_close_when_manager_startup_fails() -> None:
    cockpit_client: Final = httpx.AsyncClient()
    subject_client: Final = httpx.AsyncClient()
    with pytest.raises(RuntimeError, match="fixture startup failure"):
        async with _close_cockpit_clients(
            _failing_lifespan, FastAPI(), (cockpit_client, subject_client),
        ):
            pytest.fail("the failing lifespan must not yield")
    assert cockpit_client.is_closed
    assert subject_client.is_closed
