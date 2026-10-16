"""
Tests for encrypted_content_affinity pre-call check.

The mechanism works without any cache and supports two encoding strategies:

1. **Items with IDs**: item IDs for output items with `encrypted_content` are rewritten to
   `encitem_{base64("litellm:model_id:{model_id};item_id:{original_id}")}`.

2. **Items without IDs** (Codex): encrypted_content itself is wrapped with model_id metadata:
   `litellm_enc:{base64("model_id:{model_id}")};{original_encrypted_content}`.

- On routing: `EncryptedContentAffinityCheck` decodes from either item IDs or wrapped
  encrypted_content to extract `model_id` and pins the request to that deployment.
- Before forwarding: `_restore_encrypted_content_item_ids_in_input` decodes IDs and unwraps
  encrypted_content back to their original forms before sending to the upstream provider.
"""

import copy
import time
from typing import List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from litellm import Router
from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import EncryptedContentAffinityCheck


import litellm
from litellm.responses.utils import ResponsesAPIRequestUtils
from litellm.types.llms.openai import ResponsesAPIResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_mock_response(output_items, response_id="resp_mock-123"):
    """Build a ResponsesAPIResponse that ``async_response_api_handler`` would return."""
    return ResponsesAPIResponse(
        id=response_id,
        created_at=1741476542,
        status="completed",
        model="openai/gpt-5.1-codex",
        output=output_items,
        usage={"input_tokens": 5, "output_tokens": 10, "total_tokens": 15},
    )


def _get_item_id(item) -> str:
    """Extract item ID from either a Pydantic model or a dict."""
    if isinstance(item, dict):
        return item.get("id", "")
    return getattr(item, "id", "") or ""


def _extract_encoded_item_id(response) -> str:
    """Return the first ``encitem_``-prefixed item ID from the response output."""
    for item in response.output or []:
        item_id = _get_item_id(item)
        if item_id.startswith("encitem_"):
            return item_id
    return ""


# ---------------------------------------------------------------------------
# Unit tests for encoding / decoding utilities
# ---------------------------------------------------------------------------


class TestEncryptedItemIdCodec:
    def test_roundtrip(self):
        model_id = "deployment-1"
        original_item_id = "rs_abc123def456"
        encoded = ResponsesAPIRequestUtils._build_encrypted_item_id(
            model_id, original_item_id
        )
        assert encoded.startswith("encitem_")
        decoded = ResponsesAPIRequestUtils._decode_encrypted_item_id(encoded)
        assert decoded is not None
        assert decoded["model_id"] == model_id
        assert decoded["item_id"] == original_item_id

    def test_decode_without_padding(self):
        """Decoding must succeed even if base64 padding (=) was stripped in transit."""
        model_id = "gpt-5.1-codex-openai-2"
        original_item_id = "rs_0efb96cb222403210069a01d5d52588196a9dc394ffdb89d00"
        encoded = ResponsesAPIRequestUtils._build_encrypted_item_id(
            model_id, original_item_id
        )
        # Strip any trailing '=' to simulate what happens in transit
        stripped = encoded.rstrip("=")
        decoded = ResponsesAPIRequestUtils._decode_encrypted_item_id(stripped)
        assert decoded is not None
        assert decoded["model_id"] == model_id
        assert decoded["item_id"] == original_item_id

    def test_non_encoded_id_returns_none(self):
        assert ResponsesAPIRequestUtils._decode_encrypted_item_id("rs_abc123") is None
        assert ResponsesAPIRequestUtils._decode_encrypted_item_id("msg_abc") is None
        assert ResponsesAPIRequestUtils._decode_encrypted_item_id("") is None

    def test_semicolon_in_item_id(self):
        """item_id values containing ';' must survive the roundtrip."""
        model_id = "deployment-1"
        original_item_id = "rs_part1;part2;part3"
        encoded = ResponsesAPIRequestUtils._build_encrypted_item_id(
            model_id, original_item_id
        )
        decoded = ResponsesAPIRequestUtils._decode_encrypted_item_id(encoded)
        assert decoded is not None
        assert decoded["item_id"] == original_item_id


class TestUpdateEncryptedContentItemIds:
    def test_rewrites_encrypted_items_in_dict_response(self):
        model_id = "deployment-1"
        response = {
            "id": "resp_123",
            "output": [
                {"id": "msg_abc", "type": "message", "content": []},
                {"id": "rs_xyz", "type": "reasoning", "encrypted_content": "secret"},
            ],
        }
        result = (
            ResponsesAPIRequestUtils._update_encrypted_content_item_ids_in_response(
                response, model_id
            )
        )
        # Plain message item untouched
        assert result["output"][0]["id"] == "msg_abc"
        # Reasoning item with encrypted_content gets encoded
        encoded_id = result["output"][1]["id"]
        assert encoded_id.startswith("encitem_")
        decoded = ResponsesAPIRequestUtils._decode_encrypted_item_id(encoded_id)
        assert decoded["model_id"] == model_id
        assert decoded["item_id"] == "rs_xyz"

    def test_no_op_when_model_id_is_none(self):
        response = {
            "output": [
                {"id": "rs_xyz", "type": "reasoning", "encrypted_content": "secret"}
            ]
        }
        result = (
            ResponsesAPIRequestUtils._update_encrypted_content_item_ids_in_response(
                response, None
            )
        )
        assert result["output"][0]["id"] == "rs_xyz"


class TestEncryptedContentWrapping:
    def test_wrap_and_unwrap_encrypted_content(self):
        """Test wrapping encrypted_content with model_id metadata."""
        model_id = "deployment-1"
        original_content = "gAAAAABpnW_yEYmSNEyOG_original_encrypted_data"
        wrapped = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id(
            original_content, model_id
        )
        assert wrapped.startswith("litellm_enc:")
        assert wrapped != original_content

        (
            unwrapped_model_id,
            unwrapped_content,
        ) = ResponsesAPIRequestUtils._unwrap_encrypted_content_with_model_id(wrapped)
        assert unwrapped_model_id == model_id
        assert unwrapped_content == original_content

    def test_unwrap_plain_encrypted_content(self):
        """Unwrapping plain encrypted_content returns None for model_id."""
        plain_content = "gAAAAABpnW_yEYmSNEyOG_plain_content"
        (
            model_id,
            content,
        ) = ResponsesAPIRequestUtils._unwrap_encrypted_content_with_model_id(
            plain_content
        )
        assert model_id is None
        assert content == plain_content

    def test_update_response_wraps_encrypted_content_without_id(self):
        """Items with encrypted_content but no ID get the content wrapped."""
        model_id = "deployment-1"
        response = {
            "id": "resp_123",
            "output": [
                {"type": "message", "content": []},
                {
                    "type": "reasoning",
                    "encrypted_content": "gAAAAABpnW_yEYmSNEyOG_secret",
                },
            ],
        }
        result = (
            ResponsesAPIRequestUtils._update_encrypted_content_item_ids_in_response(
                response, model_id
            )
        )
        assert result["output"][0].get("encrypted_content") is None
        wrapped = result["output"][1]["encrypted_content"]
        assert wrapped.startswith("litellm_enc:")

        (
            model_id_extracted,
            unwrapped,
        ) = ResponsesAPIRequestUtils._unwrap_encrypted_content_with_model_id(wrapped)
        assert model_id_extracted == model_id
        assert unwrapped == "gAAAAABpnW_yEYmSNEyOG_secret"


