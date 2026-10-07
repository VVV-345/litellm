"""为号池异步 PostgreSQL 集成测试注册兼容的事件循环工厂。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

import pytest
from pytest import Config, Item


@pytest.hookimpl
def pytest_asyncio_loop_factories(
    config: Config,
    item: Item,
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    del config, item
    return {"selector": asyncio.SelectorEventLoop}
