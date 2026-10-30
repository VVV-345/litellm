"""排队和元数据帧不算已有输出，真实增量才把门闩打开。"""

from typing import Final

from litellm.proxy.management_endpoints.account_pool_stream import EventStream


def test_queued_and_metadata_stay_meaningless_until_real_output() -> None:
    state: Final = EventStream(responses_api=True)
    state.observe_payload({"type": "response.queued"})
    assert state.meaningful is False
    state.observe_payload({"type": "response.metadata"})
    assert state.meaningful is False
    state.observe_payload({"type": "response.output_text.delta", "delta": "hello"})
    assert state.meaningful is True