class TestRestoreEncryptedContentItemIds:
    def test_restores_encoded_ids(self):
        model_id = "deployment-1"
        original_id = "rs_encrypted_item_456"
        encoded_id = ResponsesAPIRequestUtils._build_encrypted_item_id(
            model_id, original_id
        )

        request_input = [
            {"type": "message", "id": "msg_abc123", "role": "assistant"},
            {"type": "reasoning", "id": encoded_id, "encrypted_content": "secret"},
        ]
        restored = (
            ResponsesAPIRequestUtils._restore_encrypted_content_item_ids_in_input(
                request_input
            )
        )
        assert restored[0]["id"] == "msg_abc123"
        assert restored[1]["id"] == original_id

    def test_unwraps_encrypted_content(self):
        """Test that wrapped encrypted_content is unwrapped before forwarding."""
        model_id = "deployment-1"
        original_content = "gAAAAABpnW_yEYmSNEyOG_original"
        wrapped_content = (
            ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id(
                original_content, model_id
            )
        )

        request_input = [
            {"type": "reasoning", "encrypted_content": wrapped_content},
        ]
        restored = (
            ResponsesAPIRequestUtils._restore_encrypted_content_item_ids_in_input(
                request_input
            )
        )
        assert restored[0]["encrypted_content"] == original_content

    def test_no_op_for_plain_string_input(self):
        result = ResponsesAPIRequestUtils._restore_encrypted_content_item_ids_in_input(
            "Hello world"
        )
        assert result == "Hello world"

    def test_no_op_for_unencoded_ids(self):
        request_input = [{"type": "message", "id": "msg_plain"}]
        result = ResponsesAPIRequestUtils._restore_encrypted_content_item_ids_in_input(
            request_input
        )
        assert result[0]["id"] == "msg_plain"


# ---------------------------------------------------------------------------
# Integration tests (router-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_encrypted_content_affinity_tracks_and_routes():
    """
    The first response rewrites encrypted-content item IDs to encoded form.
    The follow-up request with those encoded IDs is pinned to the same deployment.

    Mocks ``async_response_api_handler`` (the method that makes the HTTP call)
    so the test is deterministic regardless of the HTTP transport in use.
    The ``@client`` decorator and ``_update_responses_api_response_id_with_model_id``
    post-processing still run, so item-ID rewriting is exercised end-to-end.
    """
    mock_resp = _build_mock_response(
        output_items=[
            {
                "type": "message",
                "id": "msg_abc123",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Hello!", "annotations": []}
                ],
            },
            {
                "type": "reasoning",
                "id": "rs_encrypted_item_456",
                "status": "completed",
                "encrypted_content": "gAAAAABpnW_yEYmSNEyOG...",
            },
        ],
    )

    router = litellm.Router(
        model_list=[
            {
                "model_name": "openai.gpt-5.1-codex",
                "litellm_params": {
                    "model": "openai/gpt-5.1-codex",
                    "api_key": "mock-api-key-1",
                },
                "model_info": {"id": "deployment-1"},
            },
            {
                "model_name": "openai.gpt-5.1-codex",
                "litellm_params": {
                    "model": "openai/gpt-5.1-codex",
                    "api_key": "mock-api-key-2",
                },
                "model_info": {"id": "deployment-2"},
            },
        ],
        optional_pre_call_checks=["encrypted_content_affinity"],
        num_retries=0,
    )

    selected_deployments = []

    def deterministic_choice(seq):
        if len(selected_deployments) == 0:
            return seq[0]
        return seq[1] if len(seq) > 1 else seq[0]

    with (
        patch(
            "litellm.llms.custom_httpx.llm_http_handler.BaseLLMHTTPHandler.async_response_api_handler",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ),
        patch(
            "litellm.router_strategy.simple_shuffle.random.choice",
            side_effect=deterministic_choice,
        ),
    ):
        # First request — goes to deployment-1 via deterministic_choice
        first_response = await router.aresponses(
            model="openai.gpt-5.1-codex",
            input="Hello, how are you?",
        )
        first_model_id = first_response._hidden_params["model_id"]
        selected_deployments.append(first_model_id)

        # The response must have rewritten the encrypted item's ID to encoded form
        encoded_item_id = _extract_encoded_item_id(first_response)
        assert encoded_item_id.startswith(
            "encitem_"
        ), f"Expected output item ID to be rewritten to encitem_... but got {encoded_item_id!r}"

        # Verify the encoded ID decodes back to the correct deployment + original ID
        decoded = ResponsesAPIRequestUtils._decode_encrypted_item_id(encoded_item_id)
        assert decoded is not None
        assert decoded["model_id"] == first_model_id
        assert decoded["item_id"] == "rs_encrypted_item_456"

        # Second request: use the encoded item IDs from the first response
        second_response = await router.aresponses(
            model="openai.gpt-5.1-codex",
            input=[
                {"type": "message", "id": "msg_abc123", "role": "assistant"},
                {
                    "type": "reasoning",
                    "id": encoded_item_id,
                    "encrypted_content": "gAAAAABpnW_yEYmSNEyOG...",
                },
            ],
        )
        second_model_id = second_response._hidden_params["model_id"]

        assert (
            second_model_id == first_model_id
        ), f"Expected affinity to route to {first_model_id}, but got {second_model_id}"


@pytest.mark.asyncio
async def test_encrypted_content_affinity_no_effect_on_chat_completions():
    """
    Encrypted content affinity should not affect regular chat completions.
    """
    router = litellm.Router(
        model_list=[
            {
                "model_name": "gpt-3.5-turbo",
                "litellm_params": {
                    "model": "gpt-3.5-turbo",
                    "api_key": "test-key",
                    "mock_response": "Hello from chat completion!",
                },
                "model_info": {"id": "chat-deployment-1"},
            },
        ],
        optional_pre_call_checks=["encrypted_content_affinity"],
        num_retries=0,
    )

    response1 = await router.acompletion(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
    )
    response2 = await router.acompletion(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello again"}],
    )
    assert response1.id is not None
    assert response2.id is not None


@pytest.mark.asyncio
async def test_encrypted_content_affinity_bypasses_rpm_limits():
    """
    When encrypted content affinity pins to a deployment, the request
    goes through even if normal routing would avoid it (usage-based-routing-v2).
    """
    mock_resp = _build_mock_response(
        output_items=[
            {
                "type": "reasoning",
                "id": "rs_encrypted_must_pin",
                "status": "completed",
                "encrypted_content": "gAAAAABpnW_yEYmSNEyOG...",
            },
        ],
        response_id="resp_mock-rpm-test",
    )

    router = litellm.Router(
        model_list=[
            {
                "model_name": "openai.gpt-5.1-codex",
                "litellm_params": {
                    "model": "openai/gpt-5.1-codex",
                    "api_key": "mock-api-key-1",
                },
                "model_info": {"id": "deployment-alpha"},
            },
            {
                "model_name": "openai.gpt-5.1-codex",
                "litellm_params": {
                    "model": "openai/gpt-5.1-codex",
                    "api_key": "mock-api-key-2",
                },
                "model_info": {"id": "deployment-beta"},
            },
        ],
        optional_pre_call_checks=["encrypted_content_affinity"],
        routing_strategy="usage-based-routing-v2",
        num_retries=0,
    )

    selected_deployments = []

    def deterministic_choice(seq):
        if len(selected_deployments) == 0:
            return seq[0]
        return seq[1] if len(seq) > 1 else seq[0]

    with (
        patch(
            "litellm.llms.custom_httpx.llm_http_handler.BaseLLMHTTPHandler.async_response_api_handler",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ),
        patch(
            "litellm.router_strategy.simple_shuffle.random.choice",
            side_effect=deterministic_choice,
        ),
    ):
        first_response = await router.aresponses(
            model="openai.gpt-5.1-codex",
            input="Initial request",
        )
        first_model_id = first_response._hidden_params["model_id"]
        selected_deployments.append(first_model_id)

        # Extract encoded item ID from the first response output
        encoded_item_id = _extract_encoded_item_id(first_response)
        assert encoded_item_id.startswith(
            "encitem_"
        ), f"Expected encitem_... but got {encoded_item_id!r}"

        # Follow-up with the encoded item ID — should pin to same deployment
        second_response = await router.aresponses(
            model="openai.gpt-5.1-codex",
            input=[
                {
                    "type": "reasoning",
                    "id": encoded_item_id,
                    "encrypted_content": "gAAAAABpnW_yEYmSNEyOG...",
                },
            ],
        )
        second_model_id = second_response._hidden_params["model_id"]

        assert second_model_id == first_model_id


