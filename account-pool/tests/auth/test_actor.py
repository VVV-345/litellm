"""验证内部 actor 信封的签名、时效、受众、请求绑定和动作绑定。"""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Final

from account_pool.auth.actor import (
    ActorAction,
    ActorVerificationFailure,
    ActorVerificationFailureCode,
    ActorVerificationSuccess,
    verify_actor_envelope,
)

_SECRET: Final = "actor-signing-secret-with-at-least-32-bytes"
_NOW: Final = datetime(2026, 8, 19, 20, 0, tzinfo=UTC)
_NOW_EPOCH: Final = int(_NOW.timestamp())
_INTEROP_EPOCH: Final = 1_776_801_600
_INTEROP_TOKEN: Final = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhY3Rpb24iOiJwYXJzZXJfb3ZlcnJpZGU6c2V0IiwiYXVkIjoiYWNjb3VudC1wb29sIiwiZXhwIjoxNzc2ODAxNjMwLCJpYXQiOjE3NzY4MDE2MDAsImlzcyI6ImxpdGVsbG0tcHJveHkiLCJqdGkiOiIzMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDMiLCJuYmYiOjE3NzY4MDE2MDAsInJlcXVlc3RfaWQiOiJyZXF1ZXN0LTEyMyIsInJvbGUiOiJwcm94eV9hZG1pbiIsInN1YiI6ImFkbWluLXVzZXIifQ."
    "IW4IFFxucocuhomwIyVWXe_OtVzpbuHEd1l4X8CuH2g"
)


def _claims(**updates: str | int) -> dict[str, str | int]:
    return {
        "iss": "litellm-proxy",
        "aud": "account-pool",
        "sub": "admin-user",
        "role": "proxy_admin",
        "request_id": "request-123",
        "action": "parser_override:set",
        "iat": _NOW_EPOCH,
        "nbf": _NOW_EPOCH,
        "exp": _NOW_EPOCH + 30,
        "jti": "30000000-0000-0000-0000-000000000003",
        **updates,
    }


def _encode_segment(value: object) -> str:
    payload: Final = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _token(claims: dict[str, str | int], secret: str = _SECRET) -> str:
    header: Final = _encode_segment({"alg": "HS256", "typ": "JWT"})
    payload: Final = _encode_segment(claims)
    signed: Final = f"{header}.{payload}"
    signature: Final = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), signed.encode("ascii"), hashlib.sha256).digest()
    ).rstrip(b"=")
    return f"{signed}.{signature.decode('ascii')}"


def _clock() -> datetime:
    return _NOW


def test_valid_envelope_returns_verified_actor() -> None:
    result: Final = verify_actor_envelope(
        token=_token(_claims()),
        request_id="request-123",
        expected_action=ActorAction.OVERRIDE_SET,
        secret=_SECRET,
        clock=_clock,
    )

    assert isinstance(result, ActorVerificationSuccess)
    assert result.actor.user_id == "admin-user"
    assert result.actor.request_id == "request-123"
    assert result.actor.action == ActorAction.OVERRIDE_SET


def test_litellm_signer_fixture_is_accepted() -> None:
    result: Final = verify_actor_envelope(
        token=_INTEROP_TOKEN,
        request_id="request-123",
        expected_action=ActorAction.OVERRIDE_SET,
        secret=_SECRET,
        clock=lambda: datetime.fromtimestamp(_INTEROP_EPOCH, tz=UTC),
    )

    assert isinstance(result, ActorVerificationSuccess)
    assert result.actor.user_id == "admin-user"


def test_tampered_and_malformed_envelopes_are_rejected() -> None:
    valid: Final = _token(_claims())
    header, _, signature = valid.split(".")
    tampered_payload: Final = _encode_segment(_claims(sub="other-user"))

    tampered: Final = verify_actor_envelope(
        token=f"{header}.{tampered_payload}.{signature}",
        request_id="request-123",
        expected_action=ActorAction.OVERRIDE_SET,
        secret=_SECRET,
        clock=_clock,
    )
    malformed: Final = verify_actor_envelope(
        token="not-a-jws",
        request_id="request-123",
        expected_action=ActorAction.OVERRIDE_SET,
        secret=_SECRET,
        clock=_clock,
    )

    assert isinstance(tampered, ActorVerificationFailure)
    assert tampered.code == ActorVerificationFailureCode.INVALID_SIGNATURE
    assert isinstance(malformed, ActorVerificationFailure)
    assert malformed.code == ActorVerificationFailureCode.MALFORMED


def test_expired_wrong_audience_and_future_tokens_are_rejected() -> None:
    expired: Final = verify_actor_envelope(
        token=_token(_claims(iat=_NOW_EPOCH - 40, nbf=_NOW_EPOCH - 40, exp=_NOW_EPOCH - 10)),
        request_id="request-123",
        expected_action=ActorAction.OVERRIDE_SET,
        secret=_SECRET,
        clock=_clock,
    )
    wrong_audience: Final = verify_actor_envelope(
        token=_token(_claims(aud="other-service")),
        request_id="request-123",
        expected_action=ActorAction.OVERRIDE_SET,
        secret=_SECRET,
        clock=_clock,
    )
    future: Final = verify_actor_envelope(
        token=_token(_claims(iat=_NOW_EPOCH + 10, nbf=_NOW_EPOCH + 10, exp=_NOW_EPOCH + 40)),
        request_id="request-123",
        expected_action=ActorAction.OVERRIDE_SET,
        secret=_SECRET,
        clock=_clock,
    )

    assert isinstance(expired, ActorVerificationFailure)
    assert expired.code == ActorVerificationFailureCode.EXPIRED
    assert isinstance(wrong_audience, ActorVerificationFailure)
    assert wrong_audience.code == ActorVerificationFailureCode.INVALID_CLAIMS
    assert isinstance(future, ActorVerificationFailure)
    assert future.code == ActorVerificationFailureCode.INVALID_CLAIMS


def test_channel_lifecycle_actions_match_proxy_contract() -> None:
    assert tuple(
        action.value
        for action in (
            ActorAction.CHANNEL_CREATE,
            ActorAction.CHANNEL_UPDATE,
            ActorAction.CHANNEL_IMPORT,
            ActorAction.CHANNEL_DETACH,
            ActorAction.CHANNEL_DELETE,
            ActorAction.CHANNEL_DELETE_EXTERNAL_DEPLOYMENT,
            ActorAction.CHANNEL_RECONCILE,
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


def test_request_action_and_configuration_are_bound() -> None:
    request_mismatch: Final = verify_actor_envelope(
        token=_token(_claims()),
        request_id="different-request",
        expected_action=ActorAction.OVERRIDE_SET,
        secret=_SECRET,
        clock=_clock,
    )
    action_mismatch: Final = verify_actor_envelope(
        token=_token(_claims()),
        request_id="request-123",
        expected_action=ActorAction.OVERRIDE_REVOKE,
        secret=_SECRET,
        clock=_clock,
    )
    configuration: Final = verify_actor_envelope(
        token=_token(_claims()),
        request_id="request-123",
        expected_action=ActorAction.OVERRIDE_SET,
        secret="too-short",
        clock=_clock,
    )

    assert isinstance(request_mismatch, ActorVerificationFailure)
    assert request_mismatch.code == ActorVerificationFailureCode.REQUEST_MISMATCH
    assert isinstance(action_mismatch, ActorVerificationFailure)
    assert action_mismatch.code == ActorVerificationFailureCode.ACTION_MISMATCH
    assert isinstance(configuration, ActorVerificationFailure)
    assert configuration.code == ActorVerificationFailureCode.CONFIGURATION
