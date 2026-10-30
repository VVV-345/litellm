from typing import Final
from unittest.mock import MagicMock

import httpx
import pytest

from litellm.litellm_core_utils.litellm_logging import Logging
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.llms.anthropic.experimental_pass_through.adapters.streaming_iterator import (
    AnthropicSSEStream,
    AnthropicStreamWrapper,
)
from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import AnthropicAdapter
from litellm.types.utils import Delta, StreamingChoices


def _make_chunk(delta: Delta, finish_reason: str | None = None) -> MagicMock:
    chunk: Final = MagicMock()
    chunk.choices = [StreamingChoices(finish_reason=finish_reason, index=0, delta=delta, logprobs=None)]
    chunk.usage = None
    chunk._hidden_params = {}
    return chunk


class _AsyncStream:
    def __init__(self, items: list[MagicMock]) -> None:
        self._it: Final = iter(items)

    def __aiter__(self) -> "_AsyncStream":
        return self

    async def __anext__(self) -> MagicMock:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _CloseableAsyncStream(_AsyncStream):
    def __init__(self, items: list[MagicMock]) -> None:
        super().__init__(items)
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def test_async_translation_returns_closable_sse_stream() -> None:
    stream: Final = AnthropicAdapter().translate_completion_output_params_streaming(
        completion_stream=_AsyncStream([_make_chunk(Delta(content="hi"))]),
        model="test-model",
    )
    assert isinstance(stream, AnthropicSSEStream)


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_sse_stream_close_releases_http_response_before_upstream_body(started: bool) -> None:
    response: Final = httpx.Response(200, stream=httpx.ByteStream(b"unread upstream body"))
    upstream: Final = CustomStreamWrapper(
        completion_stream=response,
        model="test-model",
        custom_llm_provider="openai",
        logging_obj=Logging(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
            call_type="acompletion",
            start_time=1.0,
            litellm_call_id="disconnect-test",
            function_id="test",
        ),
    )
    stream: Final = AnthropicSSEStream(AnthropicStreamWrapper(completion_stream=upstream, model="test-model"))
    if started:
        assert (await anext(stream)).startswith(b"event: message_start")
    assert not response.is_stream_consumed
    assert not response.is_closed

    await stream.aclose()
    await stream.aclose()

    assert response.is_closed
    assert not response.is_stream_consumed
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_stream", [False, True])
async def test_sse_stream_releases_upstream_once_after_content(finish_stream: bool) -> None:
    upstream: Final = _CloseableAsyncStream(
        [_make_chunk(Delta(content="hello")), _make_chunk(Delta(), finish_reason="stop")]
    )
    stream: Final = AnthropicSSEStream(AnthropicStreamWrapper(completion_stream=upstream, model="test-model"))
    events: Final[list[bytes]] = []
    async for event in stream:
        events.append(event)
        if not finish_stream and b"event: content_block_delta" in event:
            break
    assert any(b'"text": "hello"' in event or b'"text":"hello"' in event for event in events)
    if finish_stream:
        assert events[-1].startswith(b"event: message_stop")

    await stream.aclose()
    await stream.aclose()

    assert upstream.close_calls == 1