@pytest.mark.asyncio
async def test_encrypted_content_affinity_no_match_normal_routing():
    """
    Input items with non-encoded IDs (no encitem_ prefix) fall through to
    normal load balancing.
    """
    mock_resp = _build_mock_response(
        output_items=[
            {
                "type": "message",
                "id": "msg_new",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Response"}],
            },
        ],
        response_id="resp_mock-no-match",
    )

    router = litellm.Router(
        model_list=[
            {
                "model_name": "openai.gpt-5.1-codex",
                "litellm_params": {
                    "model": "openai/gpt-5.1-codex",
                    "api_key": "mock-api-key-1",
                },
                "model_info": {"id": "deployment-a"},
            },
            {
                "model_name": "openai.gpt-5.1-codex",
                "litellm_params": {
                    "model": "openai/gpt-5.1-codex",
                    "api_key": "mock-api-key-2",
                },
                "model_info": {"id": "deployment-b"},
            },
        ],
        optional_pre_call_checks=["encrypted_content_affinity"],
        num_retries=0,
    )

    with patch(
        "litellm.llms.custom_httpx.llm_http_handler.BaseLLMHTTPHandler.async_response_api_handler",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        # Non-encoded item ID — no affinity should kick in
        response = await router.aresponses(
            model="openai.gpt-5.1-codex",
            input=[
                {"type": "message", "id": "unknown_item_id_12345"},
            ],
        )
        assert response.id is not None


@pytest.mark.asyncio
async def test_encrypted_content_affinity_with_wrapped_content_no_id():
    """
    Test affinity routing when items have wrapped encrypted_content but no ID.
    This simulates Codex client behavior where IDs are omitted.
    """
    mock_resp = _build_mock_response(
        output_items=[
            {
                "type": "reasoning",
                "status": "completed",
                "encrypted_content": "gAAAAABpnW_yEYmSNEyOG_original_content",
            },
        ],
        response_id="resp_mock-wrapped-content",
    )

    router = litellm.Router(
        model_list=[
            {
                "model_name": "openai.gpt-5.1-codex",
                "litellm_params": {
                    "model": "openai/gpt-5.1-codex",
                    "api_key": "mock-api-key-1",
                },
                "model_info": {"id": "deployment-1"},
            },
            {
                "model_name": "openai.gpt-5.1-codex",
                "litellm_params": {
                    "model": "openai/gpt-5.1-codex",
                    "api_key": "mock-api-key-2",
                },
                "model_info": {"id": "deployment-2"},
            },
        ],
        optional_pre_call_checks=["encrypted_content_affinity"],
        num_retries=0,
    )

    selected_deployments = []

    def deterministic_choice(seq):
        if len(selected_deployments) == 0:
            return seq[0]
        return seq[1] if len(seq) > 1 else seq[0]

    with (
        patch(
            "litellm.llms.custom_httpx.llm_http_handler.BaseLLMHTTPHandler.async_response_api_handler",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ),
        patch(
            "litellm.router_strategy.simple_shuffle.random.choice",
            side_effect=deterministic_choice,
        ),
    ):
        # First request — goes to deployment-1
        first_response = await router.aresponses(
            model="openai.gpt-5.1-codex",
            input="Hello, how are you?",
        )
        first_model_id = first_response._hidden_params["model_id"]
        selected_deployments.append(first_model_id)

        # Extract wrapped encrypted_content from first response
        first_item = first_response.output[0]
        wrapped_content = (
            first_item.encrypted_content
            if hasattr(first_item, "encrypted_content")
            else first_item.get("encrypted_content")
        )
        assert wrapped_content.startswith(
            "litellm_enc:"
        ), f"Expected wrapped content but got {wrapped_content[:50]}..."

        # Verify we can extract model_id from wrapped content
        (
            extracted_model_id,
            _,
        ) = ResponsesAPIRequestUtils._unwrap_encrypted_content_with_model_id(
            wrapped_content
        )
        assert extracted_model_id == first_model_id

        # Second request: use wrapped encrypted_content WITHOUT an ID (Codex behavior)
        second_response = await router.aresponses(
            model="openai.gpt-5.1-codex",
            input=[
                {
                    "type": "reasoning",
                    "encrypted_content": wrapped_content,
                },
            ],
        )
        second_model_id = second_response._hidden_params["model_id"]

        assert (
            second_model_id == first_model_id
        ), f"Expected affinity to route to {first_model_id}, but got {second_model_id}"


def test_encrypted_content_wrapping_preserves_original_content():
    """
    Test that wrapping and unwrapping encrypted_content preserves the original content.
    This is critical for streaming responses where content must round-trip correctly.
    """
    model_id = "test-deployment-1"
    original_encrypted_content = (
        "gAAAAABpnW_yEYmSNEyOG_streaming_test_content_with_special_chars==+/"
    )

    wrapped = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id(
        original_encrypted_content, model_id
    )

    assert wrapped.startswith("litellm_enc:")
    assert wrapped != original_encrypted_content

    (
        extracted_model_id,
        unwrapped_content,
    ) = ResponsesAPIRequestUtils._unwrap_encrypted_content_with_model_id(wrapped)

    assert extracted_model_id == model_id
    assert unwrapped_content == original_encrypted_content


def test_encrypted_content_wrapping_with_multiple_semicolons():
    """
    Test that encrypted_content containing semicolons is handled correctly.
    """
    model_id = "deployment-with-semicolons"
    original_content = "gAAAAAB;some;content;with;semicolons"

    wrapped = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id(
        original_content, model_id
    )

    (
        extracted_model_id,
        unwrapped,
    ) = ResponsesAPIRequestUtils._unwrap_encrypted_content_with_model_id(wrapped)

    assert extracted_model_id == model_id
    assert unwrapped == original_content


# ---------------------------------------------------------------------------
# Regression tests: affinity check must not break tag-based routing
# ---------------------------------------------------------------------------

from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
    EncryptedContentAffinityCheck,
)


