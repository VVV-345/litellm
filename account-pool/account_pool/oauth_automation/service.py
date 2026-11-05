"""编排管理员配置和加密任务登记，不执行浏览器登录或上游凭据轮换。"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Final, Protocol
from uuid import UUID, uuid4

from pydantic import JsonValue, TypeAdapter

from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import AuthorizationFlow, EnvironmentStatus, SupplierKind, utc_now
from account_pool.isp_probe import ProxyLocation, ProxyLocationProbe
from account_pool.oauth_automation.models import (
    ACTIVE,
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
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose, StateCipher


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

    def identity(self, email: str) -> str:
        key: Final = self.secrets.derive(UUID(int=0), SecretPurpose.CREDENTIAL_IDENTITY).encode()
        return hmac.new(key, f"oauth-automation:codex:{email.casefold()}".encode(), hashlib.sha256).hexdigest()

    async def view(self) -> AutomationView:
        state: Final = await self.repository.read()
        return AutomationView(
            version=state.version,
            settings=state.settings,
            profiles=tuple(row.item for row in state.profiles),
            jobs=tuple(row.item for row in state.jobs),
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
        async with self.ownership.operation(request.environment_id), self.repository.transaction() as tx:
            state: Final = tx.snapshot
            existing: Final = next((job for job in state.jobs if job.item.id == request.id), None)
            if existing is not None:
                return (
                    Success(existing.item)
                    if existing.account_digest == identity and existing.item.environment_id == request.environment_id
                    else Failure(FailureCode.CONFLICT, "任务标识已被其他输入使用")
                )
            record: Final = await self.environments.get(request.environment_id)
            if record is None or record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.NOT_FOUND, "请选择已有的空白 Codex 测试卡片")
            if (
                record.supplier is not SupplierKind.OPENAI_CODEX
                or record.authorization_flow is not AuthorizationFlow.BROWSER_OAUTH
            ):
                return Failure(FailureCode.INVALID, "首版仅支持 Codex 浏览器 OAuth")
            if record.auth_file_name or record.credential_fingerprints or not record.enabled or record.manual_cooldown:
                return Failure(FailureCode.CONFLICT, "卡片已有凭据、停用或处于手动冷却，不覆盖现有认证")
            if (
                record.proxy_profile_id is None
                or record.configuration_pending
                or record.desired_configuration_version != record.observed_configuration_version
            ):
                return Failure(FailureCode.CONFLICT, "请先保存并同步卡片的普通出口代理配置")
            if any(
                job.item.environment_id == record.id and job.item.stage in ACTIVE | {"queued"} for job in state.jobs
            ):
                return Failure(FailureCode.CONFLICT, "这张卡片已有排队或运行中的任务")
            if any(p.item.bound_environment_id == record.id and p.account_digest != identity for p in state.profiles):
                return Failure(FailureCode.CONFLICT, "这张卡片已绑定其他登录身份，请使用新的空白卡片")
            bound: Final = next((p for p in state.profiles if p.account_digest == identity), None)
            if bound is not None and bound.item.bound_environment_id != record.id:
                if (
                    bound.item.bound_environment_id is not None
                    and await self.environments.get(bound.item.bound_environment_id) is None
                ):
                    return Failure(
                        FailureCode.CONFLICT, "原卡片已删除，请到指纹配置确认删除失效绑定后再提交；新建指纹不会覆盖旧绑定"
                    )
                return Failure(FailureCode.CONFLICT, "此账号的指纹已经绑定另一张卡片")
            profile: Final = bound or next(
                (p for p in state.profiles if p.item.enabled and p.account_digest is None), None
            )
            if profile is None or not profile.item.enabled:
                return Failure(FailureCode.CONFLICT, "没有可用的未绑定指纹，请添加或启用该账号原有指纹")
            if len(state.jobs) >= 200:
                return Failure(FailureCode.CONFLICT, "任务记录已达到 200 条上限，暂不能继续添加")
            matched: Final = await self._match_profile(profile, record.proxy_profile_id) if bound is None else Success(profile)
            if isinstance(matched, Failure):
                return matched
            now: Final = utc_now()
            item: Final = JobView(
                id=request.id,
                environment_id=record.id,
                profile_id=profile.item.id,
                proxy_profile_id=record.proxy_profile_id,
                configuration_version=record.observed_configuration_version,
                model=request.model,
                created_at=now,
                updated_at=now,
            )
            job: Final = StoredJob(
                item=item,
                account_digest=identity,
                credential_ciphertext=StateCipher(self.secrets).seal(record.id, request.credentials),
            )
            assigned: Final = matched.value.model_copy(
                update={
                    "account_digest": identity,
                    "item": matched.value.item.model_copy(
                        update={
                            "bound_environment_id": record.id,
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

    async def _match_profile(self, profile: StoredProfile, proxy_id: str) -> Result[StoredProfile]:
        if not profile.item.auto_match_proxy:
            return Success(profile)
        try:
            proxy_url: Final = await self.proxy_profiles.get_url(proxy_id)
            if not proxy_url:
                return Failure(FailureCode.CONFLICT, "所选出口代理不可用，未绑定指纹或启动登录")
            location: Final = await self.location_probe.locate(proxy_url)
            generated: Final = await self.worker.generate(location.country_code, profile.item.os)
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
