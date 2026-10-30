"""编排批量文件和 OAuth 任务，仅在管理后台补充账号，不参与模型请求链路。"""

from __future__ import annotations

import asyncio
import logging
import secrets as random_secrets
from datetime import datetime, timedelta
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from account_pool.application.quota_state import routing_quota_state
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.credential_import import (
    expand_import_file,
    normalize_auth_file,
    parse_two_factor_text,
    validate_two_factor_entry,
)
from account_pool.credential_ownership import CredentialConflict, credential_identity
from account_pool.domain import (
    AuthorizationView,
    CreateEnvironmentRequest,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    ProxyMode,
    SupplierKind,
    UpdateEnvironmentRequest,
    to_view,
    utc_now,
)
from account_pool.onboarding_models import (
    OnboardingAction,
    OnboardingCleanup,
    OnboardingCleanupResult,
    OnboardingEntry,
    OnboardingImport,
    OnboardingImportResult,
    OnboardingItem,
    OnboardingPreview,
    OnboardingSecrets,
    OnboardingState,
    OnboardingTarget,
    OnboardingTargetView,
)
from account_pool.onboarding_repository import OnboardingRepository, StoredOnboarding
from account_pool.ports import EnvironmentRepository
from account_pool.service import EnvironmentService
from account_pool.shared.result import Failure, FailureCode, Result, Success
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose, StateCipher

_LOGGER: Final = logging.getLogger(__name__)
_ACTIVE: Final = frozenset({"queued", "running", "awaiting_authorization"})
_COMPLETED: Final = frozenset({"ready", "cooling_down", "disabled"})
_SUPPLIER_NAMES: Final = {
    "openai_codex": "Codex",
    "anthropic_claude": "Claude",
    "google_antigravity": "Antigravity",
    "kimi": "Kimi",
    "xai": "xAI",
}


class OnboardingPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    entry: OnboardingEntry
    mailbox_ready: bool = False
    proposed_password: str | None = None
    proxy_profile_id: str | None = None


def file_identity(entry: OnboardingEntry, supplier: str, secrets: EnvironmentSecretDeriver) -> tuple[str, ...]:
    if not entry.label.lower().endswith(".json") or any(char in entry.label for char in ("/", "\\", "\x00")):
        raise CredentialConflict("请选择文件名不含路径的 JSON 认证文件")
    if len(entry.content.encode()) > 1024 * 1024:
        raise CredentialConflict("单个文件不得超过 1 MiB")
    try:
        content: Final = normalize_auth_file(entry.content.encode(), supplier)
        values: Final = TypeAdapter(dict[str, JsonValue]).validate_json(content)
    except ValueError:
        raise CredentialConflict("认证文件必须是 JSON 对象") from None
    definition: Final = SupplierRegistry.default().get(SupplierKind(supplier))
    declared: Final = values.get("type") or values.get("provider")
    if declared not in (definition.auth_file_provider_key, supplier):
        raise CredentialConflict("文件供应商与所选供应商不一致，或缺少 type/provider 字段")
    return credential_identity(content, supplier, secrets).fingerprints


def import_entries(request: OnboardingImport) -> tuple[OnboardingEntry, ...]:
    def expand(entry: OnboardingEntry) -> tuple[OnboardingEntry, ...]:
        if not entry.content or request.source != "auth_file" or len(entry.content.encode()) > 1024 * 1024:
            return (entry,)
        try:
            return expand_import_file(entry, request.supplier)
        except ValueError:
            return (entry,)

    return tuple(item for entry in request.entries for item in expand(entry))


def target_view(target: OnboardingTarget, items: tuple[OnboardingItem, ...]) -> OnboardingTargetView:
    selected: Final = tuple(item for item in items if item.source == "oauth" and item.supplier == target.supplier)
    ready: Final = sum(item.state == "ready" and (not target.model or target.model in item.models) for item in selected)
    pending: Final = sum(item.state in _ACTIVE for item in selected)
    return OnboardingTargetView(
        **target.model_dump(),
        ready=ready,
        in_progress=pending,
        standby=sum(item.state == "standby" for item in selected),
        awaiting_mailbox=sum(item.state == "awaiting_mailbox" for item in selected),
        shortage=max(0, target.count - ready - pending),
    )


def completion_time(item: OnboardingItem) -> datetime | None:
    return item.completed_at or (item.updated_at if item.card_name is not None and item.state in _COMPLETED else None)