@pytest.mark.asyncio
async def test_encrypted_content_affinity_does_not_create_litellm_metadata_for_chat():
    """
    For chat completions / embeddings, request_kwargs uses 'metadata' (not
    'litellm_metadata').  The affinity check must NOT create a spurious
    'litellm_metadata' key, because that would cause
    _get_metadata_variable_name_from_kwargs to return 'litellm_metadata'
    and tag-based routing would look for tags in the wrong dict.
    """
    check = EncryptedContentAffinityCheck()
    deployments = [
        {"model_info": {"id": "dep-1"}, "litellm_params": {"model": "gpt-4"}},
    ]
    request_kwargs = {"metadata": {"tags": ["prod"]}}

    result = await check.async_filter_deployments(
        model="gpt-4",
        healthy_deployments=deployments,
        messages=[{"role": "user", "content": "hi"}],
        request_kwargs=request_kwargs,
    )

    # Must not inject litellm_metadata
    assert "litellm_metadata" not in request_kwargs
    # Tags must be untouched
    assert request_kwargs["metadata"]["tags"] == ["prod"]
    # All deployments returned (no pinning)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_encrypted_content_affinity_preserves_litellm_metadata_for_responses():
    """
    For Responses API calls, litellm_metadata already exists.  The affinity
    check should set the flag there and preserve existing keys.
    """
    check = EncryptedContentAffinityCheck()
    deployments = [
        {"model_info": {"id": "dep-1"}, "litellm_params": {"model": "gpt-5.1-codex"}},
    ]
    request_kwargs = {
        "litellm_metadata": {"model_info": {"id": "dep-1"}},
    }

    await check.async_filter_deployments(
        model="gpt-5.1-codex",
        healthy_deployments=deployments,
        messages=None,
        request_kwargs=request_kwargs,
    )

    assert (
        request_kwargs["litellm_metadata"]["encrypted_content_affinity_enabled"] is True
    )
    assert request_kwargs["litellm_metadata"]["model_info"] == {"id": "dep-1"}


def test_encrypted_content_wrapping_empty_string():
    """
    Test that empty encrypted_content is handled gracefully.
    """
    model_id = "test-deployment"
    original_content = ""

    wrapped = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id(
        original_content, model_id
    )

    assert wrapped.startswith("litellm_enc:")

    (
        extracted_model_id,
        unwrapped,
    ) = ResponsesAPIRequestUtils._unwrap_encrypted_content_with_model_id(wrapped)

    assert extracted_model_id == model_id
    assert unwrapped == original_content


# ---------------------------------------------------------------------------
# LIT-2531: cross-model-group fallback via encryption boundary (api_base + api_key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_affinity_falls_back_to_same_encryption_boundary_on_model_group_switch():
    """
    LIT-2531: Client starts a session on gpt-5.3-codex, follow-up switches to
    gpt-5.4 mid-chat (e.g. via Codex `model_migrations`). Affinity must pin to
    the gpt-5.4 deployment on the SAME Azure resource as the originating
    gpt-5.3-codex deployment -- otherwise Azure rejects the encrypted_content.
    """
    first_resp = _build_mock_response(
        output_items=[
            {
                "type": "reasoning",
                "id": "rs_encrypted_xyz",
                "status": "completed",
                "encrypted_content": "gAAAAABpnW_yEYmSNEyOG...",
            },
        ],
        response_id="resp_first",
    )
    second_resp = _build_mock_response(
        output_items=[
            {
                "type": "message",
                "id": "msg_ok",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            },
        ],
        response_id="resp_second",
    )

    ACCOUNT_A_BASE = "https://account-a.openai.azure.com/"
    ACCOUNT_A_KEY = "key-a"
    ACCOUNT_B_BASE = "https://account-b.openai.azure.com/"
    ACCOUNT_B_KEY = "key-b"

    router = litellm.Router(
        model_list=[
            {
                "model_name": "gpt-5.3-codex",
                "litellm_params": {
                    "model": "azure/gpt-5.3-codex",
                    "api_base": ACCOUNT_A_BASE,
                    "api_key": ACCOUNT_A_KEY,
                    "api_version": "2025-04-01-preview",
                },
                "model_info": {"id": "gpt-5.3-codex-account-a"},
            },
            {
                "model_name": "gpt-5.3-codex",
                "litellm_params": {
                    "model": "azure/gpt-5.3-codex",
                    "api_base": ACCOUNT_B_BASE,
                    "api_key": ACCOUNT_B_KEY,
                    "api_version": "2025-04-01-preview",
                },
                "model_info": {"id": "gpt-5.3-codex-account-b"},
            },
            {
                "model_name": "gpt-5.4",
                "litellm_params": {
                    "model": "azure/gpt-5.4",
                    "api_base": ACCOUNT_A_BASE,
                    "api_key": ACCOUNT_A_KEY,
                    "api_version": "2025-04-01-preview",
                },
                "model_info": {"id": "gpt-5.4-account-a"},
            },
            {
                "model_name": "gpt-5.4",
                "litellm_params": {
                    "model": "azure/gpt-5.4",
                    "api_base": ACCOUNT_B_BASE,
                    "api_key": ACCOUNT_B_KEY,
                    "api_version": "2025-04-01-preview",
                },
                "model_info": {"id": "gpt-5.4-account-b"},
            },
        ],
        optional_pre_call_checks=["encrypted_content_affinity"],
        num_retries=0,
    )

    def first_call_picks_account_a(seq):
        for d in seq:
            if d["model_info"]["id"] == "gpt-5.3-codex-account-a":
                return d
        return seq[0]

    with (
        patch(
            "litellm.llms.custom_httpx.llm_http_handler.BaseLLMHTTPHandler.async_response_api_handler",
            new_callable=AsyncMock,
            return_value=first_resp,
        ),
        patch(
            "litellm.router_strategy.simple_shuffle.random.choice",
            side_effect=first_call_picks_account_a,
        ),
    ):
        r1 = await router.aresponses(model="gpt-5.3-codex", input="hi")

    assert r1._hidden_params["model_id"] == "gpt-5.3-codex-account-a"
    encoded_id = _extract_encoded_item_id(r1)
    assert encoded_id.startswith("encitem_")

    # simple_shuffle.random.choice NOT patched: prove affinity narrows the
    # candidate pool to a single deployment regardless of which one shuffle picks.
    with patch(
        "litellm.llms.custom_httpx.llm_http_handler.BaseLLMHTTPHandler.async_response_api_handler",
        new_callable=AsyncMock,
        return_value=second_resp,
    ):
        r2 = await router.aresponses(
            model="gpt-5.4",
            input=[
                {
                    "type": "reasoning",
                    "id": encoded_id,
                    "encrypted_content": "gAAAAABpnW_yEYmSNEyOG...",
                },
            ],
        )

    assert r2._hidden_params["model_id"] == "gpt-5.4-account-a"


