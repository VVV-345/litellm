"""验证 LiteLLM 代理签发的短时 JWS，并绑定操作者、请求 ID 与授权动作。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from account_pool.models import FrozenModel

_ISSUER: Final = "litellm-proxy"
_AUDIENCE: Final = "account-pool"
_MAX_TOKEN_LENGTH: Final = 4096
_MAX_LIFETIME_SECONDS: Final = 60
_CLOCK_SKEW_SECONDS: Final = 5
_MIN_SECRET_BYTES: Final = 32

Clock = Callable[[], datetime]


class ActorAction(StrEnum):
    CHANNEL_CREATE = "channel:create"
    CHANNEL_UPDATE = "channel:update"
    CHANNEL_IMPORT = "channel:import"
    CHANNEL_DETACH = "channel:detach"
    CHANNEL_DELETE = "channel:delete"
    CHANNEL_DELETE_EXTERNAL_DEPLOYMENT = "channel:delete_external_deployment"
    CHANNEL_RECONCILE = "channel:reconcile"
    HEALTH_PROBE = "health:probe"
    PARSER_START = "parser_task:start"
    SNAPSHOT_IMPORT = "parser_snapshot:import"
    OVERRIDE_SET = "parser_override:set"
    OVERRIDE_REVOKE = "parser_override:revoke"
    ROUTING_POLICY_UPDATE = "routing_policy:update"
    ROUTING_CANDIDATE_UPDATE = "routing_candidate:update"
    ROUTING_ORDER_UPDATE = "routing_order:update"
    ROUTING_CANDIDATE_DELETE = "routing_candidate:delete"


class ActorVerificationFailureCode(StrEnum):
    CONFIGURATION = "configuration"
    MALFORMED = "malformed"
    INVALID_SIGNATURE = "invalid_signature"
    INVALID_CLAIMS = "invalid_claims"
    EXPIRED = "expired"
    REQUEST_MISMATCH = "request_mismatch"
    ACTION_MISMATCH = "action_mismatch"


class _JwsHeader(FrozenModel):
    alg: Literal["HS256"]
    typ: Literal["JWT"]


_HEADER: Final[TypeAdapter[_JwsHeader]] = TypeAdapter(_JwsHeader)


class _ActorClaims(FrozenModel):
    iss: Literal["litellm-proxy"]
    aud: Literal["account-pool"]
    sub: str = Field(min_length=1, max_length=255)
    role: Literal["proxy_admin"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    action: ActorAction
    iat: int = Field(ge=0)
    nbf: int = Field(ge=0)
    exp: int = Field(ge=0)
    jti: UUID

    @model_validator(mode="after")
    def validate_lifetime(self) -> _ActorClaims:
        if self.nbf < self.iat or self.exp <= self.nbf:
            raise ValueError("actor envelope timestamps are not ordered")
        if self.exp - self.iat > _MAX_LIFETIME_SECONDS:
            raise ValueError("actor envelope lifetime exceeds the maximum")
        return self


class ActorContext(FrozenModel):
    user_id: str
    role: Literal["proxy_admin", "system"]
    actor_type: Literal["user", "system"] = "user"
    request_id: str
    action: ActorAction
    envelope_id: UUID

    @model_validator(mode="after")
    def validate_actor_type_and_role(self) -> ActorContext:
        if (self.actor_type == "user") != (self.role == "proxy_admin"):
            raise ValueError("actor type and role do not match")
        return self


class ActorVerificationSuccess(FrozenModel):
    status: Literal["verified"] = "verified"
    actor: ActorContext


class ActorVerificationFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: ActorVerificationFailureCode


ActorVerificationResult = ActorVerificationSuccess | ActorVerificationFailure


def utc_now() -> datetime:
    return datetime.now(UTC)


def verify_actor_envelope(
    token: str | None,
    request_id: str | None,
    expected_action: ActorAction,
    secret: str | None,
    clock: Clock = utc_now,
) -> ActorVerificationResult:
    if secret is None or len(secret.encode("utf-8")) < _MIN_SECRET_BYTES:
        return ActorVerificationFailure(code=ActorVerificationFailureCode.CONFIGURATION)
    if token is None or request_id is None or len(token) > _MAX_TOKEN_LENGTH:
        return ActorVerificationFailure(code=ActorVerificationFailureCode.MALFORMED)
    segments: Final = tuple(token.split("."))
    if len(segments) != 3 or any(not segment for segment in segments):
        return ActorVerificationFailure(code=ActorVerificationFailureCode.MALFORMED)
    encoded_header, encoded_payload, encoded_signature = segments
    try:
        header: Final = _HEADER.validate_json(_decode_segment(encoded_header))
        if header.alg != "HS256" or header.typ != "JWT":
            return ActorVerificationFailure(code=ActorVerificationFailureCode.MALFORMED)
        provided_signature: Final = _decode_segment(encoded_signature)
    except (binascii.Error, UnicodeEncodeError, ValidationError, ValueError):
        return ActorVerificationFailure(code=ActorVerificationFailureCode.MALFORMED)
    signed: Final = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected_signature: Final = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_signature, expected_signature):
        return ActorVerificationFailure(code=ActorVerificationFailureCode.INVALID_SIGNATURE)
    # 必须先验签再解析身份声明，未经认证的载荷不能参与后续授权判断。
    try:
        claims: Final = _ActorClaims.model_validate_json(_decode_segment(encoded_payload))
    except (binascii.Error, UnicodeEncodeError, ValidationError, ValueError):
        return ActorVerificationFailure(code=ActorVerificationFailureCode.INVALID_CLAIMS)
    now: Final = int(clock().timestamp())
    if claims.iat > now + _CLOCK_SKEW_SECONDS or claims.nbf > now + _CLOCK_SKEW_SECONDS:
        return ActorVerificationFailure(code=ActorVerificationFailureCode.INVALID_CLAIMS)
    if claims.exp <= now - _CLOCK_SKEW_SECONDS:
        return ActorVerificationFailure(code=ActorVerificationFailureCode.EXPIRED)
    if claims.request_id != request_id:
        return ActorVerificationFailure(code=ActorVerificationFailureCode.REQUEST_MISMATCH)
    if claims.action != expected_action:
        return ActorVerificationFailure(code=ActorVerificationFailureCode.ACTION_MISMATCH)
    return ActorVerificationSuccess(
        actor=ActorContext(
            user_id=claims.sub,
            role=claims.role,
            request_id=claims.request_id,
            action=claims.action,
            envelope_id=claims.jti,
        )
    )


def _decode_segment(value: str) -> bytes:
    padding: Final = "=" * (-len(value) % 4)
    return base64.b64decode(f"{value}{padding}", altchars=b"-_", validate=True)
