"""编排批量文件和 OAuth 任务，仅在管理后台补充账号，不参与模型请求链路。"""

from __future__ import annotations

import asyncio
import logging
import secrets as random_secrets
from collections.abc import Awaitable, Callable
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
from account_pool.oauth_automation.models import ACTIVE as AUTOMATION_ACTIVE
from account_pool.oauth_automation.models import AutomationDraft, JobInput, JobView
from account_pool.oauth_automation.service import AutomationRegistry
from account_pool.onboarding.models import (
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
    onboarding_card_name,
)
from account_pool.onboarding.repository import OnboardingRepository, StoredOnboarding
from account_pool.ports import EnvironmentRepository
from account_pool.service import EnvironmentService
from account_pool.shared.result import Failure, FailureCode, Result, Success
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose, StateCipher

_LOGGER: Final = logging.getLogger(__name__)
_ACTIVE: Final = frozenset({"queued", "running", "awaiting_authorization"})
_COMPLETED: Final = frozenset({"ready", "cooling_down", "disabled"})


class OnboardingPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    entry: OnboardingEntry
    mailbox_ready: bool = False
    proposed_password: str | None = None
    proxy_profile_id: str | None = None
    automation_job_id: UUID | None = None
    manual_oauth_only: bool = False


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


def import_entries(request: OnboardingImport) -> tuple[tuple[OnboardingEntry, ...], tuple[str | None, ...]]:
    def expand(entry: OnboardingEntry) -> tuple[tuple[OnboardingEntry, str | None], ...]:
        if not entry.content or request.source != "auth_file" or len(entry.content.encode()) > 1024 * 1024:
            return ((entry, None),)
        try:
            return tuple(
                (item.model_copy(update={"proxy_profile_id": entry.proxy_profile_id}), None)
                for item in expand_import_file(entry, request.supplier, request.auth_file_format)
            )
        except CredentialConflict as error:
            return ((entry, str(error)),)
        except ValueError:
            return ((entry, "认证文件格式无效，请检查账号字段"),)

    expanded: Final = tuple(item for entry in request.entries for item in expand(entry))
    return tuple(entry for entry, _ in expanded), tuple(error for _, error in expanded)


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
        automation: AutomationRegistry | None = None,
        *,
        retry_automation_job: Callable[[UUID], Awaitable[Result[JobView]]] | None = None,
    ) -> None:
        self.repository: Final = repository
        self._environments: Final = environments
        self._service: Final = service
        self._secrets: Final = secrets
        self._automation: Final = automation
        self._retry_automation_job: Final = retry_automation_job
        self._cipher: Final = StateCipher(secrets)
        self._active_item_ids: Final[set[UUID]] = set()
        self._processing_batch = False

    def _payload(self, row: StoredOnboarding) -> OnboardingPayload:
        return OnboardingPayload.model_validate_json(self._cipher.open(row.item.id, row.ciphertext))

    def _fingerprints(self, request: OnboardingImport, entry: OnboardingEntry) -> tuple[str, ...]:
        if entry.totp_secret:
            validate_two_factor_entry(entry, request.supplier)
            if not (entry.proxy_profile_id or request.proxy_profile_id) or entry.content:
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
        entries, errors = import_entries(request)
        prepared: Final = request.model_copy(update={"entries": entries})
        return await self._preview(prepared, errors)

    async def _preview(
        self, request: OnboardingImport, errors: tuple[str | None, ...] = ()
    ) -> tuple[OnboardingPreview, ...]:
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
            if errors and (message := errors[index]) is not None:
                return OnboardingPreview(
                    index=index, label=entry.label, status="invalid", message=message
                ), frozenset()
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
        entries, errors = import_entries(request)
        prepared: Final = request.model_copy(update={"entries": entries})
        return await self._submit(prepared, errors)

    async def _submit(self, request: OnboardingImport, errors: tuple[str | None, ...] = ()) -> OnboardingImportResult:
        preview: Final = await self._preview(request, errors)
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
                proxy_profile_id=entry.proxy_profile_id or request.proxy_profile_id,
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
                if self._automation is not None:
                    pause_payload: Final = self._payload(row)
                    if pause_payload.automation_job_id is not None:
                        active_job: Final = await self._automation.get_job(pause_payload.automation_job_id)
                        if active_job is not None and active_job.stage in ("queued", *AUTOMATION_ACTIVE):
                            cancelled: Final = await self._automation.cancel(active_job.id)
                            if isinstance(cancelled, Failure) and cancelled.code is not FailureCode.NOT_FOUND:
                                latest: Final = await self._automation.get_job(active_job.id)
                                if latest is not None and latest.stage in ("queued", *AUTOMATION_ACTIVE):
                                    return cancelled
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

    async def _persist_unlocked(
        self,
        row: StoredOnboarding,
        state: OnboardingState,
        message: str,
        *,
        created_automation_job_id: UUID | None = None,
    ) -> OnboardingItem | None:
        cancel_after_unlock: UUID | None = None
        saved_item: OnboardingItem | None = None
        while True:
            async with self.repository.worker_lock() as acquired:
                if acquired:
                    latest = await self.repository.get(row.item.id)
                    if latest is None or latest.history_cleared or latest.paused or latest.item.state == "disabled":
                        cancel_after_unlock = created_automation_job_id
                    else:
                        merged = latest.model_copy(
                            update={
                                "ciphertext": row.ciphertext,
                                "item": row.item.model_copy(
                                    update={
                                        "attempts": row.item.attempts,
                                        "card_name": row.item.card_name or latest.item.card_name,
                                        "models": row.item.models or latest.item.models,
                                    }
                                ),
                            }
                        )
                        saved_item = await self._save(merged, state, message)
                    break
            # 未拿到锁时先释放数据库连接；不能用旧快照覆盖已经确认的暂停。
            await asyncio.sleep(0.05)
        if cancel_after_unlock is not None and self._automation is not None:
            await self._automation.cancel(cancel_after_unlock)
        return saved_item

    async def import_login_file(self, card_id: UUID, content: str) -> Result[EnvironmentView]:
        row: StoredOnboarding | None = None
        payload: OnboardingPayload | None = None
        entries: tuple[OnboardingEntry, ...] = ()
        record: EnvironmentRecord | None = None
        item_id: UUID | None = None
        async with self.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "后台正在处理卡片，请稍后重试")
            record = await self._environments.get(card_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "卡片不存在")
            if record.auth_file_name or record.credential_fingerprints:
                return Failure(FailureCode.CONFLICT, "2FA 登录资料不能直接替换有效凭据，请使用空卡片或批量上号")
            if not record.enabled or record.manual_cooldown or record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.CONFLICT, "卡片已停用、冷却或删除")
            if record.proxy_mode is not ProxyMode.PROFILE or not record.proxy_profile_id:
                return Failure(FailureCode.CONFLICT, "请先为卡片选择 OAuth 出站代理")
            try:
                entries = parse_two_factor_text(content, record.supplier.value)
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
            existing: Final = tuple(item for item in await self.repository.list() if item.item.card_id == card_id)
            if existing:
                if existing[0].fingerprints != keys or self._payload(existing[0]).entry != entries[0]:
                    return Failure(FailureCode.CONFLICT, "该卡片已有不同的上号资料，请在原任务中继续")
                return Success(to_view(record))
            checked: Final = await self._preview(request)
            if checked[0].status != "valid":
                return Failure(FailureCode.CONFLICT, checked[0].message)
            now: Final = utc_now()
            item_id = uuid5(request.job_id, "0")
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
            use_automation = (
                self._automation is not None
                and record.supplier is SupplierKind.OPENAI_CODEX
                and bool(entries[0].totp_secret)
                and bool(entries[0].supplier_password)
                and await self._automation.readiness_error() is None
            )
            payload = OnboardingPayload(
                entry=entries[0],
                mailbox_ready=True,
                proxy_profile_id=record.proxy_profile_id,
                manual_oauth_only=not use_automation,
            )
            row = StoredOnboarding(
                item=item, ciphertext=self._cipher.seal(item_id, payload.model_dump_json()), fingerprints=keys
            )
            if not await self.repository.insert(row):
                return Failure(FailureCode.CONFLICT, "账号已有上号任务，请在原任务中继续")
            self._active_item_ids.add(item_id)
        try:
            assert row is not None and payload is not None and record is not None and item_id is not None
            if use_automation and self._automation is not None:
                credentials: Final = (
                    f"{entries[0].label}----{entries[0].supplier_password}----{entries[0].totp_secret}"
                )
                submitted: Final = await self._automation.submit(
                    JobInput(
                        id=uuid5(item_id, "automation:0"),
                        environment_id=card_id,
                        credentials=credentials,
                        draft=None,
                    )
                )
                if isinstance(submitted, Failure):
                    await self._persist_unlocked(
                        row, "failed", submitted.message or "浏览器 OAuth 自动队列入队失败，请检查原卡片后重试"
                    )
                    return submitted
                tracked_payload: Final = payload.model_copy(update={"automation_job_id": submitted.value.id})
                tracked_row: Final = row.model_copy(
                    update={"ciphertext": self._cipher.seal(item_id, tracked_payload.model_dump_json())}
                )
                persisted: Final = await self._persist_unlocked(
                    tracked_row,
                    "awaiting_authorization",
                    "登录资料已保存并加入浏览器 OAuth 自动队列",
                    created_automation_job_id=submitted.value.id,
                )
                if persisted is None:
                    return Failure(FailureCode.CONFLICT, "任务已被暂停或清理")
                return Success(to_view(record))
            authorization: Final = await self._service.authorize_environment(
                card_id, operation_id=f"onboarding:{item_id}", require_empty=True
            )
            if isinstance(authorization, Failure):
                await self._persist_unlocked(row, "failed", "授权启动失败，登录资料已保存，请检查原卡片后重试")
                return authorization
            persisted_manual: Final = await self._persist_unlocked(
                row, "awaiting_authorization", "登录资料已保存，请完成登录和二次验证"
            )
            if persisted_manual is None:
                return Failure(FailureCode.CONFLICT, "任务已被暂停或清理")
            return Success(authorization.value.environment)
        finally:
            if item_id is not None:
                self._active_item_ids.discard(item_id)

    async def targets(self) -> tuple[OnboardingTargetView, ...]:
        items: Final = tuple(row.item for row in await self.repository.list())
        return tuple(target_view(target, items) for target in await self.repository.targets())

    async def authorization(self, item_id: UUID) -> Result[AuthorizationView]:
        row: Final = await self.repository.get(item_id)
        if row is None or row.item.source != "oauth" or row.item.state != "awaiting_authorization":
            return Failure(FailureCode.CONFLICT, "任务暂无待完成授权")
        if self._automation is not None:
            payload: Final = self._payload(row)
            if payload.automation_job_id is not None:
                job: Final = await self._automation.get_job(payload.automation_job_id)
                if job is not None and (job.stage in ("queued", *AUTOMATION_ACTIVE) or job.cleanup_pending):
                    if job.stage == "queued":
                        readiness_problem: Final = await self._automation.readiness_error()
                        if readiness_problem is not None:
                            return Failure(FailureCode.CONFLICT, readiness_problem)
                    return Failure(
                        FailureCode.CONFLICT,
                        "任务已接入浏览器 OAuth 自动化队列，请在自动化面板查看进度或接管屏幕",
                    )
        return await self._service.pending_authorization(row.item.card_id)

    async def _process(self, running: StoredOnboarding) -> None:
        payload: Final = self._payload(running)
        request: Final = CreateEnvironmentRequest(
            name=onboarding_card_name(running.item.supplier, running.item.label),
            supplier=SupplierKind(running.item.supplier),
            proxy_profile_id=payload.proxy_profile_id,
            operation_id=f"onboarding:{running.item.id}",
        )
        if running.item.source == "auth_file":
            result: Final = await self._service.create_auth_file_environment(
                request,
                running.item.card_id,
                payload.entry.label,
                payload.entry.content.encode(),
            )
            if isinstance(result, Failure):
                await self._persist_unlocked(running, "failed", "凭证导入或验证失败，请检查原卡片后重试")
                return
            await self._observe(running, unlocked=True)
            return
        existing: Final = await self._environments.get(running.item.card_id)
        if existing is not None and (
            not existing.enabled or existing.manual_cooldown or existing.status is EnvironmentStatus.DELETING
        ):
            await self._persist_unlocked(running, "disabled", "原卡片已手动停用、冷却或删除，自动化不会覆盖")
            return
        if (
            existing is not None
            and self._automation is not None
            and self._retry_automation_job is not None
            and payload.automation_job_id is not None
        ):
            previous_job: Final = await self._automation.get_job(payload.automation_job_id)
            if previous_job is not None and (
                previous_job.stage == "verification_failed"
                or (existing.oauth_verification_pending and previous_job.stage in ("failed", "cancelled", "expired"))
            ):
                if previous_job.cleanup_pending:
                    requeued: Final = running.model_copy(
                        update={"item": running.item.model_copy(update={"attempts": max(0, running.item.attempts - 1)})}
                    )
                    await self._persist_unlocked(requeued, "queued", "浏览器自动化清理中，稍后自动重试验收")
                    return
                retried: Final = await self._retry_automation_job(previous_job.id)
                if isinstance(retried, Failure):
                    latest_job: Final = await self._automation.get_job(previous_job.id)
                    if retried.code is FailureCode.CONFLICT and (
                        "已有运行任务" in retried.message
                        or "清理" in retried.message
                        or (
                            latest_job is not None
                            and (latest_job.stage in ("queued", *AUTOMATION_ACTIVE) or latest_job.cleanup_pending)
                        )
                    ):
                        requeued = running.model_copy(
                            update={
                                "item": running.item.model_copy(update={"attempts": max(0, running.item.attempts - 1)})
                            }
                        )
                        await self._persist_unlocked(requeued, "queued", "浏览器自动化队列正在推进，稍后自动重试验收")
                        return
                    await self._persist_unlocked(
                        running, "failed", retried.message or "自动重试验收失败，请检查原卡片后重试"
                    )
                    return
                await self._persist_unlocked(
                    running, "awaiting_authorization", f"已重新触发同卡模型验收：{retried.value.message}",
                    created_automation_job_id=previous_job.id,
                )
                return
        if (
            existing is not None
            and existing.auth_file_name is not None
            and existing.status is not EnvironmentStatus.ERROR
        ):
            await self._observe(running, unlocked=True)
            return
        if (
            self._automation is not None
            and not payload.manual_oauth_only
            and running.item.supplier == "openai_codex"
            and running.item.source == "oauth"
            and payload.entry.totp_secret
            and payload.entry.supplier_password
        ):
            await self._enqueue_automation(running, payload, request, existing)
            return
        pending: Final = await self._service.pending_authorization(running.item.card_id)
        if isinstance(pending, Success):
            await self._persist_unlocked(running, "awaiting_authorization", "请打开授权并完成登录，验证通过后自动完成")
            return
        result_oauth: Final = (
            await self._service.create_environment(request, environment_id=running.item.card_id)
            if existing is None
            else await self._service.authorize_environment(running.item.card_id, require_empty=True)
            if payload.entry.totp_secret
            else await self._service.resume_onboarding_oauth(running.item.card_id)
        )
        await self._persist_unlocked(
            running,
            "failed" if isinstance(result_oauth, Failure) else "awaiting_authorization",
            "授权启动失败，请检查卡片并手动重试"
            if isinstance(result_oauth, Failure)
            else "请完成登录；验证码或二次验证需人工接续",
        )

    async def _stop_unready_job(self, job_id: UUID) -> str | None:
        assert self._automation is not None
        reason = await self._automation.readiness_error()
        if reason is None:
            return None
        result = await self._automation.cancel(job_id)
        if isinstance(result, Failure):
            return reason if result.code is FailureCode.NOT_FOUND else None
        return reason if result.value.stage == "cancelled" or result.value.cancel_requested else None

    async def _enqueue_automation(
        self,
        running: StoredOnboarding,
        payload: OnboardingPayload,
        request: CreateEnvironmentRequest,
        existing: EnvironmentRecord | None,
    ) -> None:
        assert self._automation is not None
        if payload.automation_job_id is not None:
            previous: Final = await self._automation.get_job(payload.automation_job_id)
            if previous is not None and (previous.stage in ("queued", *AUTOMATION_ACTIVE) or previous.cleanup_pending):
                if previous.stage == "queued":
                    queued_problem: Final = await self._stop_unready_job(previous.id)
                    if queued_problem is not None:
                        await self._persist_unlocked(
                            running, "failed", f"浏览器 OAuth 自动化未就绪，已取消排队任务：{queued_problem}"
                        )
                        return
                message: Final = (
                    "浏览器自动登录等待人工接管验证码或二次验证"
                    if previous.stage == "manual"
                    else f"浏览器 OAuth 自动队列执行中：{previous.message}"
                )
                await self._persist_unlocked(running, "awaiting_authorization", message)
                return
        readiness_problem: Final = await self._automation.readiness_error()
        if readiness_problem is not None:
            await self._persist_unlocked(running, "failed", readiness_problem)
            return
        credentials: Final = f"{running.item.label}----{payload.entry.supplier_password}----{payload.entry.totp_secret}"
        if existing is None:
            proxy_id: Final = payload.proxy_profile_id or await self._service.default_proxy_profile_id()
            if not proxy_id:
                await self._persist_unlocked(running, "failed", "批量 2FA 自动上号需要指定代理配置，请先选择代理后重试")
                return
            stale: Final = await self._automation.get_job(running.item.card_id)
            if stale is not None and (stale.stage in ("queued", *AUTOMATION_ACTIVE) or stale.cleanup_pending):
                updated_payload: Final = payload.model_copy(update={"automation_job_id": stale.id})
                tracked: Final = running.model_copy(
                    update={"ciphertext": self._cipher.seal(running.item.id, updated_payload.model_dump_json())}
                )
                await self._persist_unlocked(
                    tracked, "awaiting_authorization", f"浏览器 OAuth 自动队列执行中：{stale.message}"
                )
                return
            if stale is not None and stale.stage in ("failed", "cancelled", "expired", "verification_failed"):
                deleted: Final = await self._automation.delete_job(stale.id)
                if isinstance(deleted, Failure) and deleted.code is not FailureCode.NOT_FOUND:
                    if deleted.code is FailureCode.CONFLICT:
                        requeued: Final = running.model_copy(
                            update={"item": running.item.model_copy(update={"attempts": max(0, running.item.attempts - 1)})}
                        )
                        await self._persist_unlocked(requeued, "queued", "浏览器自动化队列正在推进，稍后自动重新入队")
                        return
                    await self._persist_unlocked(
                        running, "failed", deleted.message or "清理历史自动化任务失败，请稍后重试"
                    )
                    return
            job_input: Final = JobInput(
                id=running.item.card_id,
                environment_id=running.item.card_id,
                credentials=credentials,
                draft=AutomationDraft(name=request.name, proxy_profile_id=proxy_id),
            )
        else:
            job_input = JobInput(
                id=uuid5(running.item.id, f"automation:{running.item.attempts}"),
                environment_id=running.item.card_id,
                credentials=credentials,
                draft=None,
            )
        submitted: Final = await self._automation.submit(job_input)
        if isinstance(submitted, Failure):
            await self._persist_unlocked(
                running, "failed", submitted.message or "浏览器 OAuth 自动队列入队失败，请检查资料后重试"
            )
            return
        saved_payload: Final = payload.model_copy(update={"automation_job_id": submitted.value.id})
        queued_row: Final = running.model_copy(
            update={"ciphertext": self._cipher.seal(running.item.id, saved_payload.model_dump_json())}
        )
        await self._persist_unlocked(
            queued_row,
            "awaiting_authorization",
            "已加入浏览器 OAuth 自动队列，等待自动登录并验证",
            created_automation_job_id=submitted.value.id,
        )

    async def _observe(self, row: StoredOnboarding, *, unlocked: bool = False) -> None:
        async def persist(target: StoredOnboarding, state: OnboardingState, message: str) -> OnboardingItem | None:
            if unlocked:
                return await self._persist_unlocked(target, state, message)
            return await self._save(target, state, message)

        result: Final = await self._service.get_environment(row.item.card_id)
        record: Final = await self._environments.get(row.item.card_id)
        if self._automation is not None:
            payload: Final = self._payload(row)
            if payload.automation_job_id is not None:
                job: Final = await self._automation.get_job(payload.automation_job_id)
                if job is not None and (job.stage in ("queued", *AUTOMATION_ACTIVE) or job.cleanup_pending):
                    if job.stage == "queued":
                        queued_problem: Final = await self._stop_unready_job(job.id)
                        if queued_problem is not None:
                            await persist(
                                row,
                                "failed",
                                f"浏览器 OAuth 自动化未就绪，已取消排队任务：{queued_problem}",
                            )
                            return
                    message: Final = (
                        "浏览器自动登录等待人工接管验证码或二次验证"
                        if job.stage == "manual"
                        else f"浏览器 OAuth 自动队列执行中：{job.message}"
                    )
                    await persist(row, "awaiting_authorization", message)
                    return
                if job is not None and job.stage == "verification_failed":
                    await persist(row, "failed", f"自动上号模型验收失败：{job.message}")
                    return
                if (
                    job is not None
                    and job.stage in ("failed", "cancelled", "expired")
                    and (
                        record is None
                        or record.status is not EnvironmentStatus.READY
                        or record.oauth_verification_pending
                    )
                ):
                    await persist(row, "failed", f"自动上号失败：{job.message}")
                    return
        if record is None:
            await persist(row, "failed", "卡片已不存在，请检查；不会自动重复创建")
            return
        if (
            not record.enabled
            or record.manual_cooldown
            or record.status in (EnvironmentStatus.DISABLED, EnvironmentStatus.DELETING)
        ):
            await persist(row, "disabled", "卡片已手动停用或冷却，自动化已暂停")
            return
        if (
            record.status is EnvironmentStatus.READY
            and not record.configuration_pending
            and isinstance(result, Success)
        ):
            if record.oauth_verification_pending:
                await persist(row, "failed", "凭据已保存但模型验收未通过，请检查卡片验收状态后重试")
                return
            gateway: Final = self._service.gateway_environment(record)
            if not gateway.routable:
                await persist(row, "cooling_down", "当前卡片不可路由，等待后台配额或健康恢复")
                return
            if not usable_models(record):
                await persist(row, "cooling_down", "当前模型额度耗尽或处于冷却，等待恢复")
                return
            await self._complete(row, record, unlocked=unlocked)
            return
        if record.status is EnvironmentStatus.COOLING_DOWN or record.automatic_cooldown:
            await persist(row, "cooling_down", "额度或健康冷却中，恢复验证通过后重新计入")
            return
        if record.status is EnvironmentStatus.ERROR:
            await persist(row, "failed", "卡片验证失败或授权失效，请检查卡片后重试")
            return
        if (
            record.oauth_expires_at is not None
            and record.oauth_expires_at <= utc_now()
            and record.auth_file_name is None
        ):
            await persist(row, "failed", "授权已过期，请手动重试")
            return
        if row.item.source == "auth_file":
            await persist(row, "failed", "凭证已保存，卡片配置或模型仍未就绪，请稍后重试原任务")
            return
        await persist(row, "awaiting_authorization", "等待授权完成和模型验证")

    async def _complete(self, row: StoredOnboarding, record: EnvironmentRecord, *, unlocked: bool = False) -> None:
        async def persist(target: StoredOnboarding, state: OnboardingState, message: str) -> OnboardingItem | None:
            if unlocked:
                return await self._persist_unlocked(target, state, message)
            return await self._save(target, state, message)

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
                await persist(row, "failed", "实际授权账号与导入邮箱不一致，卡片已停用，请检查登录账号")
            return
        account: Final = record.credential_email or record.credential_account_id or row.item.label.removesuffix(".json")
        name: Final = onboarding_card_name(row.item.supplier, account, record.quota.plan_type)
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
                await persist(row, "failed", "凭证已验证，但卡片命名或配置尚未完成，请重试原任务")
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
        await persist(completed, "ready", "凭证、模型与网关配置验证通过")

    async def run_once(self) -> None:
        selected: StoredOnboarding | None = None
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
            if self._processing_batch:
                return
            pending: Final = tuple(
                row
                for row in await self.repository.list()
                if row.item.state in ("queued", "running") and row.item.id not in self._active_item_ids
            )
            if not pending:
                return
            if pending[0].item.state == "running" and pending[0].item.attempts >= 3:
                await self._save(pending[0], "failed", "任务多次中断，已暂停自动恢复，请检查后手动重试")
                return
            selected = pending[0].model_copy(
                update={"item": pending[0].item.model_copy(update={"attempts": pending[0].item.attempts + 1})}
            )
            await self._save(selected, "running", "创建或恢复卡片，验证凭证与模型")
            self._processing_batch = True
            self._active_item_ids.add(selected.item.id)
        try:
            await self._process(selected)
        except Exception:
            await self._persist_unlocked(selected, "failed", "后台任务未完成，检查卡片后可重试")
        finally:
            self._active_item_ids.discard(selected.item.id)
            self._processing_batch = False

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