@pytest.mark.asyncio
async def test_affinity_falls_back_to_same_boundary_on_alias_switch():
    """
    LIT-2531 alias path: gpt-5.2-codex is a LiteLLM alias that points at the
    same underlying Azure model as gpt-5.3-codex. Different model_name groups
    in the router, so model_id-based pinning misses, but the encryption
    boundary (api_base + api_key) is identical -> follow-up must still pin.
    """
    first_resp = _build_mock_response(
        output_items=[
            {
                "type": "reasoning",
                "id": "rs_alias_xyz",
                "status": "completed",
                "encrypted_content": "gAAAAABpnW_yEYmSNEyOG...",
            },
        ],
        response_id="resp_alias_first",
    )
    second_resp = _build_mock_response(
        output_items=[
            {
                "type": "message",
                "id": "msg_alias_ok",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ok"}],
            },
        ],
        response_id="resp_alias_second",
    )

    ACCOUNT_A_BASE = "https://account-a.openai.azure.com/"
    ACCOUNT_A_KEY = "key-a"
    ACCOUNT_B_BASE = "https://account-b.openai.azure.com/"
    ACCOUNT_B_KEY = "key-b"

    router = litellm.Router(
        model_list=[
            {
                "model_name": "gpt-5.3-codex",
                "litellm_params": {
                    "model": "azure/gpt-5.3-codex",
                    "api_base": ACCOUNT_A_BASE,
                    "api_key": ACCOUNT_A_KEY,
                    "api_version": "2025-04-01-preview",
                },
                "model_info": {"id": "gpt-5.3-codex-account-a"},
            },
            {
                "model_name": "gpt-5.3-codex",
                "litellm_params": {
                    "model": "azure/gpt-5.3-codex",
                    "api_base": ACCOUNT_B_BASE,
                    "api_key": ACCOUNT_B_KEY,
                    "api_version": "2025-04-01-preview",
                },
                "model_info": {"id": "gpt-5.3-codex-account-b"},
            },
            {
                "model_name": "gpt-5.2-codex",
                "litellm_params": {
                    "model": "azure/gpt-5.3-codex",
                    "api_base": ACCOUNT_A_BASE,
                    "api_key": ACCOUNT_A_KEY,
                    "api_version": "2025-04-01-preview",
                },
                "model_info": {"id": "gpt-5.2-codex-account-a"},
            },
            {
                "model_name": "gpt-5.2-codex",
                "litellm_params": {
                    "model": "azure/gpt-5.3-codex",
                    "api_base": ACCOUNT_B_BASE,
                    "api_key": ACCOUNT_B_KEY,
                    "api_version": "2025-04-01-preview",
                },
                "model_info": {"id": "gpt-5.2-codex-account-b"},
            },
        ],
        optional_pre_call_checks=["encrypted_content_affinity"],
        num_retries=0,
    )

    def pick_account_a(seq):
        for d in seq:
            if d["model_info"]["id"] == "gpt-5.3-codex-account-a":
                return d
        return seq[0]

    with (
        patch(
            "litellm.llms.custom_httpx.llm_http_handler.BaseLLMHTTPHandler.async_response_api_handler",
            new_callable=AsyncMock,
            return_value=first_resp,
        ),
        patch(
            "litellm.router_strategy.simple_shuffle.random.choice",
            side_effect=pick_account_a,
        ),
    ):
        r1 = await router.aresponses(model="gpt-5.3-codex", input="hi")

    encoded_id = _extract_encoded_item_id(r1)
    assert encoded_id.startswith("encitem_")

    with patch(
        "litellm.llms.custom_httpx.llm_http_handler.BaseLLMHTTPHandler.async_response_api_handler",
        new_callable=AsyncMock,
        return_value=second_resp,
    ):
        r2 = await router.aresponses(
            model="gpt-5.2-codex",
            input=[
                {
                    "type": "reasoning",
                    "id": encoded_id,
                    "encrypted_content": "gAAAAABpnW_yEYmSNEyOG...",
                },
            ],
        )

    assert r2._hidden_params["model_id"] == "gpt-5.2-codex-account-a"


def test_boundary_fallback_no_router_ref_returns_empty():
    """
    Standalone use (no router wired in) -> the boundary lookup short-circuits
    to ``[]`` instead of crashing on ``None.get_deployment``.
    """
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    check = EncryptedContentAffinityCheck(router=None)
    healthy = [
        {
            "model_info": {"id": "dep-1"},
            "litellm_params": {"api_base": "https://x", "api_key": "k"},
        }
    ]
    matches, originating = check._find_deployments_on_same_encryption_boundary(
        healthy_deployments=healthy,
        model_id="dep-2",
    )
    assert matches == []
    assert originating is None


def test_boundary_fallback_originating_deployment_removed_returns_empty():
    """
    If the originating deployment has been removed from the router (e.g. via
    /model/delete), ``router.get_deployment`` returns None and we return [] so
    the caller falls back to the full healthy_deployments list.
    """
    from unittest.mock import MagicMock

    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    mock_router = MagicMock()
    mock_router.get_deployment.return_value = None

    check = EncryptedContentAffinityCheck(router=mock_router)
    healthy = [
        {
            "model_info": {"id": "dep-1"},
            "litellm_params": {"api_base": "https://x", "api_key": "k"},
        }
    ]
    matches, originating = check._find_deployments_on_same_encryption_boundary(
        healthy_deployments=healthy,
        model_id="dep-removed",
    )
    assert matches == []
    assert originating is None
    mock_router.get_deployment.assert_called_once_with(model_id="dep-removed")


def test_boundary_key_accepts_pydantic_litellm_params_instance():
    """
    Regression: ``_encryption_boundary_key`` must accept any object exposing
    dict-style ``.get()`` (incl. ``LiteLLM_Params`` Pydantic instances) — not
    just plain dicts.

    A stricter ``isinstance(dict)`` guard would silently return ``None`` for a
    ``LiteLLM_Params`` value, drop the deployment from boundary matching, and
    fall back to the full pool — which is the exact ``invalid_encrypted_content``
    failure this check exists to prevent.
    """
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )
    from litellm.types.router import LiteLLM_Params

    pydantic_params = LiteLLM_Params(
        model="azure/gpt-5.3-codex",
        api_base="https://mateo-resource.openai.azure.com",
        api_key="fake-azure-resource-key-a",
    )
    plain_params = {
        "model": "azure/gpt-5.3-codex",
        "api_base": "https://mateo-resource.openai.azure.com",
        "api_key": "fake-azure-resource-key-a",
    }

    pydantic_key = EncryptedContentAffinityCheck._encryption_boundary_key(
        pydantic_params
    )
    plain_key = EncryptedContentAffinityCheck._encryption_boundary_key(plain_params)

    assert pydantic_key is not None
    assert (
        pydantic_key
        == plain_key
        == (
            "https://mateo-resource.openai.azure.com",
            "fake-azure-resource-key-a",
        )
    )


def test_boundary_key_rejects_non_dict_like_inputs():
    """
    Inputs that don't expose ``.get()`` (None, lists, strings, ints) -> None.
    Guards against accidentally treating a stray non-dict-like value as a
    valid boundary.
    """
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    for bad in (None, [], "not a dict", 42, object()):
        assert EncryptedContentAffinityCheck._encryption_boundary_key(bad) is None

    assert (
        EncryptedContentAffinityCheck._encryption_boundary_key(
            {"api_base": "", "api_key": "k"}
        )
        is None
    )
    assert (
        EncryptedContentAffinityCheck._encryption_boundary_key(
            {"api_base": "https://x"}
        )
        is None
    )


# ---------------------------------------------------------------------------
# Fail-fast when originating deployment is unavailable and no boundary peer
# ---------------------------------------------------------------------------


def _make_originating_mock(api_base: str, api_key: str):
    from unittest.mock import MagicMock

    originating = MagicMock()
    originating.model_info.account_pool_environment_id = "pool-card"
    originating.litellm_params.model_dump.return_value = {
        "api_base": api_base,
        "api_key": api_key,
    }
    return originating


def _make_router_mock_with_cooldown(
    originating, cooldown_entries: Optional[List[tuple]] = None
):
    """
    Build a MagicMock router whose ``cooldown_cache.async_get_active_cooldowns``
    returns ``cooldown_entries`` (defaulting to ``[]`` — no active cooldown).
    """
    from unittest.mock import AsyncMock, MagicMock

    mock_router = MagicMock()
    mock_router.get_deployment.return_value = originating
    mock_router.cooldown_cache.async_get_active_cooldowns = AsyncMock(
        return_value=list(cooldown_entries or [])
    )
    return mock_router


