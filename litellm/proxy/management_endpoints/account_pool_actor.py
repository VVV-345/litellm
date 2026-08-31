"""为发往 Account Pool 的管理写请求签发短时、请求绑定的操作者 JWS。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID, uuid4

_ISSUER: Final = "litellm-proxy"
_AUDIENCE: Final = "account-pool"
_LIFETIME_SECONDS: Final = 30
_MIN_SECRET_BYTES: Final = 32
_REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

EpochClock = Callable[[], int]
EnvelopeIdFactory = Callable[[], UUID]
JsonScalar = str | int


class AccountPoolActorAction(StrEnum):
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


class ActorSigningFailureCode(StrEnum):
    CONFIGURATION = "configuration"
    IDENTITY_REQUIRED = "identity_required"
    INVALID_REQUEST_ID = "invalid_request_id"


@dataclass(frozen=True, slots=True)
class ActorEnvelope:
    token: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ActorSigningFailure:
    status: Literal["failed"]
    code: ActorSigningFailureCode


ActorSigningResult = ActorEnvelope | ActorSigningFailure


def epoch_now() -> int:
    return int(time.time())


def sign_actor_envelope(
    *,
    user_id: str | None,
    role: str | None,
    request_id: str,
    action: AccountPoolActorAction,
    secret: str | None,
    clock: EpochClock = epoch_now,
    envelope_id_factory: EnvelopeIdFactory = uuid4,
) -> ActorSigningResult:
    if secret is None or len(secret.encode("utf-8")) < _MIN_SECRET_BYTES:
        return ActorSigningFailure(status="failed", code=ActorSigningFailureCode.CONFIGURATION)
    if user_id is None or not user_id or role != "proxy_admin":
        return ActorSigningFailure(status="failed", code=ActorSigningFailureCode.IDENTITY_REQUIRED)
    if _REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        return ActorSigningFailure(status="failed", code=ActorSigningFailureCode.INVALID_REQUEST_ID)
    issued_at: Final = clock()
    header: Final[Mapping[str, JsonScalar]] = {"alg": "HS256", "typ": "JWT"}
    claims: Final[Mapping[str, JsonScalar]] = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": user_id,
        "role": role,
        "request_id": request_id,
        "action": action,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + _LIFETIME_SECONDS,
        "jti": str(envelope_id_factory()),
    }
    encoded_header: Final = _encode_json(header)
    encoded_claims: Final = _encode_json(claims)
    signed: Final = f"{encoded_header}.{encoded_claims}"
    signature: Final = hmac.new(secret.encode("utf-8"), signed.encode("ascii"), hashlib.sha256).digest()
    return ActorEnvelope(token=f"{signed}.{_encode_bytes(signature)}", request_id=request_id)


def _encode_json(value: Mapping[str, JsonScalar]) -> str:
    payload: Final = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return _encode_bytes(payload)


def _encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