def usable_models(record: EnvironmentRecord) -> tuple[str, ...]:
    now: Final = utc_now()
    remaining, _ = routing_quota_state(record.quota, now)
    if remaining is not None and remaining <= 0:
        return ()
    blocked: Final = frozenset(item.model for item in record.model_cooldowns if item.retry_at > now) | frozenset(
        item.model
        for item in record.model_quotas
        for value, _ in (routing_quota_state(item.quota, now),)
        if value is not None and value <= 0
    )
    return tuple(model for model in record.enabled_models if model not in blocked)


class OnboardingService:
    def __init__(
        self,
        repository: OnboardingRepository,
        environments: EnvironmentRepository,
        service: EnvironmentService,
        secrets: EnvironmentSecretDeriver,
    ) -> None:
        self.repository: Final = repository
        self._environments: Final = environments
        self._service: Final = service
        self._secrets: Final = secrets
        self._cipher: Final = StateCipher(secrets)

    def _payload(self, row: StoredOnboarding) -> OnboardingPayload:
        return OnboardingPayload.model_validate_json(self._cipher.open(row.item.id, row.ciphertext))

    def _fingerprints(self, request: OnboardingImport, entry: OnboardingEntry) -> tuple[str, ...]:
        if entry.totp_secret:
            validate_two_factor_entry(entry, request.supplier)
            if request.proxy_profile_id is None or entry.content:
                raise CredentialConflict("2FA 登录资料必须选择 OAuth 出站代理，且不能混入认证 Token")
        elif request.source == "auth_file":
            return file_identity(entry, request.supplier, self._secrets)
        elif entry.content or not entry.mailbox_password:
            raise CredentialConflict("OAuth 账号需要邮箱密码，不接受认证 JSON 内容")
        if entry.label.count("@") != 1 or any(char.isspace() for char in entry.label):
            raise CredentialConflict("邮箱格式不正确")
        identity: Final = uuid5(NAMESPACE_URL, f"onboarding:{request.supplier}:{entry.label.casefold()}")
        return ("mailbox:" + self._secrets.derive(identity, SecretPurpose.CREDENTIAL_IDENTITY),)

    async def preview(self, request: OnboardingImport) -> tuple[OnboardingPreview, ...]:
        prepared: Final = request.model_copy(update={"entries": import_entries(request)})
        return await self._preview(prepared)

    async def _preview(self, request: OnboardingImport) -> tuple[OnboardingPreview, ...]:
        if (
            len(request.entries) > 100
            or sum(len(entry.content.encode()) for entry in request.entries) > 8 * 1024 * 1024
        ):
            return (OnboardingPreview(index=0, label="本批次", status="invalid", message="每批最多 100 个账号、8 MiB"),)
        rows: Final = await self.repository.list()
        records: Final = await self._environments.list()
        known: Final = {key for row in rows for key in row.fingerprints} | {
            key for record in records for key in record.credential_fingerprints
        }

        def inspect(index: int, entry: OnboardingEntry) -> tuple[OnboardingPreview, frozenset[str]]:
            try:
                fingerprints: Final = frozenset(self._fingerprints(request, entry))
                return OnboardingPreview(
                    index=index,
                    label=entry.label,
                    status="valid",
                    message="已识别 2FA 登录资料，导入后需完成 OAuth 授权"
                    if entry.totp_secret
                    else "校验通过，提交后验证实际可用性",
                ), fingerprints
            except CredentialConflict as error:
                return OnboardingPreview(
                    index=index, label=entry.label, status="invalid", message=str(error)
                ), frozenset()

        inspected: Final = tuple(inspect(index, entry) for index, entry in enumerate(request.entries))
        return tuple(
            result.model_copy(update={"status": "duplicate", "message": "账号或凭证已存在，请查看原任务或卡片"})
            if result.status == "valid"
            and fingerprints.intersection(known.union(*(previous_keys for _, previous_keys in inspected[:index])))
            else result
            for index, (result, fingerprints) in enumerate(inspected)
        )

    async def submit(self, request: OnboardingImport) -> OnboardingImportResult:
        prepared: Final = request.model_copy(update={"entries": import_entries(request)})
        return await self._submit(prepared)

    async def _submit(self, request: OnboardingImport) -> OnboardingImportResult:
        preview: Final = await self._preview(request)
        for result in preview:
            if result.status != "valid":
                continue
            entry: Final = request.entries[result.index]
            item_id: Final = uuid5(request.job_id, str(result.index))
            now: Final = utc_now()
            state: Final = (
                "queued"
                if request.source == "auth_file" or entry.totp_secret
                else "awaiting_mailbox"
                if request.prepare_mailbox
                else "standby"
            )
            item: Final = OnboardingItem(
                id=item_id,
                job_id=request.job_id,
                source="oauth" if entry.totp_secret else request.source,
                supplier=request.supplier,
                mailbox="mail" if entry.totp_secret else request.mailbox if request.source == "oauth" else None,
                label=entry.label,
                state=state,
                message="等待后台处理" if state == "queued" else "等待邮箱准备或启动授权",
                card_id=uuid5(item_id, "card"),
                created_at=now,
                updated_at=now,
            )
            payload: Final = OnboardingPayload(
                entry=entry,
                mailbox_ready=bool(entry.totp_secret) or not request.prepare_mailbox,
                proxy_profile_id=request.proxy_profile_id,
            )
            await self.repository.insert(
                StoredOnboarding(
                    item=item,
                    ciphertext=self._cipher.seal(item_id, payload.model_dump_json()),
                    fingerprints=self._fingerprints(request, entry),
                )
            )
        return OnboardingImportResult(
            preview=preview,
            items=tuple(
                row.item
                for row in await self.repository.list()
                if row.item.job_id == request.job_id and not row.history_cleared
            ),
        )

    async def list(self) -> tuple[OnboardingItem, ...]:
        return tuple(row.item for row in await self.repository.list() if not row.history_cleared)

    async def cleanup(self, request: OnboardingCleanup) -> Result[OnboardingCleanupResult]:
        async with self.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "后台正在处理卡片，请稍后重试")
            eligible: Final = tuple(
                row
                for row in await self.repository.list()
                if not row.history_cleared
                and not row.paused
                and row.item.state in _COMPLETED
                and (request.source is None or row.item.source == request.source)
                and (completed_at := completion_time(row.item)) is not None
                and (request.completed_before is None or completed_at < request.completed_before)
            )
            for row in eligible:
                # 隐藏完成历史并丢弃原始认证文件，保留 OAuth 账号资料、卡片关联与去重指纹。
                await self.repository.save(
                    row.model_copy(
                        update={
                            "item": row.item.model_copy(update={"completed_at": completion_time(row.item)}),
                            "history_cleared": True,
                            "ciphertext": row.ciphertext if row.item.source == "oauth" else "",
                        }
                    )
                )
            return Success(OnboardingCleanupResult(cleared=len(eligible)))

    async def reveal(self, item_id: UUID) -> Result[OnboardingSecrets]:
        row: Final = await self.repository.get(item_id)
        if row is None or row.history_cleared or row.item.source != "oauth":
            return Failure(FailureCode.NOT_FOUND, "未找到 OAuth 账号")
        payload: Final = self._payload(row)
        return Success(
            OnboardingSecrets(
                mailbox_password=payload.entry.mailbox_password,
                supplier_password=payload.entry.supplier_password,
                proposed_password=payload.proposed_password,
                totp_secret=payload.entry.totp_secret,
            )
        )

    async def action(self, item_id: UUID, request: OnboardingAction) -> Result[OnboardingItem]:
        async with self.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "后台正在处理卡片，请稍后重试")
            row: Final = await self.repository.get(item_id)
            if row is None or row.history_cleared:
                return Failure(FailureCode.NOT_FOUND, "任务不存在")
            if request.action == "pause":
                return Success(
                    await self._save(
                        row.model_copy(update={"paused": True}), "disabled", "已暂停此任务，已有卡片请在仪表盘管理"
                    )
                )
            if request.action in ("mailbox_ready", "generate_password"):
                if self._payload(row).entry.totp_secret:
                    return Failure(FailureCode.CONFLICT, "2FA 文件是供应商登录资料，不用于修改邮箱密码")
                if row.item.source != "oauth" or row.item.state not in (
                    "awaiting_mailbox",
                    "standby",
                    "disabled",
                    "failed",
                ):
                    return Failure(FailureCode.CONFLICT, "当前任务不在邮箱准备阶段")
                payload: Final = self._payload(row)
                if request.action == "generate_password":
                    proposed: Final = "Aa9!" + random_secrets.token_urlsafe(20)
                    generated: Final = payload.model_copy(update={"proposed_password": proposed})
                    await self.repository.save(
                        row.model_copy(
                            update={
                                "ciphertext": self._cipher.seal(item_id, generated.model_dump_json()),
                            }
                        )
                    )
                    return Success(row.item)
                password: Final = (
                    request.mailbox_password or payload.proposed_password or payload.entry.mailbox_password
                )
                prepared: Final = payload.model_copy(
                    update={
                        "entry": payload.entry.model_copy(update={"mailbox_password": password}),
                        "mailbox_ready": True,
                        "proposed_password": None,
                    }
                )
                updated: Final = row.model_copy(
                    update={"ciphertext": self._cipher.seal(item_id, prepared.model_dump_json()), "paused": False}
                )
                return Success(await self._save(updated, "standby", "邮箱已由管理员确认，等待启动授权"))
            if row.item.state not in ("standby", "failed", "disabled", "awaiting_mailbox"):
                return Failure(FailureCode.CONFLICT, "任务已经在处理或已完成")
            if row.item.source == "oauth" and not self._payload(row).mailbox_ready:
                return Failure(FailureCode.CONFLICT, "请先完成邮箱准备并确认")
            return Success(
                await self._save(row.model_copy(update={"paused": False}), "queued", "已排队，复用原任务和卡片")
            )

    async def _save(self, row: StoredOnboarding, state: OnboardingState, message: str) -> OnboardingItem:
        now: Final = utc_now()
        item: Final = row.item.model_copy(
            update={
                "state": state,
                "message": message,
                "updated_at": now,
                "completed_at": completion_time(row.item) or (now if state == "ready" else None),
            }
        )
        await self.repository.save(row.model_copy(update={"item": item}))
        return item

    async def import_login_file(self, card_id: UUID, content: str) -> Result[EnvironmentView]:
        async with self.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "后台正在处理卡片，请稍后重试")
            record: Final = await self._environments.get(card_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "卡片不存在")
            if record.auth_file_name or record.credential_fingerprints:
                return Failure(FailureCode.CONFLICT, "2FA 登录资料不能直接替换有效凭据，请使用空卡片或批量上号")
            if not record.enabled or record.manual_cooldown or record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.CONFLICT, "卡片已停用、冷却或删除")
            if record.proxy_mode is not ProxyMode.PROFILE or not record.proxy_profile_id:
                return Failure(FailureCode.CONFLICT, "请先为卡片选择 OAuth 出站代理")
            try:
                entries: Final = parse_two_factor_text(content, record.supplier.value)
            except CredentialConflict as error:
                return Failure(FailureCode.INVALID, str(error))
            if len(entries) != 1:
                return Failure(FailureCode.INVALID, "单张卡片只能导入一个账号，多行文本请使用批量上号")
            request: Final = OnboardingImport(
                job_id=uuid5(card_id, "2fa-import"),
                source="oauth",
                supplier=record.supplier.value,
                proxy_profile_id=record.proxy_profile_id,
                prepare_mailbox=False,
                entries=entries,
            )
            keys: Final = self._fingerprints(request, entries[0])
            existing: Final = tuple(row for row in await self.repository.list() if row.item.card_id == card_id)
            if existing:
                if existing[0].fingerprints != keys or self._payload(existing[0]).entry != entries[0]:
                    return Failure(FailureCode.CONFLICT, "该卡片已有不同的上号资料，请在原任务中继续")
                return Success(to_view(record))
            checked: Final = await self._preview(request)
            if checked[0].status != "valid":
                return Failure(FailureCode.CONFLICT, checked[0].message)
            now: Final = utc_now()
            item_id: Final = uuid5(request.job_id, "0")
            item: Final = OnboardingItem(
                id=item_id,
                job_id=request.job_id,
                source="oauth",
                supplier=request.supplier,
                mailbox="mail",
                label=entries[0].label,
                state="queued",
                message="登录资料已保存，等待 OAuth 授权",
                card_id=card_id,
                created_at=now,
                updated_at=now,
            )
            payload: Final = OnboardingPayload(
                entry=entries[0], mailbox_ready=True, proxy_profile_id=record.proxy_profile_id
            )
            row: Final = StoredOnboarding(
                item=item, ciphertext=self._cipher.seal(item_id, payload.model_dump_json()), fingerprints=keys
            )
            if not await self.repository.insert(row):
                return Failure(FailureCode.CONFLICT, "账号已有上号任务，请在原任务中继续")
            authorization: Final = await self._service.authorize_environment(
                card_id, operation_id=f"onboarding:{item_id}", require_empty=True
            )
            if isinstance(authorization, Failure):
                await self._save(row, "failed", "授权启动失败，登录资料已保存，请检查原卡片后重试")
                return authorization
            await self._save(row, "awaiting_authorization", "登录资料已保存，请完成登录和二次验证")
            return Success(authorization.value.environment)

    async def targets(self) -> tuple[OnboardingTargetView, ...]:
        items: Final = tuple(row.item for row in await self.repository.list())
        return tuple(target_view(target, items) for target in await self.repository.targets())

    async def authorization(self, item_id: UUID) -> Result[AuthorizationView]:
        row: Final = await self.repository.get(item_id)
        if row is None or row.item.source != "oauth" or row.item.state != "awaiting_authorization":
            return Failure(FailureCode.CONFLICT, "任务暂无待完成授权")
        return await self._service.pending_authorization(row.item.card_id)

    async def _process(self, row: StoredOnboarding) -> None:
        payload: Final = self._payload(row)
        request: Final = CreateEnvironmentRequest(
            name=f"{_SUPPLIER_NAMES[row.item.supplier]}-套餐待识别-{row.item.label}"[:80],
            supplier=SupplierKind(row.item.supplier),
            proxy_profile_id=payload.proxy_profile_id,
            operation_id=f"onboarding:{row.item.id}",
        )
        running: Final = row.model_copy(
            update={"item": row.item.model_copy(update={"attempts": row.item.attempts + 1})}
        )
        await self._save(running, "running", "创建或恢复卡片，验证凭证与模型")
        if row.item.source == "auth_file":
            result: Final = await self._service.create_auth_file_environment(
                request,
                row.item.card_id,
                payload.entry.label,
                payload.entry.content.encode(),
            )
            if isinstance(result, Failure):
                await self._save(running, "failed", "凭证导入或验证失败，请检查原卡片后重试")
                return
            await self._observe(running)
            return
        existing: Final = await self._environments.get(row.item.card_id)
        if existing is not None and (
            not existing.enabled or existing.manual_cooldown or existing.status is EnvironmentStatus.DELETING
        ):
            await self._save(running, "disabled", "原卡片已手动停用、冷却或删除，自动化不会覆盖")
            return
        if (
            existing is not None
            and existing.auth_file_name is not None
            and existing.status is not EnvironmentStatus.ERROR
        ):
            await self._observe(running)
            return
        pending: Final = await self._service.pending_authorization(row.item.card_id)
        if isinstance(pending, Success):
            await self._save(running, "awaiting_authorization", "请打开授权并完成登录，验证通过后自动完成")
            return
        result_oauth: Final = (
            await self._service.create_environment(request, environment_id=row.item.card_id)
            if existing is None
            else await self._service.authorize_environment(row.item.card_id, require_empty=True)
            if payload.entry.totp_secret
            else await self._service.resume_onboarding_oauth(row.item.card_id)
        )
        await self._save(
            running,
            "failed" if isinstance(result_oauth, Failure) else "awaiting_authorization",
            "授权启动失败，请检查卡片并手动重试"
            if isinstance(result_oauth, Failure)
            else "请完成登录；验证码或二次验证需人工接续",
        )

    async def _observe(self, row: StoredOnboarding) -> None:
        result: Final = await self._service.get_environment(row.item.card_id)
        record: Final = await self._environments.get(row.item.card_id)
        if record is None:
            await self._save(row, "failed", "卡片已不存在，请检查；不会自动重复创建")
            return
        if (
            not record.enabled
            or record.manual_cooldown
            or record.status in (EnvironmentStatus.DISABLED, EnvironmentStatus.DELETING)
        ):
            await self._save(row, "disabled", "卡片已手动停用或冷却，自动化已暂停")
            return
        if (
            record.status is EnvironmentStatus.READY
            and not record.configuration_pending
            and isinstance(result, Success)
        ):
            gateway: Final = self._service.gateway_environment(record)
            if not gateway.routable:
                await self._save(row, "cooling_down", "当前卡片不可路由，等待后台配额或健康恢复")
                return
            if not usable_models(record):
                await self._save(row, "cooling_down", "当前模型额度耗尽或处于冷却，等待恢复")
                return
            await self._complete(row, record)
            return
        if record.status is EnvironmentStatus.COOLING_DOWN or record.automatic_cooldown:
            await self._save(row, "cooling_down", "额度或健康冷却中，恢复验证通过后重新计入")
            return
        if record.status is EnvironmentStatus.ERROR:
            await self._save(row, "failed", "卡片验证失败或授权失效，请检查卡片后重试")
            return
        if (
            record.oauth_expires_at is not None
            and record.oauth_expires_at <= utc_now()
            and record.auth_file_name is None
        ):
            await self._save(row, "failed", "授权已过期，请手动重试")
            return
        if row.item.source == "auth_file":
            await self._save(row, "failed", "凭证已保存，卡片配置或模型仍未就绪，请稍后重试原任务")
            return
        await self._save(row, "awaiting_authorization", "等待授权完成和模型验证")

    async def _complete(self, row: StoredOnboarding, record: EnvironmentRecord) -> None:
        if (
            row.item.source == "oauth"
            and record.credential_email
            and record.credential_email.casefold() != row.item.label.casefold()
        ):
            disabled: Final = await self._service.update_environment(
                record.id,
                UpdateEnvironmentRequest(
                    version=record.version,
                    name=record.name,
                    concurrency_limit=record.concurrency_limit,
                    enabled=False,
                    manual_cooldown=record.manual_cooldown,
                    proxy_mode=record.proxy_mode,
                    proxy_profile_id=record.proxy_profile_id,
                    enabled_models=record.enabled_models,
                ),
            )
            if isinstance(disabled, Success):
                await self._save(row, "failed", "实际授权账号与导入邮箱不一致，卡片已停用，请检查登录账号")
            return
        account: Final = record.credential_email or record.credential_account_id or row.item.label.removesuffix(".json")
        name: Final = f"{_SUPPLIER_NAMES[row.item.supplier]}-{record.quota.plan_type or '套餐待识别'}-{account}"[:80]
        if row.item.card_name is None and record.name != name:
            renamed: Final = await self._service.update_environment(
                record.id,
                UpdateEnvironmentRequest(
                    version=record.version,
                    name=name,
                    concurrency_limit=record.concurrency_limit,
                    enabled=record.enabled,
                    manual_cooldown=record.manual_cooldown,
                    proxy_mode=record.proxy_mode,
                    proxy_profile_id=record.proxy_profile_id,
                    enabled_models=record.enabled_models,
                ),
            )
            if isinstance(renamed, Failure):
                await self._save(row, "failed", "凭证已验证，但卡片命名或配置尚未完成，请重试原任务")
                return
        completed: Final = row.model_copy(
            update={
                "item": row.item.model_copy(
                    update={
                        "card_name": name if row.item.card_name is None else record.name,
                        "models": usable_models(record),
                    }
                )
            }
        )
        await self._save(completed, "ready", "凭证、模型与网关配置验证通过")

    async def run_once(self) -> None:
        async with self.repository.worker_lock() as acquired:
            if not acquired:
                return
            rows: Final = await self.repository.list()
            for row in rows:
                if row.item.source == "auth_file" and row.item.state == "ready":
                    continue
                if row.item.state in ("awaiting_authorization", "ready", "cooling_down"):
                    if row.item.updated_at < utc_now() - timedelta(seconds=30):
                        try:
                            await self._observe(row)
                        except Exception:
                            _LOGGER.warning("Onboarding card refresh failed: %s", row.item.card_id)
            current: Final = await self.repository.list()
            for target in await self.repository.targets():
                view: Final = target_view(target, tuple(row.item for row in current))
                if not target.enabled or view.shortage == 0:
                    continue
                standby: Final = tuple(
                    row
                    for row in current
                    if row.item.source == "oauth"
                    and row.item.supplier == target.supplier
                    and row.item.state == "standby"
                )
                for row in standby[: view.shortage]:
                    await self._save(row, "queued", "可用数量不足，已排队等待授权")
            pending: Final = tuple(
                row for row in await self.repository.list() if row.item.state in ("queued", "running")
            )
            if pending:
                if pending[0].item.state == "running" and pending[0].item.attempts >= 3:
                    await self._save(pending[0], "failed", "任务多次中断，已暂停自动恢复，请检查后手动重试")
                    return
                try:
                    await self._process(pending[0])
                except Exception:
                    latest: Final = await self.repository.get(pending[0].item.id)
                    await self._save(latest or pending[0], "failed", "后台任务未完成，检查卡片后可重试")

    async def run_until_cancelled(self, stopped: asyncio.Event) -> None:
        while not stopped.is_set():
            try:
                await self.run_once()
            except Exception as error:
                _LOGGER.warning("Onboarding worker failed: %s", type(error).__name__)
            try:
                await asyncio.wait_for(stopped.wait(), timeout=5)
            except TimeoutError:
                pass