@pytest.mark.asyncio
async def test_pool_affinity_raises_service_unavailable_when_origin_cooled_for_non_429():
    """
    Originating deployment is in the router config, in cooldown for a non-429
    cause (e.g. a 500), and no boundary peer is configured. The check must
    surface this as a 503 (transient, but not rate-limit-specific) rather than
    dispatching to a non-peer deployment.
    """
    from litellm.exceptions import ServiceUnavailableError
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    originating = _make_originating_mock("https://account-a.openai.azure.com/", "key-a")
    mock_router = _make_router_mock_with_cooldown(
        originating,
        cooldown_entries=[
            (
                "deployment-a-cooled",
                {
                    "exception_received": "boom",
                    "status_code": "500",
                    "timestamp": time.time(),
                    "cooldown_time": 60.0,
                },
            )
        ],
    )

    check = EncryptedContentAffinityCheck(router=mock_router)
    encoded_id = ResponsesAPIRequestUtils._build_encrypted_item_id(
        "deployment-a-cooled", "rs_test"
    )
    healthy_only_b = [
        {
            "model_info": {"id": "deployment-b"},
            "litellm_params": {
                "api_base": "https://account-b.openai.azure.com/",
                "api_key": "key-b",
                "model": "azure/gpt-5.4",
            },
        }
    ]
    request_kwargs = {
        "input": [{"id": encoded_id, "type": "reasoning"}],
    }

    with pytest.raises(ServiceUnavailableError) as excinfo:
        await check.async_filter_deployments(
            model="gpt-5.4",
            healthy_deployments=healthy_only_b,
            messages=None,
            request_kwargs=request_kwargs,
        )

    # Public error message intentionally omits the originating model_id to
    # avoid an authenticated-caller probing oracle.
    assert "deployment-a-cooled" not in str(excinfo.value)
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_pool_affinity_raises_rate_limit_with_retry_after_when_origin_cooled_for_429():
    """
    Originating deployment is in cooldown specifically because of a 429.
    The check must surface this as a 429 RateLimitError with a Retry-After
    header derived from the cooldown's remaining window, so OpenAI-compatible
    clients respect the backoff instead of giving up on a 503.
    """
    from litellm.exceptions import RateLimitError
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    originating = _make_originating_mock("https://account-a.openai.azure.com/", "key-a")
    cooldown_started = time.time() - 5.0
    mock_router = _make_router_mock_with_cooldown(
        originating,
        cooldown_entries=[
            (
                "deployment-a-cooled-429",
                {
                    "exception_received": "rate limited",
                    "status_code": "429",
                    "timestamp": cooldown_started,
                    "cooldown_time": 60.0,
                },
            )
        ],
    )

    check = EncryptedContentAffinityCheck(router=mock_router)
    encoded_id = ResponsesAPIRequestUtils._build_encrypted_item_id(
        "deployment-a-cooled-429", "rs_test"
    )
    healthy_only_b = [
        {
            "model_info": {"id": "deployment-b"},
            "litellm_params": {
                "api_base": "https://account-b.openai.azure.com/",
                "api_key": "key-b",
                "model": "azure/gpt-5.4",
            },
        }
    ]
    request_kwargs = {
        "input": [{"id": encoded_id, "type": "reasoning"}],
    }

    with pytest.raises(RateLimitError) as excinfo:
        await check.async_filter_deployments(
            model="gpt-5.4",
            healthy_deployments=healthy_only_b,
            messages=None,
            request_kwargs=request_kwargs,
        )

    assert "deployment-a-cooled-429" not in str(excinfo.value)
    assert excinfo.value.status_code == 429
    retry_after = excinfo.value.response.headers.get("retry-after")
    assert retry_after is not None
    assert 1 <= int(retry_after) <= 60


@pytest.mark.asyncio
async def test_pool_affinity_raises_service_unavailable_when_origin_filtered_without_cooldown_entry():
    """
    Originating deployment is configured but absent from healthy_deployments
    with no active cooldown entry. Surface as 503 (we cannot prove the cause
    was rate-limiting) rather than guessing 429.
    """
    from litellm.exceptions import ServiceUnavailableError
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    originating = _make_originating_mock("https://account-a.openai.azure.com/", "key-a")
    mock_router = _make_router_mock_with_cooldown(originating, cooldown_entries=[])

    check = EncryptedContentAffinityCheck(router=mock_router)
    encoded_id = ResponsesAPIRequestUtils._build_encrypted_item_id(
        "deployment-a-filtered", "rs_test"
    )
    healthy_only_b = [
        {
            "model_info": {"id": "deployment-b"},
            "litellm_params": {
                "api_base": "https://account-b.openai.azure.com/",
                "api_key": "key-b",
                "model": "azure/gpt-5.4",
            },
        }
    ]
    request_kwargs = {
        "input": [{"id": encoded_id, "type": "reasoning"}],
    }

    with pytest.raises(ServiceUnavailableError) as excinfo:
        await check.async_filter_deployments(
            model="gpt-5.4",
            healthy_deployments=healthy_only_b,
            messages=None,
            request_kwargs=request_kwargs,
        )

    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_pool_affinity_raises_bad_request_when_origin_removed():
    """
    Originating deployment was removed from the router config and no boundary
    peer is available. This is permanent (the stale encrypted_content cannot
    be honored), so surface a 400 with actionable text.
    """
    from unittest.mock import MagicMock

    from litellm.exceptions import BadRequestError
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    mock_router = MagicMock()
    mock_router.get_deployment.return_value = None

    check = EncryptedContentAffinityCheck(router=mock_router)
    encoded_id = ResponsesAPIRequestUtils._build_encrypted_item_id(
        "deployment-removed", "rs_test"
    )
    healthy_only_b = [
        {
            "model_info": {"id": "deployment-b", "account_pool_environment_id": "pool-card"},
            "litellm_params": {
                "api_base": "https://account-b.openai.azure.com/",
                "api_key": "key-b",
                "model": "azure/gpt-5.4",
            },
        }
    ]
    request_kwargs = {
        "input": [{"id": encoded_id, "type": "reasoning"}],
    }

    with pytest.raises(BadRequestError) as excinfo:
        await check.async_filter_deployments(
            model="gpt-5.4",
            healthy_deployments=healthy_only_b,
            messages=None,
            request_kwargs=request_kwargs,
        )

    assert "deployment-removed" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_affinity_does_not_raise_when_boundary_peer_available():
    """
    Even when the originating deployment is filtered out, if a peer on the
    same (api_base, api_key) is in healthy_deployments, the boundary-match
    path must succeed silently — no exception.
    """
    from unittest.mock import MagicMock

    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    originating = MagicMock()
    originating.litellm_params.model_dump.return_value = {
        "api_base": "https://account-a.openai.azure.com/",
        "api_key": "key-a",
    }
    mock_router = MagicMock()
    mock_router.get_deployment.return_value = originating

    check = EncryptedContentAffinityCheck(router=mock_router)
    encoded_id = ResponsesAPIRequestUtils._build_encrypted_item_id(
        "deployment-a", "rs_test"
    )
    peer = {
        "model_info": {"id": "deployment-a-peer"},
        "litellm_params": {
            "api_base": "https://account-a.openai.azure.com/",
            "api_key": "key-a",
            "model": "azure/gpt-5.4",
        },
    }
    request_kwargs = {
        "input": [{"id": encoded_id, "type": "reasoning"}],
    }

    result = await check.async_filter_deployments(
        model="gpt-5.4",
        healthy_deployments=[peer],
        messages=None,
        request_kwargs=request_kwargs,
    )

    assert result == [peer]
    assert request_kwargs.get("_encrypted_content_affinity_pinned") is True


