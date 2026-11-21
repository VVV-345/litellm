"""只向已知 GLM 平台查询和显式兑换个人重置权益，不登录、不领取、不自动兑换。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import MappingProxyType
from typing import Final, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from account_pool.application.quota_state import routing_quota
from account_pool.channels.openai_compatible import HostResolver, resolve_host, validate_destination
from account_pool.domain import EnvironmentRecord, QuotaSnapshot, configured_proxy_url, utc_now
from account_pool.provider_families import GLM_CODING_PLAN_QUOTA_URLS
from account_pool.providers.usage.glm import parse_glm_coding_quota
from account_pool.quota_reset_models import ResetCredit, ResetObservation, ResetOperation, ResetReply, ResetVerification
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose, StateCipher

_ZONE: Final = timezone(timedelta(hours=8))
_MAX_BYTES: Final = 262_144


class _Inventory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    customer_id: int | str = Field(alias="customerId")
    target_type: Literal["PERSONAL"] = Field(alias="targetType")
    five_hour: tuple[JsonValue, ...] = Field(alias="fiveHourResets", max_length=1000)
    weekly: tuple[JsonValue, ...] = Field(alias="weekResets", max_length=1000)


class _InventoryReply(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    code: Literal[200]
    success: Literal[True]
    data: _Inventory


class _Card(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    record_id: int | str = Field(alias="recordId")
    grant_type: str = Field(alias="grantType", pattern=r"^[A-Z_]{1,32}$")
    expires: str = Field(alias="expireTime", max_length=64)
    available: bool


class _UsePayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    target_type: Literal["PERSONAL"] = Field(default="PERSONAL", alias="targetType")
    reset_type: Literal["FIVE_HOUR", "WEEK"] = Field(alias="resetType")
    record_id: int = Field(alias="recordId", gt=0)
    grant_type: str = Field(alias="grantType")
    request_id: str = Field(alias="requestId")


class _Prompt(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Literal["user"] = "user"
    content: str = "Reply only OK"


class _Thinking(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["disabled"] = "disabled"


class _ProbePayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: str
    messages: tuple[_Prompt, ...] = (_Prompt(),)
    max_tokens: int = 64
    stream: Literal[False] = False
    thinking: _Thinking = _Thinking()


class _UseReply(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    code: int
    success: bool
    data: JsonValue = None


def _number_id(value: int | str) -> str:
    text: Final = str(value)
    if not text.isascii() or not text.isdecimal() or text.startswith("0") or len(text) > 19 or int(text) >= 2**63:
        raise ValueError("供应商重置标识无效")
    return text


def _credit(raw: JsonValue, kind: Literal["FIVE_HOUR", "WEEK"], quota: QuotaSnapshot) -> ResetCredit | None:
    try:
        value: Final = _Card.model_validate(raw)
        record_id: Final = _number_id(value.record_id)
        parsed: Final = datetime.fromisoformat(value.expires.replace("Z", "+00:00"))
        expiry: Final = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=_ZONE)
        if not value.available or expiry <= utc_now():
            return None
        window: Final = next(
            (w for w in quota.windows if w.name == ("5 hour" if kind == "FIVE_HOUR" else "Weekly")), None
        )
        week_blocked: Final = kind == "FIVE_HOUR" and any(
            w.name == "Weekly" and w.remaining_percent <= 0 for w in quota.windows
        )
        return ResetCredit(
            id=record_id,
            reset_type=kind,
            grant_type=value.grant_type,
            expires_at=expiry,
            eligible=window is not None and window.used_percent > 0 and not week_blocked,
        )
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class GLMResetClient:
    http: httpx.AsyncClient
    secrets: EnvironmentSecretDeriver
    resolver: HostResolver = resolve_host

    def _origin(self, record: EnvironmentRecord) -> str:
        configuration: Final = record.openai_compatible
        if configuration is None or len(configuration.credentials) != 1:
            raise ValueError("需要单个 GLM Coding Plan Key")
        base: Final = str(configuration.base_url).rstrip("/")
        if base not in GLM_CODING_PLAN_QUOTA_URLS:
            raise ValueError("此接入站点不支持 GLM 官方额度重置")
        return base.split("/api/", 1)[0]

    async def _request(
        self,
        record: EnvironmentRecord,
        method: Literal["GET", "POST"],
        path: str,
        body: BaseModel | None = None,
    ) -> bytes:
        origin: Final = self._origin(record)
        configuration: Final = record.openai_compatible
        if configuration is None:
            raise ValueError("GLM 卡片配置缺失")
        key: Final = StateCipher(self.secrets).open(record.id, configuration.credentials[0].api_key_ciphertext)
        endpoint: Final = origin + path
        destination, _ = await validate_destination(endpoint, self.resolver)
        proxy: Final = configuration.credentials[0].proxy_url or configured_proxy_url(record) or None
        async with asyncio.timeout(30), AsyncExitStack() as stack:
            client: Final = (
                self.http
                if proxy is None
                else await stack.enter_async_context(
                    httpx.AsyncClient(proxy=proxy, timeout=25, trust_env=False, follow_redirects=False)
                )
            )
            async with client.stream(
                method,
                destination,
                content=body.model_dump_json(by_alias=True).encode() if body is not None else None,
                follow_redirects=False,
                headers=MappingProxyType(
                    {
                        "Authorization": key if origin == "https://open.bigmodel.cn" else "Bearer " + key,
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Content-Type": "application/json",
                    }
                ),
            ) as response:
                response.raise_for_status()
                content: Final = BytesIO()
                async for chunk in response.aiter_bytes():
                    if content.tell() + len(chunk) > _MAX_BYTES:
                        raise ValueError("供应商重置响应超过大小限制")
                    content.write(chunk)
                return content.getvalue()

    async def observe(self, record: EnvironmentRecord) -> ResetObservation:
        quota: Final = parse_glm_coding_quota(
            (await self._request(record, "GET", "/api/monitor/usage/quota/limit")).decode(), utc_now()
        )
        if (
            quota is None
            or quota.refresh_status != "complete"
            or not any(w.name in ("5 hour", "Weekly") for w in quota.windows)
        ):
            raise ValueError("未确认个人 Coding Plan，团队版不能仅凭推理 Key 兑换")
        inventory: Final = _InventoryReply.model_validate_json(
            await self._request(record, "GET", "/api/biz/customer-package-reset/list?targetType=PERSONAL")
        ).data
        customer: Final = _number_id(inventory.customer_id)
        groups: Final[tuple[tuple[Literal["FIVE_HOUR", "WEEK"], tuple[JsonValue, ...]], ...]] = (
            ("FIVE_HOUR", inventory.five_hour),
            ("WEEK", inventory.weekly),
        )
        parsed: Final = tuple(
            credit for kind, rows in groups for raw in rows if (credit := _credit(raw, kind, quota)) is not None
        )
        ids: Final = tuple(card.id for card in parsed)
        cards: Final = tuple(
            sorted((card for card in parsed if ids.count(card.id) == 1), key=lambda card: (card.expires_at, card.id))
        )
        eligible: Final = tuple(card for card in cards if card.eligible)
        chosen: Final = next(iter(eligible), None)
        binding: Final = f"glm-reset:{self._origin(record)}:PERSONAL:{customer}"
        scope: Final = hmac.new(
            self.secrets.derive(UUID(int=0), SecretPurpose.CREDENTIAL_IDENTITY).encode(),
            binding.encode(),
            hashlib.sha256,
        ).hexdigest()
        return ResetObservation(
            available=len(cards),
            credit_id=chosen.id if chosen else None,
            eligible=bool(eligible),
            quota=quota,
            cards=cards,
            provider_scope=scope,
        )

    async def consume(self, record: EnvironmentRecord, operation: ResetOperation) -> ResetReply:
        # 必须重读具体权益及账号；绝不省略 recordId，也不将旧确认替换为另一张权益。
        current: Final = await self.observe(record)
        if current.provider_scope != operation.before.provider_scope:
            raise ValueError("重置账号已变化")
        original: Final = next((c for c in operation.before.cards if c.id == operation.credit_id), None)
        selected: Final = next((c for c in current.cards if c.id == operation.credit_id), None)
        if original is None or selected is None:
            return ResetReply(code="no_credit")
        if selected != original:
            return ResetReply(code="nothing_to_reset")
        if not selected.eligible:
            return ResetReply(code="nothing_to_reset")
        payload: Final = _UsePayload(
            targetType="PERSONAL",
            resetType=selected.reset_type,
            recordId=int(selected.id),
            grantType=selected.grant_type,
            requestId=str(operation.request_id),
        )
        reply: Final = _UseReply.model_validate_json(
            await self._request(record, "POST", "/api/biz/customer-package-reset/use", payload)
        )
        if reply.code == 400 and not reply.success:
            return ResetReply(code="no_credit")
        if reply.code != 200 or not reply.success or reply.data != int(selected.id):
            raise ValueError("供应商未确认具体重置权益，结果待核对")
        return ResetReply(code="reset", windows_reset=1)

    async def verify(self, record: EnvironmentRecord, model: str, recover: bool) -> ResetVerification:
        current: Final = await self.observe(record)
        windows: Final = tuple(w for w in current.quota.windows if w.name != "MCP Monthly")
        missing_blocker: Final = any(
            w.remaining_percent <= 0 and not any(f.name == w.name for f in windows)
            for w in routing_quota(record).windows
        )
        if missing_blocker or not windows or any(w.remaining_percent <= 0 for w in windows):
            return ResetVerification(passed=False, restored=False, reason="quota_not_recovered")
        if record.openai_compatible is None or "/api/coding/paas/v4" not in str(record.openai_compatible.base_url):
            return ResetVerification(passed=False, restored=False, reason="coding_endpoint_required")
        body: Final = _ProbePayload(model=model)
        reply: Final = _ChatReply.model_validate_json(
            await self._request(record, "POST", "/api/coding/paas/v4/chat/completions", body)
        )
        passed: Final = any(choice.message.content and choice.message.content.strip() for choice in reply.choices)
        return ResetVerification(
            passed=passed,
            restored=passed and recover,
            reason="restored" if passed and recover else "passed" if passed else "model_check_failed",
        )


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message


class _ChatReply(BaseModel):
    choices: tuple[_Choice, ...]
