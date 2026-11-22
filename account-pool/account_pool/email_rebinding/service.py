"""登记邮箱资料并要求显式确认；这里只做短事务，不执行浏览器或收信网络操作。"""

import csv
import io
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from uuid import UUID, uuid4

from pydantic import ValidationError

from account_pool.domain import EnvironmentStatus, SupplierKind, utc_now
from account_pool.email_rebinding.imports import parse_mailboxes
from account_pool.email_rebinding.models import (
    BindingPoolView,
    BindingView,
    ConfirmBinding,
    MailboxSecret,
    MailboxView,
    PoolImportResult,
    PoolUpload,
    ProposeBinding,
    StoredBinding,
    StoredMailbox,
)
from account_pool.email_rebinding.repository import PostgresBindingRepository
from account_pool.oauth_automation.models import ACTIVE
from account_pool.oauth_automation.service import AutomationRegistry
from account_pool.shared.result import Failure, FailureCode, Result, Success
from account_pool.shared.secrets import StateCipher


def operation(job_id: UUID) -> str:
    return f"email-rebind:{job_id.hex}"


@dataclass(frozen=True, slots=True)
class BindingService:
    repository: PostgresBindingRepository
    automation: AutomationRegistry

    @property
    def cipher(self) -> StateCipher:
        return StateCipher(self.automation.secrets)

    async def view(self) -> BindingPoolView:
        state: Final = await self.repository.read()
        return BindingPoolView(
            version=state.version,
            mailboxes=tuple(x.item for x in state.mailboxes),
            jobs=tuple(x.item for x in state.jobs),
        )

    async def upload(self, request: PoolUpload) -> Result[PoolImportResult]:
        try:
            rows: Final = parse_mailboxes(request.content)
        except ValidationError:
            return Failure(FailureCode.INVALID, "邮箱字段长度或格式无效，未导入任何资料")
        except ValueError as error:
            return Failure(FailureCode.INVALID, str(error))
        async with self.repository.transaction() as tx:
            known: Final = frozenset(row.digest for row in tx.snapshot.mailboxes)
            new: Final = tuple(
                StoredMailbox(
                    item=MailboxView(
                        id=(identity := uuid4()),
                        email=row.secret.email,
                        method=row.secret.method,
                        status="bound" if row.bound else "available",
                        bound_at=utc_now() if row.bound else None,
                    ),
                    digest=self.automation.identity("mailbox:" + row.secret.email),
                    ciphertext=self.cipher.seal(identity, row.secret.model_dump_json()),
                )
                for row in rows
                if self.automation.identity("mailbox:" + row.secret.email) not in known
            )
            if len(tx.snapshot.mailboxes) + len(new) > 2000:
                return Failure(FailureCode.CONFLICT, "邮箱池最多保留 2000 条资料，原数据没有覆盖")
            marked: Final = frozenset(row.secret.email for row in rows if row.bound)
            prior: Final = tuple(
                row.model_copy(update={"item": row.item.model_copy(update={"status": "bound", "bound_at": utc_now()})})
                if row.item.email in marked and row.item.status == "available"
                else row
                for row in tx.snapshot.mailboxes
            )
            if new or prior != tx.snapshot.mailboxes:
                await tx.save(tx.snapshot.model_copy(update={"mailboxes": (*prior, *new)}))
        return Success(PoolImportResult(view=await self.view(), added=len(new), existing=len(rows) - len(new)))

    async def export(self) -> str:
        output: Final = io.StringIO()
        writer: Final = csv.writer(output)
        writer.writerow(("email", "binding_status", "bound_card_id", "bound_at"))
        for row in (await self.view()).mailboxes:
            writer.writerow(
                (
                    ("'" + row.email if row.email.startswith(("=", "+", "-", "@")) else row.email),
                    row.status,
                    str(row.environment_id or ""),
                    row.bound_at.isoformat() if row.bound_at else "",
                )
            )
        return output.getvalue()

    async def propose(self, request: ProposeBinding) -> Result[BindingView]:
        async with self.automation.ownership.operation(request.environment_id):
            record: Final = await self.automation.environments.get(request.environment_id)
            if (
                record is None
                or record.supplier is not SupplierKind.OPENAI_CODEX
                or record.status is not EnvironmentStatus.READY
                or record.proxy_profile_id is None
                or not record.credential_email
                or not record.credential_account_id
                or not record.auth_file_name
                or record.oauth_automation_operation
                or record.oauth_verification_pending
                or record.configuration_pending
                or not record.enabled
                or record.manual_cooldown
                or record.auth_file_disabled
            ):
                return Failure(FailureCode.CONFLICT, "仅允许已完成 OAuth、可用且没有其它操作的 Codex 卡片换绑")
            automation: Final = await self.automation.repository.read()
            profile: Final = next((p for p in automation.profiles if p.item.bound_environment_id == record.id), None)
            if (
                profile is None
                or not profile.item.enabled
                or not profile.login_ciphertext
                or profile.account_digest != self.automation.identity(record.credential_email)
            ):
                return Failure(FailureCode.CONFLICT, "请先保存此卡的原账号密码、2FA 与固定指纹，未预占邮箱")
            if (
                not automation.settings.enabled
                or automation.checked_image != self.automation.worker.image
                or not automation.checked_image
            ):
                return Failure(FailureCode.CONFLICT, "请先完成浏览器镜像自检并启用自动化")
            if any(
                x.item.environment_id == record.id and (x.item.stage in ACTIVE | {"queued"} or x.cleanup_pending)
                for x in automation.jobs
            ):
                return Failure(FailureCode.CONFLICT, "此卡已有授权任务，请先等待任务及清理结束")
            async with self.repository.transaction() as tx:
                existing: Final = next((x for x in tx.snapshot.jobs if x.item.id == request.id), None)
                if existing:
                    return (
                        Success(existing.item)
                        if existing.item.environment_id == record.id
                        else Failure(FailureCode.CONFLICT, "操作标识已占用")
                    )
                active: Final = next(
                    (
                        x
                        for x in tx.snapshot.jobs
                        if x.item.environment_id == record.id and x.item.stage not in {"completed", "cancelled"}
                    ),
                    None,
                )
                if active:
                    return Success(active.item)
                mailbox: Final = next(
                    (
                        m
                        for m in tx.snapshot.mailboxes
                        if m.item.status == "available"
                        and m.item.email != record.credential_email.casefold()
                        and (request.mailbox_id is None or m.item.id == request.mailbox_id)
                    ),
                    None,
                )
                if mailbox is None:
                    return Failure(FailureCode.CONFLICT, "没有可分配邮箱或任务记录已满；不会重复使用已绑定邮箱")
                existing_jobs: Final = tx.snapshot.jobs
                pruned_jobs: Final = (
                    existing_jobs
                    if len(existing_jobs) < 200
                    else _prune_oldest_terminal_job(existing_jobs)
                )
                if pruned_jobs is None:
                    return Failure(FailureCode.CONFLICT, "没有可分配邮箱或任务记录已满；不会重复使用已绑定邮箱")
                now: Final = utc_now()
                item: Final = BindingView(
                    id=request.id,
                    environment_id=record.id,
                    mailbox_id=mailbox.item.id,
                    old_email=record.credential_email,
                    target_email=mailbox.item.email,
                    created_at=now,
                    updated_at=now,
                    confirmation_expires_at=now + timedelta(minutes=15),
                )
                reserved: Final = mailbox.model_copy(
                    update={
                        "item": mailbox.item.model_copy(
                            update={
                                "status": "reserved",
                                "job_id": item.id,
                                "environment_id": record.id,
                            }
                        )
                    }
                )
                await tx.save(
                    tx.snapshot.model_copy(
                        update={
                            "mailboxes": tuple(
                                reserved if m.item.id == mailbox.item.id else m for m in tx.snapshot.mailboxes
                            ),
                            "jobs": (
                                *pruned_jobs,
                                StoredBinding(
                                    item=item,
                                    profile_id=profile.item.id,
                                    card_version=record.version,
                                    proxy_profile_id=record.proxy_profile_id,
                                    configuration_version=record.observed_configuration_version,
                                    profile_digest=profile.item.digest,
                                    original_login_ciphertext=profile.login_ciphertext,
                                ),
                            ),
                        }
                    )
                )
                return Success(item)

    async def confirm(self, job_id: UUID, request: ConfirmBinding) -> Result[BindingView]:
        initial: Final = next((j for j in (await self.repository.read()).jobs if j.item.id == job_id), None)
        if initial is None:
            return Failure(FailureCode.NOT_FOUND, "换绑任务不存在")
        async with (
            self.automation.ownership.operation(initial.item.environment_id),
            self.repository.transaction() as tx,
        ):
            job: Final = next(j for j in tx.snapshot.jobs if j.item.id == job_id)
            if job.item.stage != "awaiting_confirmation":
                return (
                    Failure(FailureCode.CONFLICT, "任务已取消") if job.item.stage == "cancelled" else Success(job.item)
                )
            if job.item.version != request.version or job.item.confirmation_expires_at <= utc_now():
                return Failure(FailureCode.CONFLICT, "确认已过期或资料已改变，请重新核对")
            record: Final = await self.automation.environments.get(job.item.environment_id)
            if record is None or record.oauth_automation_operation not in (None, operation(job_id)):
                return Failure(FailureCode.CONFLICT, "卡片已被其它操作占用")
            if record.oauth_automation_operation is None:
                if (
                    record.version != job.card_version
                    or record.status is not EnvironmentStatus.READY
                    or not record.enabled
                    or record.manual_cooldown
                ):
                    return Failure(FailureCode.CONFLICT, "卡片已改变，未启动换绑")
                saved: Final = await self.automation.environments.save_if_version(
                    record.model_copy(
                        update={
                            "version": record.version + 1,
                            "oauth_automation_operation": operation(job_id),
                            "email_rebinding_job_id": job_id,
                        }
                    ),
                    record.version,
                )
                if saved is None:
                    return Failure(FailureCode.CONFLICT, "卡片已改变，未启动换绑")
            updated: Final = job.model_copy(
                update={
                    "item": job.item.model_copy(
                        update={
                            "stage": "draining",
                            "version": job.item.version + 1,
                            "confirmed_at": utc_now(),
                            "updated_at": utc_now(),
                            "message": "已确认，暂停新请求并等待在途请求结束",
                        }
                    )
                }
            )
            await tx.save(
                tx.snapshot.model_copy(
                    update={"jobs": tuple(updated if j.item.id == job_id else j for j in tx.snapshot.jobs)}
                )
            )
            return Success(updated.item)

    async def save(self, row: StoredBinding) -> StoredBinding:
        async with self.repository.transaction() as tx:
            latest: Final = next(j for j in tx.snapshot.jobs if j.item.id == row.item.id)
            if latest.item.version != row.item.version:
                return latest
            updated: Final = row.model_copy(
                update={
                    "item": row.item.model_copy(
                        update={
                            "version": row.item.version + 1,
                            "updated_at": utc_now(),
                        }
                    )
                }
            )
            await tx.save(
                tx.snapshot.model_copy(
                    update={"jobs": tuple(updated if j.item.id == row.item.id else j for j in tx.snapshot.jobs)}
                )
            )
            return updated

    async def mark_bound(self, row: StoredBinding) -> StoredBinding:
        async with self.repository.transaction() as tx:
            latest: Final = next(j for j in tx.snapshot.jobs if j.item.id == row.item.id)
            if latest.item.email_changed:
                return latest
            updated: Final = latest.model_copy(
                update={
                    "item": latest.item.model_copy(
                        update={
                            "email_changed": True,
                            "stage": "email_changed",
                            "version": latest.item.version + 1,
                            "updated_at": utc_now(),
                            "message": "邮箱已换绑并核对账号，接下来仅重新授权，不再换绑",
                        }
                    )
                }
            )
            await tx.save(
                tx.snapshot.model_copy(
                    update={
                        "jobs": tuple(updated if j.item.id == row.item.id else j for j in tx.snapshot.jobs),
                        "mailboxes": tuple(
                            m.model_copy(
                                update={"item": m.item.model_copy(update={"status": "bound", "bound_at": utc_now()})}
                            )
                            if m.item.id == row.item.mailbox_id
                            else m
                            for m in tx.snapshot.mailboxes
                        ),
                    }
                )
            )
            return updated

    async def cancel(self, job_id: UUID) -> Result[BindingView]:
        row: Final = next((j for j in (await self.repository.read()).jobs if j.item.id == job_id), None)
        if row is None:
            return Failure(FailureCode.NOT_FOUND, "换绑任务不存在")
        if row.item.irreversible_started or row.item.email_changed or row.item.browser_session_id:
            return Failure(FailureCode.CONFLICT, "任务可能已修改邮箱，保留进度和邮箱占用；请接管或核实，不能重新分配")
        async with self.automation.ownership.operation(row.item.environment_id), self.repository.transaction() as tx:
            latest: Final = next(j for j in tx.snapshot.jobs if j.item.id == job_id)
            if latest.item.irreversible_started or latest.item.browser_session_id:
                return Failure(FailureCode.CONFLICT, "浏览器已开始操作，未释放邮箱")
            record: Final = await self.automation.environments.get(row.item.environment_id)
            if record and record.oauth_automation_operation == operation(job_id):
                saved: Final = await self.automation.environments.save_if_version(
                    record.model_copy(
                        update={
                            "version": record.version + 1,
                            "oauth_automation_operation": None,
                            "email_rebinding_job_id": None,
                        }
                    ),
                    record.version,
                )
                if saved is None:
                    return Failure(FailureCode.CONFLICT, "卡片已改变，请刷新")
            updated: Final = latest.item.model_copy(
                update={
                    "stage": "cancelled",
                    "version": latest.item.version + 1,
                    "updated_at": utc_now(),
                    "message": "已取消，未执行换绑",
                }
            )
            await tx.save(
                tx.snapshot.model_copy(
                    update={
                        "jobs": tuple(
                            j.model_copy(update={"item": updated}) if j.item.id == job_id else j
                            for j in tx.snapshot.jobs
                        ),
                        "mailboxes": tuple(
                            m.model_copy(
                                update={
                                    "item": m.item.model_copy(
                                        update={"status": "available", "job_id": None, "environment_id": None}
                                    )
                                }
                            )
                            if m.item.job_id == job_id and m.item.status == "reserved"
                            else m
                            for m in tx.snapshot.mailboxes
                        ),
                    }
                )
            )
            return Success(updated)

    async def abort(self, job_id: UUID) -> Result[BindingView]:
        row: Final = next((j for j in (await self.repository.read()).jobs if j.item.id == job_id), None)
        if row is None:
            return Failure(FailureCode.NOT_FOUND, "换绑任务不存在")
        if row.item.stage in {"completed", "cancelled"}:
            return Success(row.item)
        async with self.automation.ownership.operation(row.item.environment_id), self.repository.transaction() as tx:
            latest: Final = next(j for j in tx.snapshot.jobs if j.item.id == job_id)
            if latest.item.stage in {"completed", "cancelled"}:
                return Success(latest.item)
            record: Final = await self.automation.environments.get(row.item.environment_id)
            if record is not None and (
                record.oauth_automation_operation == operation(job_id) or record.email_rebinding_job_id == job_id
            ):
                saved: Final = await self.automation.environments.save_if_version(
                    record.model_copy(
                        update={
                            "version": record.version + 1,
                            **(
                                {"oauth_automation_operation": None}
                                if record.oauth_automation_operation == operation(job_id)
                                else {}
                            ),
                            **(
                                {"email_rebinding_job_id": None}
                                if record.email_rebinding_job_id == job_id
                                else {}
                            ),
                        }
                    ),
                    record.version,
                )
                if saved is None:
                    return Failure(FailureCode.CONFLICT, "卡片已改变，请刷新")
            # 已进入不可逆边界的任务强制解除卡片占用，但保留目标邮箱占用防止重复分配。
            updated: Final = latest.item.model_copy(
                update={
                    "stage": "cancelled",
                    "browser_session_id": None,
                    "version": latest.item.version + 1,
                    "updated_at": utc_now(),
                    "message": (
                        "管理员已强制终止并解除卡片占用；目标邮箱保留占用状态，避免重复分配"
                        if latest.item.irreversible_started or latest.item.email_changed
                        else "已取消，未执行换绑"
                    ),
                }
            )
            release_mailbox: Final = not latest.item.irreversible_started and not latest.item.email_changed
            await tx.save(
                tx.snapshot.model_copy(
                    update={
                        "jobs": tuple(
                            j.model_copy(update={"item": updated, "worker_ciphertext": None})
                            if j.item.id == job_id
                            else j
                            for j in tx.snapshot.jobs
                        ),
                        "mailboxes": tuple(
                            m.model_copy(
                                update={
                                    "item": m.item.model_copy(
                                        update={"status": "available", "job_id": None, "environment_id": None}
                                    )
                                }
                            )
                            if release_mailbox and m.item.job_id == job_id and m.item.status == "reserved"
                            else m
                            for m in tx.snapshot.mailboxes
                        ),
                    }
                )
            )
            return Success(updated)

    async def mail_secret(self, mailbox_id: UUID) -> MailboxSecret:
        row: Final = next(m for m in (await self.repository.read()).mailboxes if m.item.id == mailbox_id)
        return MailboxSecret.model_validate_json(self.cipher.open(mailbox_id, row.ciphertext))

    async def save_mail_secret(self, mailbox_id: UUID, secret: MailboxSecret) -> None:
        async with self.repository.transaction() as tx:
            await tx.save(
                tx.snapshot.model_copy(
                    update={
                        "mailboxes": tuple(
                            row.model_copy(
                                update={"ciphertext": self.cipher.seal(mailbox_id, secret.model_dump_json())}
                            )
                            if row.item.id == mailbox_id
                            else row
                            for row in tx.snapshot.mailboxes
                        )
                    }
                )
            )


def _prune_oldest_terminal_job(jobs: tuple[StoredBinding, ...]) -> tuple[StoredBinding, ...] | None:
    oldest: Final = next((index for index, job in enumerate(jobs) if job.item.stage in {"completed", "cancelled"}), None)
    if oldest is None:
        return None
    return (*jobs[:oldest], *jobs[oldest + 1 :])