@pytest.mark.asyncio
async def test_model_group_affinity_config_enables_encrypted_content_affinity():
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    model_group = "openai.gpt-5.1-codex"
    target_deployment = {
        "model_name": model_group,
        "litellm_params": {"model": "openai/gpt-5.1-codex"},
        "model_info": {"id": "deployment-b"},
    }
    healthy_deployments = [
        {
            "model_name": model_group,
            "litellm_params": {"model": "openai/gpt-5.1-codex"},
            "model_info": {"id": "deployment-a"},
        },
        target_deployment,
    ]
    encoded_id = ResponsesAPIRequestUtils._build_encrypted_item_id(
        "deployment-b", "rs_test"
    )
    request_kwargs = {
        "input": [{"type": "reasoning", "id": encoded_id}],
        "litellm_metadata": {},
    }
    check = EncryptedContentAffinityCheck(
        enable_global_affinity=False,
        model_group_affinity_config={
            model_group: ["encrypted_content_affinity"],
        },
    )

    filtered = await check.async_filter_deployments(
        model=model_group,
        healthy_deployments=healthy_deployments,
        messages=None,
        request_kwargs=request_kwargs,
    )

    assert filtered == [target_deployment]
    assert request_kwargs["litellm_metadata"]["encrypted_content_affinity_enabled"]
    assert request_kwargs.get("_encrypted_content_affinity_pinned") is True


@pytest.mark.asyncio
async def test_model_group_affinity_config_does_not_disable_global_encrypted_content_affinity():
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    model_group = "openai.gpt-5.1-codex"
    target_deployment = {
        "model_name": model_group,
        "litellm_params": {"model": "openai/gpt-5.1-codex"},
        "model_info": {"id": "deployment-b"},
    }
    healthy_deployments = [
        {
            "model_name": model_group,
            "litellm_params": {"model": "openai/gpt-5.1-codex"},
            "model_info": {"id": "deployment-a"},
        },
        target_deployment,
    ]
    encoded_id = ResponsesAPIRequestUtils._build_encrypted_item_id(
        "deployment-b", "rs_test"
    )
    request_kwargs = {
        "input": [{"type": "reasoning", "id": encoded_id}],
        "litellm_metadata": {},
    }
    check = EncryptedContentAffinityCheck(
        enable_global_affinity=True,
        model_group_affinity_config={
            model_group: ["deployment_affinity"],
        },
    )

    filtered = await check.async_filter_deployments(
        model=model_group,
        healthy_deployments=healthy_deployments,
        messages=None,
        request_kwargs=request_kwargs,
    )

    assert filtered == [target_deployment]
    assert request_kwargs["litellm_metadata"]["encrypted_content_affinity_enabled"]
    assert request_kwargs.get("_encrypted_content_affinity_pinned") is True


@pytest.mark.asyncio
async def test_model_group_encrypted_content_affinity_overrides_global_deployment_affinity():
    from litellm.router_utils.pre_call_checks.deployment_affinity_check import (
        DeploymentAffinityCheck,
    )
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentAffinityCheck,
    )

    model_group = "openai.gpt-5.1-codex"
    user_api_key_hash = "test-user-key"
    deployment_a = {
        "model_name": model_group,
        "litellm_params": {
            "model": "openai/gpt-5.1-codex",
            "api_key": "mock-api-key-a",
        },
        "model_info": {"id": "deployment-a"},
    }
    deployment_b = {
        "model_name": model_group,
        "litellm_params": {
            "model": "openai/gpt-5.1-codex",
            "api_key": "mock-api-key-b",
        },
        "model_info": {"id": "deployment-b"},
    }
    router = litellm.Router(
        model_list=[deployment_a, deployment_b],
        optional_pre_call_checks=["deployment_affinity"],
        model_group_affinity_config={
            model_group: ["encrypted_content_affinity"],
        },
        num_retries=0,
    )

    try:
        callbacks = router.optional_callbacks or []
        deployment_callback = next(
            cb for cb in callbacks if isinstance(cb, DeploymentAffinityCheck)
        )
        encrypted_content_callback = next(
            cb for cb in callbacks if isinstance(cb, EncryptedContentAffinityCheck)
        )
        assert callbacks.index(encrypted_content_callback) < callbacks.index(
            deployment_callback
        )
        assert encrypted_content_callback.enable_global_affinity is False

        cache_key = DeploymentAffinityCheck.get_affinity_cache_key(
            model_group=model_group,
            user_key=user_api_key_hash,
        )
        await deployment_callback.cache.async_set_cache(
            key=cache_key,
            value={"model_id": "deployment-a"},
            ttl=60,
        )
        encoded_id = ResponsesAPIRequestUtils._build_encrypted_item_id(
            "deployment-b", "rs_test"
        )
        request_kwargs = {
            "input": [
                {
                    "type": "reasoning",
                    "id": encoded_id,
                    "encrypted_content": "gAAAAABpnW_yEYmSNEyOG...",
                }
            ],
            "metadata": {"user_api_key_hash": user_api_key_hash},
            "litellm_metadata": {},
        }

        after_deployment_affinity = await deployment_callback.async_filter_deployments(
            model=model_group,
            healthy_deployments=[deployment_a, deployment_b],
            messages=None,
            request_kwargs=request_kwargs,
        )
        assert after_deployment_affinity == [deployment_a, deployment_b]

        after_encrypted_content_affinity = (
            await encrypted_content_callback.async_filter_deployments(
                model=model_group,
                healthy_deployments=after_deployment_affinity,
                messages=None,
                request_kwargs=request_kwargs,
            )
        )

        assert after_encrypted_content_affinity == [deployment_b]
        assert request_kwargs.get("_encrypted_content_affinity_pinned") is True
    finally:
        router.discard()

def _official_router():
    return Router(
        model_list=[
            {
                "model_name": "official",
                "model_info": {"id": name},
                "litellm_params": {"model": "openai/test-model", "api_base": base, "api_key": "test", "order": order},
            }
            for name, base, order in (
                ("a", "https://a.invalid/v1", 1),
                ("b", "https://b.invalid/v1", 2),
                ("peer", "https://a.invalid/v1", 2),
            )
        ],
        num_retries=0,
        optional_pre_call_checks=["encrypted_content_affinity"],
    )


def _official_item(origin, summary=True):
    from litellm.responses.utils import ResponsesAPIRequestUtils

    return {
        "type": "reasoning",
        "id": "rs_" + origin,
        "encrypted_content": ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id("opaque", origin),
        "summary": [{"type": "summary_text", "text": "Budget 5000"}] if summary else [],
    }


@pytest.mark.asyncio
async def test_official_no_peer_keeps_history_and_summary():
    router = _official_router()
    try:
        history = [{"role": "user", "content": "continue"}, _official_item("a")]
        result = await EncryptedContentAffinityCheck(router).async_filter_deployments(
            "official", [router.model_list[1]], None, {"input": history, "litellm_metadata": {}}
        )
        assert result == [router.model_list[1]]
        assert history == [
            {"role": "user", "content": "continue"},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Budget 5000"}]},
        ]
    finally:
        router.discard()


@pytest.mark.asyncio
async def test_official_pin_strips_only_foreign_reasoning():
    router = _official_router()
    try:
        history = [_official_item("a"), _official_item("b"), _official_item("peer")]
        original = copy.deepcopy(history)
        result = await EncryptedContentAffinityCheck(router).async_filter_deployments(
            "official", router.model_list, None, {"input": history, "litellm_metadata": {}}
        )
        assert result[0]["model_info"]["id"] == "a"
        assert history[0] == original[0] and history[2] == original[2]
        assert "encrypted_content" not in history[1] and "id" not in history[1]
        assert history[1]["summary"] == original[1]["summary"]
    finally:
        router.discard()


