"""Boundary coverage for reasoning effort normalization on the ``/v1/messages`` adapter.

``test_reasoning_effort_fields.py`` pins ``normalize_reasoning_effort_value`` itself. These tests
sit one layer out, on the kwargs the handler actually hands to ``litellm.acompletion``, so the
regression they guard is the one a caller sees: a tier the proxy advertises has to be the tier that
leaves the adapter, in the shape the target expects.
"""

import pytest

from litellm.llms.anthropic.experimental_pass_through.adapters.handler import (
    LiteLLMMessagesToCompletionTransformationHandler,
)

MESSAGES = [{"role": "user", "content": "hello"}]


def _reasoning_effort_sent(model: str, provider: str, reasoning_effort: object, thinking=None) -> object:
    completion_kwargs, _ = LiteLLMMessagesToCompletionTransformationHandler._prepare_completion_kwargs(
        max_tokens=1024,
        messages=MESSAGES,
        model=model,
        metadata=None,
        stop_sequences=None,
        stream=False,
        system=None,
        temperature=None,
        thinking=thinking,
        tool_choice=None,
        tools=None,
        top_k=None,
        top_p=None,
        output_format=None,
        extra_kwargs={"custom_llm_provider": provider, "reasoning_effort": reasoning_effort},
    )
    return completion_kwargs.get("reasoning_effort")


def test_gpt6_astra_messages_preserves_explicit_effort():
    assert _reasoning_effort_sent("gpt-6-astra", "openai", "xhigh") == "xhigh"


def test_gpt6_astra_messages_rejects_conflicting_thinking():
    from litellm import BadRequestError

    with pytest.raises(BadRequestError):
        _reasoning_effort_sent("gpt-6-astra", "openai", "high", {"type": "disabled"})


@pytest.mark.parametrize("model", [
    "gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol", "gpt-6.1-terra",
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "codex-auto-review",
])
@pytest.mark.asyncio
@pytest.mark.parametrize("display,summary", [("omitted", None), ("summarized", "auto")])
@pytest.mark.parametrize("explicit", [None, "high", "max", "xhigh", "low", "medium"])
@pytest.mark.parametrize("kind", ["enabled", "adaptive"])
@pytest.mark.parametrize("stream", [False, True])
async def test_reasoning_family_messages_wire_preserves_thinking_intent(
    monkeypatch, model, display, summary, explicit, kind, stream,
):
    import json

    import httpx
    import litellm
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

    monkeypatch.setenv("LITELLM_REASONING_AUTO_SUMMARY", "true")
    captured = []

    def upstream(request):
        captured.append((request.url.path, json.loads(request.content)))
        payload = {
            "id": "resp_messages", "object": "response", "created_at": 1, "status": "completed",
            "model": model, "output": [{"type": "message", "id": "msg", "status": "completed",
                "role": "assistant", "content": [{"type": "output_text", "text": "OK", "annotations": []}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        if json.loads(request.content).get("stream"):
            events = [
                {"type": "response.created", "response": {**payload, "status": "in_progress", "output": []}},
                {"type": "response.output_text.delta", "item_id": "msg", "output_index": 0,
                 "content_index": 0, "delta": "OK"},
                {"type": "response.completed", "response": payload},
            ]
            return httpx.Response(200, text="".join(
                "data: " + json.dumps({**x, "sequence_number": i}) + "\n\n" for i, x in enumerate(events)
            ), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http_client:
        client = AsyncHTTPHandler()
        await client.client.aclose()
        client.client = http_client
        result = await litellm.anthropic.messages.acreate(
            model="openai/" + model, messages=MESSAGES, max_tokens=512, api_key="test",
            api_base="https://fixture.invalid/v1", client=client, stream=stream,
            thinking={"type": kind, "display": display,
                      **({"budget_tokens": 32768} if explicit in (None, "high") else {})},
            **({"output_config": {"effort": "max"}} if explicit == "max" else {"reasoning_effort": explicit}),
        )
        if stream:
            raw_events = b"".join([event async for event in result]).decode()
            events = [json.loads(line[6:]) for line in raw_events.splitlines() if line.startswith("data: ")]
            assert "".join(event.get("delta", {}).get("text", "") for event in events) == "OK"
            assert sum(event["type"] == "message_stop" for event in events) == 1
        else:
            assert result["content"][-1]["text"] == "OK"
    assert len(captured) == 1
    path, body = captured[0]
    assert path == "/v1/responses"
    assert body["model"] == model
    assert body["reasoning"]["effort"] == (
        "max" if explicit == "max" else explicit or ("high" if kind == "enabled" else "medium")
    )
    assert body["reasoning"].get("summary") == summary
    assert "thinking" not in body


class TestTheNormalizedTierIsTheTierSent:
    """The bug in the caller's terms: a proxy advertising kimi-k3 ``max`` accepted the request and
    then put ``high`` on the wire. Every spelling of the entry has to survive the adapter, including
    the provider-prefixed model name the handler is actually called with."""

    @pytest.mark.parametrize(
        "model, provider",
        [
            ("kimi-k3", "moonshot"),
            ("kimi-k3", "fireworks_ai"),
            ("fireworks_ai/kimi-k3", "fireworks_ai"),
            ("kimi-k3-us", "fireworks_ai"),
            ("FW-Kimi-K3", "azure_ai"),
        ],
    )
    def test_a_declared_tier_reaches_the_outgoing_request(self, local_model_cost_map, model, provider):
        assert _reasoning_effort_sent(model, provider, "max") == "max"

    @pytest.mark.parametrize("effort, expected", [("xhigh", "high"), ("minimal", "low")])
    def test_a_tier_the_entry_does_not_declare_still_degrades(self, local_model_cost_map, effort, expected):
        assert _reasoning_effort_sent("kimi-k3", "fireworks_ai", effort) == expected

    def test_the_fallback_is_a_tier_the_deployment_accepts(self, local_model_cost_map):
        """gpt-5.5-pro refuses ``low``, the floor the ``minimal`` chain used to stop on, so stopping
        there would have sent a level the model map says the model rejects."""
        assert _reasoning_effort_sent("gpt-5.5-pro", "azure", "minimal") == "medium"

    @pytest.mark.parametrize(
        "model, provider, expected",
        [("kimi-k3", "fireworks_ai", "max"), ("gpt-5-mini", "azure", "high")],
    )
    def test_the_dict_form_normalizes_effort_and_keeps_its_siblings(
        self, local_model_cost_map, model, provider, expected
    ):
        sent = _reasoning_effort_sent(model, provider, {"effort": "max", "summary": "detailed"})

        assert sent == {"effort": expected, "summary": "detailed"}

    @pytest.mark.parametrize(
        "model, provider, effort, expected",
        [("claude-opus-4-7", "anthropic", "max", "max"), ("gpt-5-mini", "azure", "max", "high")],
    )
    def test_an_entry_on_the_per_level_flags_is_unchanged(
        self, local_model_cost_map, model, provider, effort, expected
    ):
        assert _reasoning_effort_sent(model, provider, effort) == expected
