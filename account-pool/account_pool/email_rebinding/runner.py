"""推进已确认的换绑任务，外部提交前落盘账号证明，失败不释放邮箱或重放换绑。"""

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from account_pool.domain import utc_now
from account_pool.email_rebinding.mailbox import MailboxReader, MailboxUnavailable
from account_pool.email_rebinding.models import (
    BindingStage,
    BindingView,
    MailboxSecret,
    RebindingWorkerView,
    StoredBinding,
)
from account_pool.email_rebinding.service import BindingService, operation
from account_pool.manager_logs import ManagerDiagnostic
from account_pool.oauth_automation.login_scripts import LoginScriptStore
from account_pool.oauth_automation.models import AutomationScreen
from account_pool.oauth_automation.runner import AutomationRunner
from account_pool.oauth_browser import OAuthBrowserAutomation, OAuthBrowserSessionGrant
from account_pool.shared.result import Failure, FailureCode, Result, Success

_LOGGER: Final = logging.getLogger(__name__)


class WorkerSecrets(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    token: str = Field(repr=False)
    ticket: str = Field(repr=False)
    script_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class EmailBindingRunner:
    service: BindingService
    oauth: AutomationRunner
    reader: MailboxReader
    scripts: LoginScriptStore

    async def _progress(self, row: StoredBinding, stage: BindingStage, message: str) -> StoredBinding:
        if row.item.stage == stage and row.item.message == message:
            return row
        saved: Final = await self.service.save(
            row.model_copy(update={"item": row.item.model_copy(update={
                "stage": stage, "message": message,
                "irreversible_started": row.item.irreversible_started or stage == "uncertain",
            })})
        )
        if self.oauth.record_diagnostic is not None:
            await self.oauth.record_diagnostic(
                row.item.environment_id,
                row.item.browser_session_id,
                ManagerDiagnostic(
                    step="email_verification",
                    state="failed"
                    if stage in {"failed", "verification_failed"}
                    else "completed"
                    if stage == "completed"
                    else "waiting"
                    if stage in {"manual", "uncertain", "waiting_code"}
                    else "running",
                ),
            )
        return saved

    async def _command(
        self, row: StoredBinding, action: str, payload: dict[str, JsonValue] | None = None
    ) -> RebindingWorkerView:
        if row.item.browser_session_id is None or row.worker_ciphertext is None:
            raise ValueError("worker session missing")
        secret: Final = WorkerSecrets.model_validate_json(
            self.service.cipher.open(row.item.environment_id, row.worker_ciphertext)
        )
        return await self.oauth.worker.binding_command(row.item.browser_session_id, secret.token, action, payload)

    async def _cleanup(self, row: StoredBinding) -> bool:
        if row.item.browser_session_id is None:
            return True
        await self.oauth.browser.fail(row.item.browser_session_id)
        session: Final = await self.oauth.browser.sessions.get(row.item.browser_session_id)
        return session is None or not session.cleanup_pending

    async def run_once(self) -> None:
        async with self.service.repository.worker_lock() as acquired:
            if not acquired:
                return
            jobs: Final = (await self.service.repository.read()).jobs
            for expired in (
                j
                for j in jobs
                if j.item.stage == "awaiting_confirmation" and j.item.confirmation_expires_at <= utc_now()
            ):
                await self.service.cancel(expired.item.id)
            row: Final = next(
                (
                    j
                    for j in jobs
                    if j.item.stage
                    not in {
                        "awaiting_confirmation",
                        "completed",
                        "cancelled",
                        "failed",
                        "uncertain",
                        "verification_failed",
                    }
                ),
                None,
            )
            if row is None:
                return
            if row.item.email_changed:
                await self._authorization(row)
            elif row.item.stage == "draining":
                if not await self.service.repository.has_requests(row.item.environment_id):
                    await self._start(row)
            elif row.item.stage == "starting" and not row.item.irreversible_started:
                if await self._cleanup(row):
                    await self.service.save(
                        row.model_copy(
                            update={
                                "worker_ciphertext": None,
                                "item": row.item.model_copy(update={"stage": "draining", "browser_session_id": None}),
                            }
                        )
                    )
            elif row.item.browser_session_id is not None:
                await self._advance(row)

    async def _start(self, row: StoredBinding) -> None:
        record: Final = await self.service.automation.environments.get(row.item.environment_id)
        state: Final = await self.service.automation.repository.read()
        profile: Final = next((p for p in state.profiles if p.item.id == row.profile_id), None)
        if (
            record is None
            or record.oauth_automation_operation != operation(row.item.id)
            or record.proxy_profile_id != row.proxy_profile_id
            or record.observed_configuration_version != row.configuration_version
            or not record.enabled
            or record.manual_cooldown
            or record.configuration_pending
            or profile is None
            or profile.item.digest != row.profile_digest
            or not profile.item.enabled
            or profile.item.bound_environment_id != record.id
        ):
            await self._progress(row, "failed", "原卡或指纹已改变，未执行换绑；原卡与邮箱保留")
            return
        if any(
            r.oauth_automation_operation not in (None, operation(row.item.id))
            for r in await self.service.automation.environments.list()
        ):
            return
        script: Final = self.scripts.read()
        if isinstance(script, Failure):
            await self._progress(row, "failed", script.message)
            return
        token: Final = secrets.token_urlsafe(32)
        claimed: Final = await self._progress(row, "starting", "正在准备原代理和原指纹的隔离浏览器")

        async def reserved(grant: OAuthBrowserSessionGrant) -> None:
            await self.service.save(
                claimed.model_copy(
                    update={
                        "worker_ciphertext": self.service.cipher.seal(
                            row.item.environment_id,
                            WorkerSecrets(
                                token=token,
                                ticket=grant.ticket,
                                script_sha256=script.value.info.sha256 if script.value else None,
                            ).model_dump_json(),
                        ),
                        "item": claimed.item.model_copy(update={"browser_session_id": grant.session.id}),
                    }
                )
            )

        try:
            started: Final = await self.oauth.browser.start_rebinding(
                row.item.environment_id,
                operation(row.item.id),
                OAuthBrowserAutomation(
                    fingerprint_json=json.dumps(profile.data, separators=(",", ":")),
                    worker_token=token,
                    expires_at=utc_now() + timedelta(seconds=state.settings.total_timeout_seconds),
                    human_timeout_seconds=state.settings.human_timeout_seconds,
                    purpose="email_rebind",
                    login_script_package=script.value.payload if script.value else None,
                ),
                reserved,
            )
            durable: Final = next(j for j in (await self.service.repository.read()).jobs if j.item.id == row.item.id)
            if isinstance(started, Failure):
                cleaned: Final = await self._cleanup(durable)
                target: Final = (
                    durable.model_copy(update={"worker_ciphertext": None, "item": durable.item.model_copy(update={"browser_session_id": None})})
                    if cleaned
                    else durable
                )
                await self._progress(target, "failed", started.message)
                return
            if not await self.oauth.worker.confirm_binding_script(
                started.value.session.id, token, script.value.info.sha256 if script.value else None
            ):
                cleaned_script: Final = await self._cleanup(durable)
                target_script: Final = (
                    durable.model_copy(update={"worker_ciphertext": None, "item": durable.item.model_copy(update={"browser_session_id": None})})
                    if cleaned_script
                    else durable
                )
                await self._progress(target_script, "failed", "浏览器镜像不支持当前换绑协议或脚本未匹配；没有发送登录资料")
                return
            logging_in: Final = await self.service.save(
                durable.model_copy(
                    update={
                        "item": durable.item.model_copy(
                            update={
                                "stage": "changing",
                                "message": "正在登录原账号，尚未允许修改邮箱",
                            }
                        )
                    }
                )
            )
            subject: Final = (
                self.service.cipher.open(row.item.id, row.subject_ciphertext) if row.subject_ciphertext else None
            )
            await self._command(
                logging_in,
                "start",
                {
                    "credentials": self.service.cipher.open(row.item.environment_id, row.original_login_ciphertext),
                    "target": {"email": row.item.target_email, "verify_only": row.verify_only, "subject": subject},
                },
            )
        except Exception:
            failed_row: Final = next(j for j in (await self.service.repository.read()).jobs if j.item.id == row.item.id)
            cleaned_exc: Final = await self._cleanup(failed_row)
            target_exc: Final = (
                failed_row.model_copy(update={"worker_ciphertext": None, "item": failed_row.item.model_copy(update={"browser_session_id": None})})
                if cleaned_exc
                else failed_row
            )
            await self._progress(
                target_exc,
                "uncertain" if failed_row.item.irreversible_started else "failed",
                "浏览器响应未确认，保留原卡与邮箱；不会自动重新换绑，请查看画面或核实",
            )

    async def _advance(self, row: StoredBinding) -> None:
        try:
            status: Final = await self._command(row, "status")
        except Exception:
            cleaned_err: Final = await self._cleanup(row)
            target_err: Final = (
                row.model_copy(update={"worker_ciphertext": None, "item": row.item.model_copy(update={"browser_session_id": None})})
                if cleaned_err
                else row
            )
            await self._progress(
                target_err, "uncertain" if row.item.irreversible_started else "failed",
                "浏览器连接不可用；提交前可清理后重试，提交后仅核实，不重放换绑",
            )
            return
        if status.status in {"failed", "expired", "cancelled"}:
            cleaned_stop: Final = await self._cleanup(row)
            target_stop: Final = (
                row.model_copy(update={"worker_ciphertext": None, "item": row.item.model_copy(update={"browser_session_id": None})})
                if cleaned_stop
                else row
            )
            await self._progress(
                target_stop, "uncertain" if row.item.irreversible_started else "failed",
                "浏览器已停止；提交前可清理后重试，提交后仅核实，不重放换绑",
            )
            return
        if status.status in {"waiting", "manual"}:
            await self._progress(row, "manual", "页面需要人工核对：原邮箱验证、人机验证或未知页面；可打开画面接管")
            return
        if status.step == "changed" and status.status == "callback_received":
            subject: Final = (
                self.service.cipher.open(row.item.id, row.subject_ciphertext) if row.subject_ciphertext else None
            )
            if (
                not subject
                or status.subject != subject
                or status.target_email != row.item.target_email
                or status.source_email is None
                or status.source_email.casefold() != row.item.old_email.casefold()
            ):
                await self._progress(row, "uncertain", "浏览器账号证明不一致，未替换任何卡片凭据，请人工核实")
                return
            bound: Final = await self.service.mark_bound(row)
            await self._authorization(bound)
            return
        if status.step == "awaiting_commit":
            await self._grant_commit(row, status)
            return
        if status.step in ("changing", "waiting_code", "verifying") and not row.item.irreversible_started:
            await self._progress(row, "uncertain", "浏览器报告了未获准的修改步骤，保留占用，请人工核实")
            return
        if status.step == "waiting_code":
            if row.mail_poll_failed:
                return
            waiting: Final = await self._progress(row, "waiting_code", "等待目标邮箱验证码；自动收信失败时可手工填码")
            if waiting.mail_poll_failed:
                return
            since: Final = waiting.code_requested_at or utc_now() - timedelta(seconds=10)
            timed: Final = (
                await self.service.save(waiting.model_copy(update={"code_requested_at": since}))
                if waiting.code_requested_at is None
                else waiting
            )
            try:
                secret: Final = await self.service.mail_secret(row.item.mailbox_id)

                async def persist(updated: MailboxSecret) -> None:
                    await self.service.save_mail_secret(row.item.mailbox_id, updated)

                code: Final = await self.reader.poll(secret, since, persist)
                if code:
                    await self._command(timed, "code", {"code": code})
            except MailboxUnavailable:
                await self.service.save(
                    timed.model_copy(
                        update={
                            "mail_poll_failed": True,
                            "item": timed.item.model_copy(
                                update={
                                    "message": "邮箱收信授权或连接不可用，已停止自动重试，请手工填验证码或明确重试收信",
                                }
                            ),
                        }
                    )
                )
            return

    async def _grant_commit(self, row: StoredBinding, status: RebindingWorkerView) -> None:
        if row.item.irreversible_started:
            await self._progress(row, "uncertain", "已记录提交边界，不能重复允许换绑；请仅核实结果")
            return
        if (
            not status.subject
            or status.source_email is None
            or status.source_email.casefold() != row.item.old_email.casefold()
        ):
            await self._progress(row, "manual", "登录账号与原卡不一致，未允许修改邮箱")
            return
        if (
            row.subject_ciphertext
            and self.service.cipher.open(row.item.id, row.subject_ciphertext) != status.subject
        ):
            await self._progress(row, "manual", "原账号身份已改变，未允许修改邮箱")
            return
        checkpoint: Final = await self.service.save(
            row.model_copy(
                update={
                    "subject_ciphertext": self.service.cipher.seal(row.item.id, status.subject),
                    "item": row.item.model_copy(update=MappingProxyType({"irreversible_started": True})),
                }
            )
        )
        if not checkpoint.item.irreversible_started or checkpoint.subject_ciphertext is None:
            return
        await self._command(checkpoint, "commit")
        await self._progress(checkpoint, "changing", "原账号证明已保存，正在通过官网修改邮箱")
        return

    async def _authorization(self, row: StoredBinding) -> None:
        current: Final = await self.service.automation.environments.get(row.item.environment_id)
        profile: Final = next(
            (p for p in (await self.service.automation.repository.read()).profiles if p.item.id == row.profile_id), None
        )
        # 尚未入队重新授权前要求配置版本严格不变；入队后 OAuth 验收完成会正常递增配置版本，允许单调增长。
        version_invalid: Final = (
            current is not None
            and (
                current.observed_configuration_version != row.configuration_version
                if row.item.oauth_job_id is None
                else current.observed_configuration_version < row.configuration_version
            )
        )
        if (
            current is None
            or current.proxy_profile_id != row.proxy_profile_id
            or version_invalid
            or profile is None
            or profile.item.digest != row.profile_digest
            or profile.item.bound_environment_id != row.item.environment_id
        ):
            await self._progress(
                row, "verification_failed", "原卡代理、配置或固定指纹已改变，保留换绑结果，未启动新的登录"
            )
            return
        if not await self._cleanup(row):
            await self._progress(row, "email_changed", "邮箱已绑定，等待旧浏览器清理；不会新建卡片或再次换绑")
            return
        if row.item.oauth_job_id is None:
            queued: Final = await self.service.automation.queue_rebound_authorization(
                row.item.environment_id,
                row.item.id,
                row.item.old_email,
                row.item.target_email,
                row.original_login_ciphertext,
            )
            if isinstance(queued, Failure):
                await self._progress(row, "verification_failed", queued.message)
                return
            await self.service.save(
                row.model_copy(
                    update={
                        "item": row.item.model_copy(
                            update={
                                "stage": "reauthorizing",
                                "oauth_job_id": queued.value.id,
                                "message": "邮箱已绑定，正在原卡上重新 OAuth、保存凭据并验收模型",
                            }
                        )
                    }
                )
            )
            return
        state: Final = await self.service.automation.repository.read()
        linked: Final = next((j for j in state.jobs if j.item.id == row.item.oauth_job_id), None)
        if linked is None or linked.email_rebinding_job_id != row.item.id:
            await self._progress(row, "verification_failed", "重新授权任务不存在或归属不一致，原卡和进度保留")
        elif (
            linked.item.stage == "completed"
            and linked.item.credentials_saved
            and linked.item.chat_verified
            and linked.item.responses_verified
        ):
            async with self.service.automation.ownership.operation(row.item.environment_id):
                record: Final = await self.service.automation.environments.get(row.item.environment_id)
                if (
                    record is None
                    or record.credential_email is None
                    or record.credential_email.casefold() != row.item.target_email.casefold()
                ):
                    await self._progress(row, "verification_failed", "授权邮箱尚未核对一致，未完成验收")
                    return
                if record.email_rebinding_job_id == row.item.id:
                    saved: Final = await self.service.automation.environments.save_if_version(
                        record.model_copy(
                            update={
                                "version": record.version + 1,
                                "email_rebinding_job_id": None,
                            }
                        ),
                        record.version,
                    )
                    if saved is None:
                        return
                await self._progress(row, "completed", "同一卡片完成换绑、OAuth、凭据保存和 Chat/Responses 模型验收")
        elif linked.item.stage in {"failed", "expired", "cancelled", "verification_failed"}:
            await self._progress(
                row, "verification_failed", "邮箱已绑定，但重新授权或模型验收未成功；重试只补授权和验收"
            )

    async def action(self, job_id: UUID, action: str) -> Result[BindingView]:
        async with self.service.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "任务正在推进，请稍后重试")
            row: Final = next((j for j in (await self.service.repository.read()).jobs if j.item.id == job_id), None)
            if row is None:
                return Failure(FailureCode.NOT_FOUND, "换绑任务不存在")
            if action == "abort":
                await self._cleanup(row)
                return await self.service.abort(job_id)
            if action == "cancel":
                if not row.item.irreversible_started and await self._cleanup(row):
                    cleared: Final = await self.service.save(
                        row.model_copy(update={"item": row.item.model_copy(update={"browser_session_id": None})})
                    )
                    return await self.service.cancel(cleared.item.id)
                return await self.service.cancel(job_id)
            if action == "retry" and row.item.email_changed:
                if row.item.oauth_job_id:
                    result: Final = await self.oauth.action(row.item.oauth_job_id, "retry")
                    if isinstance(result, Failure):
                        return result
                outcome_1: Final = await self._progress(
                    row, "reauthorizing", "仅重试原卡重新授权或模型验收，不再次换绑"
                )
                return Success(outcome_1.item)
            if action == "reconcile":
                if row.item.email_changed or not row.subject_ciphertext or not row.item.irreversible_started:
                    return Failure(FailureCode.CONFLICT, "没有已保存的原账号证明，请先人工核实；不能重放修改邮箱")
                if not await self._cleanup(row):
                    return Failure(FailureCode.CONFLICT, "旧浏览器仍在清理，未启动核实")
                outcome_2: Final = await self.service.save(
                    row.model_copy(
                        update={
                            "verify_only": True,
                            "worker_ciphertext": None,
                            "item": row.item.model_copy(
                                update={
                                    "stage": "draining",
                                    "browser_session_id": None,
                                    "message": "仅登录新邮箱核实原账号，不修改邮箱",
                                }
                            ),
                        }
                    )
                )
                return Success(outcome_2.item)
            if action == "retry" and not row.item.irreversible_started:
                if not await self._cleanup(row):
                    return Failure(FailureCode.CONFLICT, "旧浏览器仍在清理")
                outcome_3: Final = await self.service.save(
                    row.model_copy(
                        update={"item": row.item.model_copy(update={"stage": "draining", "browser_session_id": None})}
                    )
                )
                return Success(outcome_3.item)
            if action == "retry" and row.item.stage == "waiting_code":
                outcome_4: Final = await self.service.save(row.model_copy(update={"mail_poll_failed": False}))
                return Success(outcome_4.item)
            if (
                action in {"takeover", "resume"}
                and row.item.browser_session_id is not None
                and row.item.stage in {"starting", "changing", "waiting_code", "manual"}
            ):
                try:
                    worker_status: Final = await self._command(row, action)
                except Exception:
                    return Failure(FailureCode.UPSTREAM, "浏览器控制通道不可用，请稍后重试或核实任务状态")
                if action == "resume" and worker_status.status in {"waiting", "manual", "failed", "expired", "cancelled"}:
                    return Failure(FailureCode.CONFLICT, "当前页面尚未通过账号核对或浏览器已停止，不能交回自动化")
                outcome_5: Final = await self._progress(
                    row,
                    "manual" if action == "takeover" else "changing",
                    "请核对当前官网页面和账号；已提交步骤不会重放",
                )
                return Success(outcome_5.item)
            return Failure(FailureCode.CONFLICT, "此阶段不能重试换绑；请接管或使用仅核实功能")

    async def code(self, job_id: UUID, code: str) -> Result[BindingView]:
        async with self.service.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "任务正在推进，请稍后填码")
            row: Final = next((j for j in (await self.service.repository.read()).jobs if j.item.id == job_id), None)
            if row is None or row.item.stage != "waiting_code":
                return Failure(FailureCode.CONFLICT, "任务当前不等待目标邮箱验证码")
            try:
                await self._command(row, "code", {"code": code})
            except Exception:
                return Failure(FailureCode.UPSTREAM, "浏览器暂未接收验证码，请检查画面状态后重试")
            return Success(row.item)

    async def screen(self, job_id: UUID) -> Result[AutomationScreen]:
        row: Final = next((j for j in (await self.service.repository.read()).jobs if j.item.id == job_id), None)
        if (
            row is None
            or row.item.browser_session_id is None
            or row.item.stage in {"completed", "cancelled", "failed", "email_changed", "reauthorizing", "verification_failed"}
        ):
            return Failure(FailureCode.CONFLICT, "换绑浏览器尚未就绪")
        grant: Final = await self.oauth.browser.sessions.renew_ticket(row.item.browser_session_id)
        if grant is None:
            return Failure(FailureCode.CONFLICT, "浏览器已过期，请仅核实换绑结果")
        session, ticket = grant
        return Success(
            AutomationScreen(
                id=session.id,
                environment_id=session.environment_id,
                created_at=session.created_at,
                expires_at=session.expires_at,
                ticket=ticket,
            )
        )

    async def run_until_cancelled(self, stopped: asyncio.Event) -> None:
        while not stopped.is_set():
            try:
                await self.run_once()
            except Exception as error:
                _LOGGER.warning("Email rebinding tick failed: %s", error.__class__.__name__)
            try:
                await asyncio.wait_for(stopped.wait(), timeout=2)
            except TimeoutError:
                continue