@pytest.mark.asyncio
async def test_official_target_order_beats_old_pin():
    router = _official_router()
    try:
        history = [_official_item("a")]
        result = await EncryptedContentAffinityCheck(router).async_filter_deployments(
            "official",
            router.model_list[:2],
            None,
            {"input": history, "litellm_metadata": {}, "_target_order": 2, "fallback_depth": 1},
        )
        assert [x["model_info"]["id"] for x in result] == ["b"]
        assert "encrypted_content" not in history[0]
    finally:
        router.discard()


@pytest.mark.parametrize("target,kept", [("b", False), ("peer", True)])
@pytest.mark.parametrize("surface", ["responses", "chat"])
def test_official_dispatch_handles_unmarked_fallback(target, kept, surface):
    router = _official_router()
    try:
        item = {
            "type": "reasoning",
            "encrypted_content": "unmarked",
            "summary": [{"type": "summary_text", "text": "Budget 5000"}],
        }
        metadata_key = "litellm_metadata" if surface == "responses" else "metadata"
        payload = {"fallback_depth": 1, "input": [item], metadata_key: {"model_info": {"id": "a"}}}
        if surface == "chat":
            payload["litellm_metadata"] = {"model_info": {"id": "b"}}
        deployment = next(x for x in router.model_list if x["model_info"]["id"] == target)
        router._update_kwargs_with_deployment(
            deployment,
            payload,
            function_name="_ageneric_api_call_with_fallbacks" if surface == "responses" else "acompletion",
        )
        assert ("encrypted_content" in payload["input"][0]) is kept
        assert payload["input"][0]["summary"] == item["summary"]
    finally:
        router.discard()


@pytest.mark.asyncio
async def test_official_anthropic_bridge_strips_foreign_only():
    router = _official_router()
    try:
        blocks = [
            {
                "type": "thinking",
                "thinking": "",
                "signature": "litellm_encrypted_reasoning:" + _official_item(origin)["encrypted_content"],
            }
            for origin in ("a", "b")
        ]
        blocks.append({"type": "text", "text": "Visible answer"})
        messages = [{"role": "assistant", "content": blocks}]
        result = await EncryptedContentAffinityCheck(router).async_filter_deployments(
            "official", router.model_list, messages, {"metadata": {}}
        )
        assert result[0]["model_info"]["id"] == "a"
        assert len(blocks) == 2 and blocks[-1]["text"] == "Visible answer"
    finally:
        router.discard()



@pytest.mark.parametrize("per_group", [False, True])
def test_official_runtime_disable_affinity_keeps_per_group_setting(per_group):
    router = _official_router()
    try:
        if per_group:
            router.model_group_affinity_config = {"official": ["encrypted_content_affinity"]}
        router.update_settings(optional_pre_call_checks=[])
        callbacks = [cb for cb in router.optional_callbacks if isinstance(cb, EncryptedContentAffinityCheck)]
        assert len(callbacks) == int(per_group)
        if callbacks:
            assert callbacks[0].enable_global_affinity is False
            assert callbacks[0]._is_enabled_for_model_group("official")
        router.update_settings(optional_pre_call_checks=["encrypted_content_affinity"])
        assert sum(isinstance(cb, EncryptedContentAffinityCheck) for cb in router.optional_callbacks) == 1
    finally:
        router.discard()


@pytest.mark.asyncio
@pytest.mark.parametrize("marked", [False, True])
async def test_official_real_order_fallback_keeps_visible_context(marked):
    from litellm.exceptions import ServiceUnavailableError

    router = _official_router()
    router.delete_deployment("peer")
    calls = []
    item = _official_item("a")
    if not marked:
        item["encrypted_content"] = "unmarked"
    history = [
        {"role": "user", "content": "Budget 5000"},
        item,
        {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "result"},
        {"role": "user", "content": "continue"},
    ]

    async def provider(**kwargs):
        selected = kwargs["litellm_metadata"]["model_info"]["id"]
        calls.append(selected)
        if selected == "a":
            raise ServiceUnavailableError(message="unavailable", llm_provider="openai", model="official")
        assert selected == "b"
        assert kwargs["input"][0] == history[0]
        assert kwargs["input"][2:] == history[2:]
        assert kwargs["input"][1]["summary"] == item["summary"]
        assert "encrypted_content" not in kwargs["input"][1]
        return _build_mock_response([{"type": "message", "role": "assistant", "content": "continued"}])

    try:
        result = await router._aresponses_with_streaming_fallbacks(
            original_function=provider, model="official", input=copy.deepcopy(history)
        )
        assert result.status == "completed"
        assert calls == ["a", "b"]
    finally:
        router.discard()


@pytest.mark.asyncio
async def test_official_unknown_origin_strips_without_changing_tools():
    router = _official_router()
    try:
        history = [
            _official_item("removed"),
            {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "result"},
        ]
        saved_tools = copy.deepcopy(history[1:])
        result = await EncryptedContentAffinityCheck(router).async_filter_deployments(
            "official", router.model_list, None, {"input": history}
        )
        assert result == router.model_list
        assert history[1:] == saved_tools
        assert "encrypted_content" not in history[0]
    finally:
        router.discard()


def test_official_runtime_toggle_does_not_remove_other_router_callback():
    first, second = _official_router(), _official_router()
    try:
        second_callback = next(cb for cb in second.optional_callbacks if isinstance(cb, EncryptedContentAffinityCheck))
        first.update_settings(optional_pre_call_checks=[])
        assert any(isinstance(cb, EncryptedContentAffinityCheck) for cb in litellm.callbacks)
        assert second_callback.enable_global_affinity
        second.update_settings(optional_pre_call_checks=[])
        assert not any(isinstance(cb, EncryptedContentAffinityCheck) for cb in litellm.callbacks)
    finally:
        first.discard()
        second.discard()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_boundary", [False, True])
@pytest.mark.parametrize("bridge", [False, True])
async def test_official_stream_fallback_remembers_source_boundary(same_boundary, bridge):
    from litellm.exceptions import MidStreamFallbackError, ServiceUnavailableError
    from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
    from litellm.types.llms.openai import ResponseCompletedEvent

    router = _official_router()
    router.delete_deployment("b" if same_boundary else "peer")
    target = "peer" if same_boundary else "b"
    attempts = []

    class Stream(BaseResponsesAPIStreamingIterator):
        def __init__(self, selected, metadata):
            self.selected = selected
            self.completed_response = None
            if not bridge:
                self.litellm_metadata = copy.deepcopy(metadata)
            self._hidden_params = {"model_id": selected}
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            if self.selected == "a":
                raise MidStreamFallbackError(
                    message="stream unavailable",
                    model="official",
                    llm_provider="openai",
                    original_exception=ServiceUnavailableError(
                        message="unavailable", model="official", llm_provider="openai"
                    ),
                    generated_content="",
                    is_pre_first_chunk=True,
                )
            return ResponseCompletedEvent(type="response.completed", response=_build_mock_response([]))

        async def aclose(self):
            pass

    async def provider(**kwargs):
        selected = kwargs["litellm_metadata"]["model_info"]["id"]
        attempts.append(selected)
        if selected == target:
            assert ("encrypted_content" in kwargs["input"][0]) is same_boundary
            assert kwargs["input"][0]["summary"][0]["text"] == "Budget 5000"
        return Stream(selected, kwargs["litellm_metadata"])

    try:
        stream = await router._aresponses_with_streaming_fallbacks(
            original_function=provider,
            model="official",
            stream=True,
            input=[
                {
                    "type": "reasoning",
                    "encrypted_content": "unmarked",
                    "summary": [{"type": "summary_text", "text": "Budget 5000"}],
                }
            ],
        )
        assert (await anext(stream)).type == "response.completed"
        assert attempts == ["a", target]
        await stream.aclose()
    finally:
        router.discard()
