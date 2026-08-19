"""验证 LiteLLM 为 Account Pool 写请求签发的 actor 信封内容与失败边界。"""

import base64
import hashlib
import hmac
from typing import Final, cast
from uuid import UUID

from pydantic import TypeAdapter

from litellm.proxy.management_endpoints.account_pool_actor import (
    AccountPoolActorAction,
    ActorEnvelope,
    ActorSigningFailure,
    ActorSigningFailureCode,
    sign_actor_envelope,
)

_SECRET: Final = "actor-signing-secret-with-at-least-32-bytes"
_JSON_OBJECT: Final = TypeAdapter(dict[str, object])
_INTEROP_TOKEN: Final = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhY3Rpb24iOiJwYXJzZXJfb3ZlcnJpZGU6c2V0IiwiYXVkIjoiYWNjb3VudC1wb29sIiwiZXhwIjoxNzc2ODAxNjMwLCJpYXQiOjE3NzY4MDE2MDAsImlzcyI6ImxpdGVsbG0tcHJveHkiLCJqdGkiOiIzMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDMiLCJuYmYiOjE3NzY4MDE2MDAsInJlcXVlc3RfaWQiOiJyZXF1ZXN0LTEyMyIsInJvbGUiOiJwcm94eV9hZG1pbiIsInN1YiI6ImFkbWluLXVzZXIifQ."
    "IW4IFFxucocuhomwIyVWXe_OtVzpbuHEd1l4X8CuH2g"
)


def _clock() -> int:
    return 1_776_801_600


def _envelope_id() -> UUID:
    return UUID("30000000-0000-0000-0000-000000000003")


def _decode_segment(value: str) -> bytes:
    return base64.urlsafe_b64decode(f"{value}{'=' * (-len(value) % 4)}")


def test_signer_emits_hs256_claims_bound_to_request_and_action() -> None:
    result: Final = sign_actor_envelope(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-123",
        action=AccountPoolActorAction.OVERRIDE_SET,
        secret=_SECRET,
        clock=_clock,
        envelope_id_factory=_envelope_id,
    )

    assert isinstance(result, ActorEnvelope)
    assert result.token == _INTEROP_TOKEN
    encoded_header, encoded_claims, encoded_signature = result.token.split(".")
    header: Final = _JSON_OBJECT.validate_json(_decode_segment(encoded_header))
    claims: Final = _JSON_OBJECT.validate_json(_decode_segment(encoded_claims))
    expected_signature: Final = hmac.new(
        _SECRET.encode(),
        f"{encoded_header}.{encoded_claims}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert claims["iss"] == "litellm-proxy"
    assert claims["aud"] == "account-pool"
    assert claims["sub"] == "admin-user"
    assert claims["role"] == "proxy_admin"
    assert claims["request_id"] == "request-123"
    assert claims["action"] == "parser_override:set"
    assert cast(int, claims["exp"]) - cast(int, claims["iat"]) == 30
    assert hmac.compare_digest(_decode_segment(encoded_signature), expected_signature)


def test_signer_requires_secret_identity_admin_role_and_valid_request_id() -> None:
    missing_secret: Final = sign_actor_envelope(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-123",
        action=AccountPoolActorAction.OVERRIDE_SET,
        secret=None,
    )
    missing_identity: Final = sign_actor_envelope(
        user_id=None,
        role="proxy_admin",
        request_id="request-123",
        action=AccountPoolActorAction.OVERRIDE_SET,
        secret=_SECRET,
    )
    wrong_role: Final = sign_actor_envelope(
        user_id="viewer-user",
        role="proxy_admin_viewer",
        request_id="request-123",
        action=AccountPoolActorAction.OVERRIDE_SET,
        secret=_SECRET,
    )
    invalid_request: Final = sign_actor_envelope(
        user_id="admin-user",
        role="proxy_admin",
        request_id="contains spaces",
        action=AccountPoolActorAction.OVERRIDE_SET,
        secret=_SECRET,
    )

    assert isinstance(missing_secret, ActorSigningFailure)
    assert missing_secret.code == ActorSigningFailureCode.CONFIGURATION
    assert isinstance(missing_identity, ActorSigningFailure)
    assert missing_identity.code == ActorSigningFailureCode.IDENTITY_REQUIRED
    assert isinstance(wrong_role, ActorSigningFailure)
    assert wrong_role.code == ActorSigningFailureCode.IDENTITY_REQUIRED
    assert isinstance(invalid_request, ActorSigningFailure)
    assert invalid_request.code == ActorSigningFailureCode.INVALID_REQUEST_ID


def test_channel_lifecycle_actions_match_account_pool_contract() -> None:
    assert tuple(
        action.value
        for action in (
            AccountPoolActorAction.CHANNEL_CREATE,
            AccountPoolActorAction.CHANNEL_UPDATE,
            AccountPoolActorAction.CHANNEL_IMPORT,
            AccountPoolActorAction.CHANNEL_DETACH,
            AccountPoolActorAction.CHANNEL_DELETE,
            AccountPoolActorAction.CHANNEL_DELETE_EXTERNAL_DEPLOYMENT,
            AccountPoolActorAction.CHANNEL_RECONCILE,
        )
    ) == (
        "channel:create",
        "channel:update",
        "channel:import",
        "channel:detach",
        "channel:delete",
        "channel:delete_external_deployment",
        "channel:reconcile",
    )


def test_parser_task_action_is_signed_without_exposing_provider_credentials() -> None:
    result: Final = sign_actor_envelope(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-parse",
        action=AccountPoolActorAction.PARSER_START,
        secret=_SECRET,
        clock=_clock,
        envelope_id_factory=_envelope_id,
    )

    assert isinstance(result, ActorEnvelope)
    _, encoded_claims, _ = result.token.split(".")
    claims: Final = _JSON_OBJECT.validate_json(_decode_segment(encoded_claims))
    assert claims["action"] == "parser_task:start"
    assert "api_key" not in claims
    assert "api_base" not in claims


def test_health_probe_action_is_signed_without_provider_credentials() -> None:
    result: Final = sign_actor_envelope(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-health-probe",
        action=AccountPoolActorAction.HEALTH_PROBE,
        secret=_SECRET,
        clock=_clock,
        envelope_id_factory=_envelope_id,
    )

    assert isinstance(result, ActorEnvelope)
    _, encoded_claims, _ = result.token.split(".")
    claims: Final = _JSON_OBJECT.validate_json(_decode_segment(encoded_claims))
    assert claims["action"] == "health:probe"
    assert "api_key" not in claims
    assert "deployment_id" not in claims
