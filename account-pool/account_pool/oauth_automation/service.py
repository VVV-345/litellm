"""编排管理员配置和加密任务登记，不执行浏览器登录或上游凭据轮换。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Final, Protocol
from uuid import UUID, uuid4

from pydantic import JsonValue, TypeAdapter

from account_pool.application.authorization import saved_login_can_replace
from account_pool.credential_ownership import CredentialOwnership, oauth_account_digest
from account_pool.domain import AuthorizationFlow, EnvironmentStatus, SupplierKind, utc_now
from account_pool.isp_probe import ProxyLocation, ProxyLocationProbe
from account_pool.oauth_automation.login_scripts import LoginScriptStore
from account_pool.oauth_automation.models import (
    ACTIVE,
    BROWSER_LOCALE_BY_COUNTRY,
    AutomationSettings,
    AutomationView,
    BrowserOS,
    JobInput,
    JobView,
    ProfileInput,
    ProfileUpdate,
    ProfileView,
    StoredJob,
    StoredProfile,
)
from account_pool.oauth_automation.repository import PostgresAutomationRepository
from account_pool.ports import EnvironmentRepository, ProxyProfileRepository
from account_pool.shared.result import Failure, FailureCode, Result, Success
from account_pool.shared.secrets import EnvironmentSecretDeriver, StateCipher


class ProfileWorker(Protocol):
    @property
    def image(self) -> str | None: ...
    async def generate(self, locale: str, os: BrowserOS = "linux") -> dict[str, JsonValue]: ...
    async def self_check(self) -> bool: ...


class LocationProbe(Protocol):
    async def locate(self, proxy_url: str) -> ProxyLocation: ...


async def image_check(worker: ProfileWorker) -> bool:
    try:
        return await worker.self_check()
    except Exception:
        return False


def credential_email(text: str) -> str | None:
    records: Final = tuple(line for line in text.splitlines() if "----" in line)
    if len(text.encode()) > 8192 or len(records) != 1:
        return None
    fields: Final = records[0].strip().split("----")
    if len(fields) != 3:
        return None
    email, password, secret = fields
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not password or len(password) > 4096:
        return None
    if not re.fullmatch(r"[A-Z2-7]{16,128}=*", secret.upper()):
        return None
    return email.casefold()


@dataclass(frozen=True, slots=True)
class AutomationRegistry:
    repository: PostgresAutomationRepository
    environments: EnvironmentRepository
    worker: ProfileWorker
    secrets: EnvironmentSecretDeriver
    ownership: CredentialOwnership
    proxy_profiles: ProxyProfileRepository
    location_probe: LocationProbe = field(default_factory=ProxyLocationProbe)
    login_scripts: LoginScriptStore | None = None

    def identity(self, email: str) -> str:
        return oauth_account_digest(email, self.secrets)

    async def view(self) -> AutomationView:
        state: Final = await self.repository.read()
        return AutomationView(
            version=state.version,
            settings=state.settings,
            profiles=tuple(row.item.model_copy(update={"login_saved": bool(row.login_ciphertext) or any(
                job.item.profile_id == row.item.id and job.item.environment_id == row.item.bound_environment_id
                and job.account_digest == row.account_digest and bool(job.credential_ciphertext)
                for job in state.jobs
            )}) for row in state.profiles),
            jobs=tuple(row.item.model_copy(update={"cleanup_pending": row.cleanup_pending}) for row in state.jobs),
            checked_at=state.checked_at,
            image_checked=state.checked_image is not None and state.checked_image == self.worker.image,
        )

    async def self_check(self) -> Result[AutomationView]:
        if self.worker.image is None:
            return Failure(FailureCode.CONFLICT, "请先配置按 digest 固定的服务端浏览器镜像")
        checked: Final = await image_check(self.worker)
        async with self.repository.transaction() as tx:
            await tx.save(
                tx.snapshot.model_copy(
                    update={
                        "checked_image": self.worker.image if checked else None,
                        "checked_at": utc_now() if checked else None,
                    }
                )
            )
        if not checked:
            return Failure(FailureCode.UPSTREAM, "浏览器镜像自检失败；检查镜像、显示服务和预装资源，不会启动账号登录")
        return Success(await self.view())

    async def save_settings(self, values: AutomationSettings, version: int) -> Result[AutomationView]:
        async with self.repository.transaction() as tx:
            state: Final = tx.snapshot
            if state.version != version:
                return Failure(FailureCode.CONFLICT, "配置已改变，请刷新后再保存")
            if values.enabled and (state.checked_image is None or state.checked_image != self.worker.image):
                return Failure(FailureCode.CONFLICT, "新镜像必须先通过自检才能开启自动化")
            jobs: Final = tuple(
                row.model_copy(update={"item": row.item.model_copy(update={"cancel_requested": True})})
                if not values.enabled and row.item.stage in ACTIVE
                else row
                for row in state.jobs
            )
            await tx.save(state.model_copy(update={"settings": values, "jobs": jobs}))
        return Success(await self.view())

    async def create_profile(self, request: ProfileInput) -> Result[ProfileView]:
        try:
            data: Final = TypeAdapter(dict[str, JsonValue]).validate_python(await self.worker.generate(request.locale, request.os))
        except Exception:
            return Failure(FailureCode.UPSTREAM, "指纹生成或验证失败，请先自检固定镜像")
        encoded: Final = json.dumps(data, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > 65_536:
            return Failure(FailureCode.UPSTREAM, "指纹配置超过镜像支持的大小")
        item: Final = ProfileView(
            name=request.name,
            enabled=request.enabled,
            locale=request.locale,
            auto_match_proxy=request.auto_match_proxy,
            os=request.os,
            id=uuid4(),
            digest=hashlib.sha256(encoded.encode()).hexdigest(),
        )
        async with self.repository.transaction() as tx:
            if len(tx.snapshot.profiles) >= 100:
                return Failure(FailureCode.CONFLICT, "指纹配置已满，请先整理未使用的配置")
            await tx.save(
                tx.snapshot.model_copy(
                    update={"profiles": (*tx.snapshot.profiles, StoredProfile(item=item, data=data))}
                )
            )
        return Success(item)

    async def update_profile(self, profile_id: UUID, request: ProfileUpdate) -> Result[ProfileView]:
        async with self.repository.transaction() as tx:
            state: Final = tx.snapshot
            row: Final = next((p for p in state.profiles if p.item.id == profile_id), None)
            if row is None:
                return Failure(FailureCode.NOT_FOUND, "指纹配置不存在")
            if row.item.version != request.version:
                return Failure(FailureCode.CONFLICT, "指纹配置已改变，请刷新")
            if any(job.item.profile_id == profile_id and job.item.stage in ACTIVE for job in state.jobs):
                return Failure(FailureCode.CONFLICT, "运行中的指纹配置不能修改")
            if (
                row.item.locale != request.locale or row.item.auto_match_proxy != request.auto_match_proxy
                or row.item.os != request.os
            ):
                return Failure(FailureCode.CONFLICT, "固定指纹的系统和语言不可变，请创建新的未绑定配置")
            updated: Final = row.model_copy(
                update={
                    "item": row.item.model_copy(
                        update={
                            "name": request.name,
                            "enabled": request.enabled,
                            "version": request.version + 1,
                        }
                    )
                }
            )
            await tx.save(
                state.model_copy(
                    update={"profiles": tuple(updated if p.item.id == profile_id else p for p in state.profiles)}
                )
            )
            return Success(updated.item)

    async def submit(self, request: JobInput) -> Result[JobView]:
        email: Final = credential_email(request.credentials)
        if email is None:
            return Failure(
                FailureCode.INVALID, "请选择一条 email----password----TOTPBase32 登录资料，不接收多账号混合记录"
            )
        identity: Final = self.identity(email)
        draft: Final = request.draft
        if draft is not None and request.environment_id != request.id:
            return Failure(FailureCode.INVALID, "新卡片和授权任务必须使用同一个操作标识")
        async with self.ownership.operation(request.environment_id), self.repository.transaction() as tx:
            state: Final = tx.snapshot
            existing: Final = next((job for job in state.jobs if job.item.id == request.id), None)
            if existing is not None:
                return (
                    Success(existing.item)
                    if existing.account_digest == identity and existing.item.environment_id == request.environment_id
                    and existing.draft == draft
                    and (request.reuse_profile_id is None or existing.item.profile_id == request.reuse_profile_id)
                    else Failure(FailureCode.CONFLICT, "任务标识已被其他输入使用")
                )
            record: Final = await self.environments.get(request.environment_id)
            if draft is not None:
                if record is not None:
                    return Failure(FailureCode.CONFLICT, "新卡片操作标识已占用")
                if await self.proxy_profiles.get_url(draft.proxy_profile_id) is None:
                    return Failure(FailureCode.INVALID, "所选出口代理不可用，未创建卡片")
                proxy_id, config_version = draft.proxy_profile_id, 0
            else:
                if record is None or record.status is EnvironmentStatus.DELETING:
                    return Failure(FailureCode.NOT_FOUND, "请选择已有的空白 Codex 卡片")
                if (
                    record.supplier is not SupplierKind.OPENAI_CODEX
                    or record.authorization_flow is not AuthorizationFlow.BROWSER_OAUTH
                ):
                    return Failure(FailureCode.INVALID, "仅支持 Codex 浏览器 OAuth")
                if record.auth_file_name or record.credential_fingerprints or not record.enabled or record.manual_cooldown:
                    return Failure(FailureCode.CONFLICT, "卡片已有凭据、停用或处于手动冷却，不覆盖现有认证")
                if (
                    record.proxy_profile_id is None
                    or record.configuration_pending
                    or record.desired_configuration_version != record.observed_configuration_version
                ):
                    return Failure(FailureCode.CONFLICT, "请先保存并同步卡片的普通出口代理配置")
                proxy_id, config_version = record.proxy_profile_id, record.observed_configuration_version
            environment_id: Final = request.environment_id
            if any(
                job.item.environment_id == environment_id and (
                    job.item.stage in ACTIVE | {"queued"} or job.cleanup_pending
                ) for job in state.jobs
            ):
                return Failure(FailureCode.CONFLICT, "这张卡片已有排队或运行中的任务")
            if any(p.item.bound_environment_id == environment_id and p.account_digest != identity for p in state.profiles):
                return Failure(FailureCode.CONFLICT, "卡片已有其他账号的指纹；若从未登录，先释放未登录预留")
            bound: Final = next((p for p in state.profiles if p.account_digest == identity), None)
            if request.reuse_profile_id is not None and (bound is None or bound.item.id != request.reuse_profile_id):
                return Failure(FailureCode.CONFLICT, "只能迁移与提交资料为同一账号的原指纹")
            transfer: Final = bound is not None and bound.item.bound_environment_id != environment_id
            if transfer and bound is not None:
                prior_id: Final = bound.item.bound_environment_id
                if prior_id is None or await self.environments.get(prior_id) is not None:
                    return Failure(FailureCode.CONFLICT, "原卡片仍存在，不允许把同一账号分给两张卡")
                if request.reuse_profile_id is None:
                    return Failure(FailureCode.CONFLICT, "原卡片已删除，请显式选择迁移原指纹或删除失效绑定")
                if any(job.item.profile_id == bound.item.id and (
                    job.cleanup_pending or job.item.stage in ACTIVE | {"queued"}
                ) for job in state.jobs):
                    return Failure(FailureCode.CONFLICT, "原指纹还有未停止或未清理的任务")
            profile: Final = bound or next(
                (p for p in state.profiles if p.item.enabled and p.account_digest is None), None
            )
            if profile is None or not profile.item.enabled:
                return Failure(FailureCode.CONFLICT, "没有可用的未绑定指纹，请添加或启用该账号原有指纹")
            if len(state.jobs) >= 200:
                return Failure(FailureCode.CONFLICT, "任务记录已达到 200 条上限，暂不能继续添加")
            matched: Final = await self._match_profile(profile, proxy_id) if bound is None else Success(profile)
            if isinstance(matched, Failure):
                return matched
            now: Final = utc_now()
            item: Final = JobView(
                id=request.id,
                environment_id=environment_id,
                profile_id=profile.item.id,
                proxy_profile_id=proxy_id,
                configuration_version=config_version,
                model=request.model,
                draft_pending=draft is not None,
                message="待创建卡片；已保存授权任务" if draft is not None else "等待开启自动化后串行执行",
                created_at=now,
                updated_at=now,
            )
            job: Final = StoredJob(
                item=item,
                account_digest=identity,
                credential_ciphertext=StateCipher(self.secrets).seal(environment_id, request.credentials),
                draft=draft,
            )
            assigned: Final = matched.value.model_copy(
                update={
                    "account_digest": identity,
                    **({"login_ciphertext": None} if transfer else {}),
                    "item": matched.value.item.model_copy(
                        update={
                            "bound_environment_id": environment_id,
                            "reservation": bound is None or profile.item.reservation,
                            "version": profile.item.version + 1,
                        }
                    ),
                }
            )
            await tx.save(
                state.model_copy(
                    update={
                        "profiles": tuple(assigned if p.item.id == assigned.item.id else p for p in state.profiles),
                        "jobs": (*state.jobs, job),
                    }
                )
            )
            return Success(item)

    async def reauthorize(self, environment_id: UUID) -> Result[JobView | None]:
        async with self.ownership.operation(environment_id), self.repository.transaction() as tx:
            state: Final = tx.snapshot
            record: Final = await self.environments.get(environment_id)
            if record is None or record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.NOT_FOUND, "卡片不存在")
            if record.supplier is not SupplierKind.OPENAI_CODEX or (
                record.authorization_flow is not AuthorizationFlow.BROWSER_OAUTH
            ):
                return Success(None)
            existing: Final = next(
                (row for row in state.jobs if row.item.environment_id == environment_id
                 and row.item.stage in ACTIVE | {"queued"}), None,
            )
            if existing is not None:
                return Success(existing.item)
            if record.oauth_state_consumed_at and (record.oauth_staged or record.oauth_verification_pending):
                recovery: Final = next((
                    job for job in reversed(state.jobs) if job.item.environment_id == environment_id
                    and job.item.stage == "verification_failed"
                ), None)
                if recovery is not None:
                    return Success(recovery.item.model_copy(update={"cleanup_pending": recovery.cleanup_pending}))
            saved: Final = next(
                (row for row in reversed(state.jobs) if row.item.environment_id == environment_id
                 and row.credential_ciphertext), None,
            )
            profile: Final = next((row for row in state.profiles if row.item.bound_environment_id == environment_id), None)
            ciphertext: Final = (
                profile.login_ciphertext if profile and profile.login_ciphertext
                else saved.credential_ciphertext if saved else None
            )
            if ciphertext is None:
                return Success(None)
            if (
                profile is None or not profile.item.enabled or profile.account_digest is None
                or (not profile.login_ciphertext and saved is not None and (
                    profile.item.id != saved.item.profile_id or profile.account_digest != saved.account_digest
                ))
            ):
                return Failure(FailureCode.CONFLICT, "已保存的登录资料与当前指纹绑定不一致")
            if self.worker.image is None or not state.settings.enabled or state.checked_image != self.worker.image:
                return Failure(FailureCode.CONFLICT, "请先通过当前浏览器镜像自检并开启自动化")
            if (
                not record.enabled or record.manual_cooldown or record.auth_file_disabled
                or record.configuration_pending or record.proxy_profile_id is None
                or record.desired_configuration_version != record.observed_configuration_version
                or record.oauth_automation_operation is not None
                or any(row.item.environment_id == environment_id and row.cleanup_pending for row in state.jobs)
            ):
                return Failure(FailureCode.CONFLICT, "卡片或代理尚未就绪，或上次任务还在清理")
            now: Final = utc_now()
            if record.auth_file_name or record.credential_fingerprints:
                email: Final = record.credential_email
                if email is None or self.identity(email) != profile.account_digest or not saved_login_can_replace(
                    record, email, now,
                ):
                    return Failure(FailureCode.CONFLICT, "卡片仍有可用凭据或账号无法确认，未启动重新授权")
            elif record.status not in (EnvironmentStatus.ERROR, EnvironmentStatus.AWAITING_AUTHORIZATION):
                return Failure(FailureCode.CONFLICT, "卡片状态不允许重新授权")
            if len(state.jobs) >= 200:
                return Failure(FailureCode.CONFLICT, "任务记录已达到 200 条上限，暂不能继续添加")
            item: Final = JobView(
                id=uuid4(), environment_id=environment_id, profile_id=profile.item.id,
                proxy_profile_id=record.proxy_profile_id, configuration_version=record.observed_configuration_version,
                message="已复用加密登录资料，等待建立新的 OAuth 会话", created_at=now, updated_at=now,
            )
            job: Final = StoredJob(
                item=item, account_digest=profile.account_digest, credential_ciphertext=ciphertext,
            )
            await tx.save(state.model_copy(update={"jobs": (*state.jobs, job)}))
            return Success(item)

    async def queue_rebound_authorization(
        self, environment_id: UUID, binding_id: UUID, old_email: str, target_email: str, original_login_ciphertext: str,
    ) -> Result[JobView]:
        async with self.ownership.operation(environment_id), self.repository.transaction() as tx:
            state: Final = tx.snapshot
            existing: Final = next((j for j in state.jobs if j.email_rebinding_job_id == binding_id), None)
            if existing is not None:
                return Success(existing.item) if existing.item.environment_id == environment_id else Failure(FailureCode.CONFLICT, "换绑任务归属不一致")
            record: Final = await self.environments.get(environment_id)
            profile: Final = next((p for p in state.profiles if p.item.bound_environment_id == environment_id), None)
            if (record is None or record.email_rebinding_job_id != binding_id or not record.credential_account_id
                or record.oauth_automation_operation not in (None, f"email-rebind:{binding_id.hex}")
                or record.credential_email is None or record.credential_email.casefold() not in (old_email.casefold(), target_email.casefold())
                or not record.enabled or record.manual_cooldown or record.auth_file_disabled or record.configuration_pending
                or record.credential_refresh.save_pending or record.proxy_profile_id is None or profile is None or not profile.item.enabled
                or profile.account_digest not in (self.identity(old_email), self.identity(target_email))
                or len(state.jobs) >= 200 or not state.settings.enabled or state.checked_image != self.worker.image):
                return Failure(FailureCode.CONFLICT, "原卡或指纹已改变；邮箱已绑定，保留进度等待核对")
            if any(j.item.environment_id == environment_id and j.item.stage in ACTIVE | {"queued"} for j in state.jobs):
                return Failure(FailureCode.CONFLICT, "原卡已有其它授权任务")
            cipher: Final = StateCipher(self.secrets)
            original: Final = cipher.open(environment_id, original_login_ciphertext)
            if credential_email(original) != old_email.casefold():
                return Failure(FailureCode.CONFLICT, "保存的原账号资料与换绑任务不一致")
            _, password, totp = original.strip().split("----")
            credentials: Final = cipher.seal(environment_id, f"{target_email}----{password}----{totp}")
            now: Final = utc_now()
            item: Final = JobView(id=uuid4(), environment_id=environment_id, profile_id=profile.item.id,
                proxy_profile_id=record.proxy_profile_id, configuration_version=record.observed_configuration_version,
                created_at=now, updated_at=now, message="邮箱已换绑，沿用原密码、2FA、指纹与代理重新授权")
            updated_record: Final = record.model_copy(update={
                "version": record.version + 1, "credential_email": target_email,
                "status": EnvironmentStatus.ERROR, "desired_state": EnvironmentStatus.ERROR,
                "last_error": "上游认证失效，请重新认证", "oauth_automation_operation": None,
                "oauth_verification_pending": True,
                "credential_refresh": record.credential_refresh.model_copy(update={"schedule_reason": "reauthorization_required"}),
                "updated_at": now,
            })
            saved: Final = await self.environments.save_if_version(updated_record, record.version)
            if saved is None:
                return Failure(FailureCode.CONFLICT, "原卡已改变，未覆盖凭据")
            job: Final = StoredJob(item=item, account_digest=self.identity(target_email), credential_ciphertext=credentials,
                                  email_rebinding_job_id=binding_id)
            updated_profile: Final = profile.model_copy(update={"account_digest": job.account_digest, "login_ciphertext": credentials})
            await tx.save(state.model_copy(update={
                "profiles": tuple(updated_profile if p.item.id == profile.item.id else p for p in state.profiles),
                "jobs": (*state.jobs, job),
            }))
            return Success(item)

    async def remember_login(self, job: StoredJob) -> None:
        if not job.credential_ciphertext:
            return
        async with self.repository.transaction() as tx:
            profiles: Final = tuple(
                row.model_copy(update={
                    "login_ciphertext": job.credential_ciphertext,
                    "item": row.item.model_copy(update={"reservation": False}),
                })
                if row.item.id == job.item.profile_id and row.item.bound_environment_id == job.item.environment_id
                and row.account_digest == job.account_digest else row
                for row in tx.snapshot.profiles
            )
            if profiles != tx.snapshot.profiles:
                await tx.save(tx.snapshot.model_copy(update={"profiles": profiles}))

    async def forget_login(self, profile_id: UUID) -> Result[AutomationView]:
        async with self.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "任务正在推进，请稍后再忘记登录资料")
            async with self.repository.transaction() as tx:
                profile: Final = next((row for row in tx.snapshot.profiles if row.item.id == profile_id), None)
                if profile is None:
                    return Failure(FailureCode.NOT_FOUND, "指纹配置不存在")
                related: Final = tuple(job for job in tx.snapshot.jobs if job.item.profile_id == profile_id)
                if any(job.item.stage in ACTIVE | {"queued"} or job.cleanup_pending for job in related):
                    return Failure(FailureCode.CONFLICT, "先结束并清理授权任务，再忘记登录资料")
                await tx.save(tx.snapshot.model_copy(update={
                    "profiles": tuple(row.model_copy(update={"login_ciphertext": None}) if row == profile else row
                                      for row in tx.snapshot.profiles),
                    "jobs": tuple(job.model_copy(update={"credential_ciphertext": ""}) if job in related else job
                                  for job in tx.snapshot.jobs),
                }))
        return Success(await self.view())

    async def release_reservation(self, profile_id: UUID) -> Result[AutomationView]:
        async with self.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "任务正在推进，请稍后再释放预留")
            async with self.repository.transaction() as tx:
                profile: Final = next((row for row in tx.snapshot.profiles if row.item.id == profile_id), None)
                if profile is None:
                    return Failure(FailureCode.NOT_FOUND, "指纹配置不存在")
                environment_id: Final = profile.item.bound_environment_id
                if not profile.item.reservation or environment_id is None or profile.login_ciphertext:
                    return Failure(FailureCode.CONFLICT, "已确认的指纹不能作为未登录预留释放")
                card: Final = await self.environments.get(environment_id)
                related: Final = tuple(job for job in tx.snapshot.jobs
                                       if job.item.profile_id == profile_id and job.item.environment_id == environment_id)
                if (card and (card.oauth_automation_operation or card.auth_file_name or card.oauth_state_consumed_at
                              or card.credential_fingerprints)) or any(
                    job.item.stage in ACTIVE | {"queued"} or job.cleanup_pending or job.login_submitted
                    or job.item.callback_consumed or (job.item.browser_session_id and not job.browser_fenced)
                    for job in related
                ):
                    return Failure(FailureCode.CONFLICT, "登录已开始或尚未清理，不能把指纹给其他账号")
                updated: Final = profile.model_copy(update={"account_digest": None, "item": profile.item.model_copy(
                    update={"reservation": False, "bound_environment_id": None, "version": profile.item.version + 1},
                )})
                await tx.save(tx.snapshot.model_copy(update={
                    "profiles": tuple(updated if row.item.id == profile_id else row for row in tx.snapshot.profiles),
                    "jobs": tuple(job.model_copy(update={"credential_ciphertext": ""}) if job in related else job
                                  for job in tx.snapshot.jobs),
                }))
        return Success(await self.view())

    async def _match_profile(self, profile: StoredProfile, proxy_id: str) -> Result[StoredProfile]:
        if not profile.item.auto_match_proxy:
            return Success(profile)
        try:
            proxy_url: Final = await self.proxy_profiles.get_url(proxy_id)
            if not proxy_url:
                return Failure(FailureCode.CONFLICT, "所选出口代理不可用，未绑定指纹或启动登录")
            location: Final = await self.location_probe.locate(proxy_url)
            preferred_locale: Final = BROWSER_LOCALE_BY_COUNTRY.get(location.country_code, location.country_code)
            generated: Final = await self.worker.generate(preferred_locale, profile.item.os)
            locale: Final = TypeAdapter(str).validate_python(generated.get("locale"))
            data: Final = {**generated, "timezone": location.timezone}
            encoded: Final = json.dumps(data, sort_keys=True, separators=(",", ":"))
            if len(encoded.encode()) > 65_536:
                raise ValueError("fingerprint is oversized")
            return Success(profile.model_copy(update={
                "data": data,
                "item": profile.item.model_copy(update={
                    "location": location, "matched_locale": locale,
                    "digest": hashlib.sha256(encoded.encode()).hexdigest(),
                }),
            }))
        except Exception:
            return Failure(FailureCode.UPSTREAM, "代理地区、语言或时区匹配失败；未绑定指纹或启动登录，请检查出口及固定镜像")

    async def cancel(self, job_id: UUID) -> Result[JobView]:
        async with self.repository.transaction() as tx:
            job: Final = next((row for row in tx.snapshot.jobs if row.item.id == job_id), None)
            if job is None:
                return Failure(FailureCode.NOT_FOUND, "任务不存在")
            if job.item.stage not in ACTIVE | {"queued"}:
                return Success(job.item)
            item: Final = job.item.model_copy(
                update={
                    "cancel_requested": True,
                    "updated_at": utc_now(),
                    **({"stage": "cancelled", "message": "排队任务已取消"} if job.item.stage == "queued" else {}),
                }
            )
            await tx.save(
                tx.snapshot.model_copy(
                    update={
                        "jobs": tuple(
                            row.model_copy(update={"item": item}) if row.item.id == job_id else row
                            for row in tx.snapshot.jobs
                        )
                    }
                )
            )
            return Success(item)

    async def delete_job(self, job_id: UUID) -> Result[AutomationView]:
        # 和 worker 的重试、清理互斥，避免删除正在重新排队的记录。
        async with self.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "任务正在推进，请稍后刷新再删除")
            async with self.repository.transaction() as tx:
                row: Final = next((job for job in tx.snapshot.jobs if job.item.id == job_id), None)
                if row is None:
                    return Failure(FailureCode.NOT_FOUND, "任务不存在")
                if row.item.stage in ACTIVE | {"queued"} or row.cleanup_pending:
                    return Failure(FailureCode.CONFLICT, "只能删除已结束且清理完成的任务记录")
                profiles: Final = tuple(
                    profile.model_copy(update={"login_ciphertext": row.credential_ciphertext})
                    if row.item.stage == "completed" and row.credential_ciphertext and not profile.login_ciphertext
                    and profile.item.id == row.item.profile_id
                    and profile.item.bound_environment_id == row.item.environment_id
                    and profile.account_digest == row.account_digest else profile
                    for profile in tx.snapshot.profiles
                )
                await tx.save(tx.snapshot.model_copy(
                    update={"profiles": profiles, "jobs": tuple(job for job in tx.snapshot.jobs if job.item.id != job_id)}
                ))
        return Success(await self.view())

    async def delete_profile(self, profile_id: UUID) -> Result[AutomationView]:
        async with self.repository.transaction() as tx:
            row: Final = next((profile for profile in tx.snapshot.profiles if profile.item.id == profile_id), None)
            if row is None:
                return Failure(FailureCode.NOT_FOUND, "指纹配置不存在")
            jobs: Final = tuple(job for job in tx.snapshot.jobs if job.item.profile_id == profile_id)
            if any(job.item.stage in ACTIVE | {"queued"} or job.cleanup_pending for job in jobs):
                return Failure(FailureCode.CONFLICT, "指纹仍有排队、运行或待清理任务，暂不能删除")
            if row.item.bound_environment_id is not None:
                if await self.environments.get(row.item.bound_environment_id) is not None:
                    return Failure(FailureCode.CONFLICT, "指纹仍绑定现有卡片，不能删除或更换登录身份")
            elif row.account_digest is not None or jobs:
                return Failure(FailureCode.CONFLICT, "指纹绑定状态不完整，请先核实原卡片和任务")
            await tx.save(
                tx.snapshot.model_copy(
                    update={"profiles": tuple(p for p in tx.snapshot.profiles if p.item.id != profile_id)}
                )
            )
        return Success(await self.view())
