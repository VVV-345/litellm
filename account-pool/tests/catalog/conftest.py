import asyncio
import os
import selectors
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final

LoopFactory = Callable[[], asyncio.AbstractEventLoop]


def _selector_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def pytest_asyncio_loop_factories() -> Mapping[str, LoopFactory]:
    platform_factory: Final = _selector_event_loop if os.name == "nt" else asyncio.new_event_loop
    return MappingProxyType({"platform": platform_factory})
