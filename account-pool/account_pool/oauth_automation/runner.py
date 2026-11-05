"""串行执行持久化授权任务；重启只核对现状，不重放登录、回调或结果未知的推理。"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal
from uuid import UUID

from account_pool.controlled_oauth_browser import ControlledOAuthBrowser
from account_pool.credential_ownership import OAUTH_AUTOMATION_LOCK
from account_pool.domain import EnvironmentRecord, EnvironmentStatus, utc_now
from account_pool.oauth_automation.models import ACTIVE, AutomationScreen, JobView, SessionSecrets, Stage, StoredJob
from account_pool.oauth_automation.service import AutomationRegistry
from account_pool.oauth_automation.worker import AutomationWorker
from account_pool.oauth_browser import OAuthBrowserAutomation, OAuthBrowserSessionGrant, OAuthBrowserSessionStatus
from account_pool.shared.result import Failure, FailureCode, Result, Success
from account_pool.shared.secrets import StateCipher

_LOGGER: Final = logging.getLogger(__name__)


def operation(job: StoredJob) -> str:
    return f"automation:{job.item.id.hex}:{job.item.attempt}"


@dataclass(frozen=True, slots=True)
class AutomationRunner:
    registry: AutomationRegistry
    browser: ControlledOAuthBrowser
    worker: AutomationWorker
    clock: Callable[[], datetime] = utc_now

    async def _human_deadline(self, row: StoredJob, reported: datetime) -> datetime:
        state: Final = await self.registry.repository.read()
        limit: Final = self.clock() + timedelta(seconds=state.settings.human_timeout_seconds)
        return min(reported, row.item.expires_at or limit, row.item.human_expires_at or limit, limit)

    async def _save(self, row: StoredJob) -> StoredJob:
        async with self.registry.repository.transaction() as tx:
            latest: Final = next(job for job in tx.snapshot.jobs if job.item.id == row.item.id)
            updated: Final = row.model_copy(
                update={
                    "item": row.item.model_copy(
                        update={
                            "updated_at": self.clock(),
                            "cancel_requested": latest.item.cancel_requested or row.item.cancel_requested,
                        }
                    )
                }
            )
            await tx.save(
                tx.snapshot.model_copy(
                    update={"jobs": tuple(updated if job.item.id == row.item.id else job for job in tx.snapshot.jobs)}
                )
            )
            return updated

    async def run_once(self) -> None:
        async with self.registry.repository.worker_lock() as acquired:
            if not acquired:
                return
            state: Final = await self.registry.repository.read()
            cleanup: Final = next(
                (job for job in state.jobs if job.cleanup_pending and job.item.stage not in ACTIVE), None
            )
            if cleanup is not None:
                await self._cleanup(cleanup)
                return
            current: Final = next((job for job in state.jobs if job.item.stage in ACTIVE), None)
            if current is not None:
                await self._advance(current)
                return
            if not state.settings.enabled or state.checked_image != self.registry.worker.image:
                return
            queued: Final = next((job for job in state.jobs if job.item.stage == "queued"), None)
            if queued is not None:
                await self._launch(queued)

    async def _launch(self, row: StoredJob) -> None:
        state: Final = await self.registry.repository.read()
        profile: Final = next(profile for profile in state.profiles if profile.item.id == row.item.profile_id)
        async with (
            self.registry.ownership.operation(OAUTH_AUTOMATION_LOCK),
            self.registry.ownership.operation(row.item.environment_id),
            self.browser.service.proxy_profile_operation(row.item.proxy_profile_id),
        ):
            records: Final = await self.registry.environments.list()
            sessions: Final = await asyncio.gather(
                *(self.browser.sessions.list_for_environment(record.id) for record in records)
            )
            if any(record.oauth_automation_operation is not None for record in records) or any(
                session.expires_at > self.clock()
                and session.status
                in {
                    OAuthBrowserSessionStatus.STARTING,
                    OAuthBrowserSessionStatus.ACTIVE,
                    OAuthBrowserSessionStatus.CALLBACK_PENDING,
                }
                for group in sessions
                for session in group
            ):
                return
            record: Final = await self.registry.environments.get(row.item.environment_id)
            if (
                record is None
                or record.auth_file_name
                or record.credential_fingerprints
                or not record.enabled
                or record.manual_cooldown
                or record.configuration_pending
                or record.proxy_profile_id != row.item.proxy_profile_id
                or record.observed_configuration_version != row.item.configuration_version
                or record.oauth_automation_operation is not None
                or not profile.item.enabled
            ):
                await self._save(
                    row.model_copy(
                        update={
                            "item": row.item.model_copy(
                                update={"stage": "failed", "message": "卡片或指纹状态已改变；未启动登录"}
                            )
                        }
                    )
                )
                return
            claimed: Final = await self._save(
                row.model_copy(
                    update={
                        "cleanup_pending": True,
                        "item": row.item.model_copy(
                            update={
                                "stage": "starting",
                                "message": "正在建立隔离浏览器",
                                "attempt": row.item.attempt + 1,
                                "expires_at": self.clock() + timedelta(seconds=state.settings.total_timeout_seconds),
                            }
                        ),
                    }
                )
            )
            saved: Final = await self.registry.environments.save_if_version(
                record.model_copy(
                    update={
                        "version": record.version + 1,
                        "oauth_automation_operation": operation(claimed),
                    }
                ),
                record.version,
            )
        if saved is None:
            await self._finish(claimed, "failed", "卡片被其他操作修改；未启动登录")
            return
        cipher: Final = StateCipher(self.registry.secrets)
        token: Final = secrets.token_urlsafe(32)

        async def reserved(grant: OAuthBrowserSessionGrant) -> None:
            await self._save(
                claimed.model_copy(
                    update={
                        "session_ciphertext": cipher.seal(
                            row.item.environment_id,
                            SessionSecrets(worker_token=token, ticket=grant.ticket).model_dump_json(),
                        ),
                        "item": claimed.item.model_copy(update={"browser_session_id": grant.session.id}),
                    }
                )
            )

        try:
            assert claimed.item.expires_at is not None
            async with asyncio.timeout(max(0.1, (claimed.item.expires_at - self.clock()).total_seconds())):
                started: Final = await self.browser.start(
                    row.item.environment_id,
                    operation_id=operation(claimed),
                    on_reserved=reserved,
                    automation=OAuthBrowserAutomation(
                        fingerprint_json=json.dumps(profile.data, separators=(",", ":")),
                        worker_token=token,
                        expires_at=claimed.item.expires_at,
                        human_timeout_seconds=state.settings.human_timeout_seconds,
                    ),
                )
                durable: Final = next(
                    job for job in (await self.registry.repository.read()).jobs if job.item.id == row.item.id
                )
                if isinstance(started, Failure):
                    await self._finish(durable, "failed", "浏览器启动失败；检查镜像自检、代理和显示服务")
                    return
                if durable.item.cancel_requested:
                    await self._finish(durable, "cancelled", "任务已取消")
                    return
                # 发送前落盘；进程崩溃后只查询 worker，不重复发送登录资料。
                await self._save(
                    durable.model_copy(
                        update={
                            "login_submitted": True,
                            "item": durable.item.model_copy(
                                update={"stage": "running", "message": "正在登录并处理验证器 2FA"}
                            ),
                        }
                    )
                )
                await self.worker.command(
                    started.value.session.id,
                    token,
                    "start",
                    credentials=cipher.open(row.item.environment_id, row.credential_ciphertext),
                )
        except Exception:
            latest: Final = next(
                job for job in (await self.registry.repository.read()).jobs if job.item.id == row.item.id
            )
            await self._finish(latest, "failed", "启动或任务通道失败；不会自动重发登录资料，请检查后重试")

    async def _advance(self, row: StoredJob) -> None:
        if row.item.cancel_requested:
            await self._finish(row, "cancelled", "任务已取消")
            return
        if row.item.expires_at is None or row.item.expires_at <= self.clock():
            await self._finish(row, "expired", "任务总时限已到；请重试以建立新 OAuth 会话")
            return
        if row.item.human_expires_at is not None and row.item.human_expires_at <= self.clock():
            await self._finish(row, "expired", "人工处理时限已到；已停止本次登录")
            return
        record: Final = await self.registry.environments.get(row.item.environment_id)
        if record is None or record.oauth_automation_operation != operation(row):
            await self._finish(row, "failed", "卡片归属发生变化；已停止")
            return
        if row.item.stage in {"validating", "verifying"} or record.oauth_state_consumed_at is not None:
            await self._validate(row)
            return
        if (
            row.item.stage == "starting"
            or not row.login_submitted
            or row.item.browser_session_id is None
            or row.session_ciphertext is None
        ):
            await self._finish(row, "failed", "启动被中断；不会恢复或重发结果未知的登录，请重试")
            return
        session: Final = await self.browser.sessions.get(row.item.browser_session_id)
        if session is None or not await self.browser.current_binding(session):
            await self._finish(row, "failed", "卡片、会话或出口绑定发生变化；已停止")
            return
        try:
            secret: Final = SessionSecrets.model_validate_json(
                StateCipher(self.registry.secrets).open(row.item.environment_id, row.session_ciphertext)
            )
            status: Final = await self.worker.command(row.item.browser_session_id, secret.worker_token, "status")
            if status.status in {"prepared", "failed", "cancelled", "expired"}:
                await self._finish(row, "failed", "浏览器未运行或已结束；不会重复提交登录")
                return
            stages: Final[dict[str, Stage]] = {
                "running": "running",
                "waiting": "waiting",
                "manual": "manual",
                "callback_received": "callback_pending",
            }
            stage: Final = stages[status.status]
            await self._save(
                row.model_copy(
                    update={
                        "item": row.item.model_copy(
                            update={
                                "stage": stage,
                                "message": "等待人工处理，接管后脚本将停止"
                                if stage in {"manual", "waiting"}
                                else "等待一次性 OAuth 回调"
                                if stage == "callback_pending"
                                else "正在登录并处理验证器 2FA",
                                "human_expires_at": await self._human_deadline(row, status.expires_at)
                                if stage in {"manual", "waiting"}
                                else None,
                            }
                        )
                    }
                )
            )
        except Exception:
            await self._finish(row, "failed", "浏览器通道不可达；已停止，不会自动重发登录")

    async def _validate(self, row: StoredJob) -> None:
        await self.browser.service.get_environment(row.item.environment_id)
        record: Final = await self.registry.environments.get(row.item.environment_id)
        if record is None or record.proxy_profile_id != row.item.proxy_profile_id:
            await self._finish(row, "verification_failed", "出口或卡片状态改变，未进行模型验收")
            return
        consumed: Final = row.item.callback_consumed or record.oauth_state_consumed_at is not None
        saved: Final = bool(record.auth_file_name and record.credential_fingerprints)
        updated: Final = await self._save(
            row.model_copy(
                update={
                    "item": row.item.model_copy(
                        update={
                            "callback_consumed": consumed,
                            "credentials_saved": saved,
                            "stage": "verifying" if row.verification_started else "validating",
                            "message": "回调已消费，正在确认凭据与同卡模型",
                        }
                    )
                }
            )
        )
        if not saved:
            if record.status in {EnvironmentStatus.ERROR, EnvironmentStatus.DELETING}:
                await self._finish(
                    updated, "verification_failed", "回调已消费但凭据尚未就绪；不会重放回调，请检查后重试"
                )
            return
        if record.credential_email is None or self.registry.identity(record.credential_email) != row.account_digest:
            async with self.registry.ownership.operation(record.id):
                current: Final = await self.registry.environments.get(record.id)
                if current is None or current != record or current.oauth_automation_operation != operation(row):
                    return
                # 失败状态必须留在卡片上；只结束任务会让错误账号继续经网关接单。
                blocked: Final = await self.registry.environments.save_if_version(
                    current.model_copy(
                        update={
                            "version": current.version + 1,
                            "enabled": False,
                            "status": EnvironmentStatus.DISABLED,
                            "desired_state": EnvironmentStatus.DISABLED,
                            "last_error": "自动化授权账号不匹配，卡片已停用，请核对凭据后重新授权",
                            "updated_at": self.clock(),
                        }
                    ),
                    current.version,
                )
                if blocked is None:
                    return
            await self._finish(updated, "verification_failed", "保存的账号与任务身份不一致，卡片已停用，请重新授权")
            return
        await self._verify(updated, record)

    async def _verify(self, row: StoredJob, record: EnvironmentRecord) -> None:
        if record.oauth_automation_operation != operation(row):
            await self._finish(row, "verification_failed", "卡片已不属于当前任务，未启动模型验收")
            return
        if row.verification_started:
            await self._finish(row, "verification_failed", "上次模型验收结果未知；请仅重试验收，不重新登录")
            return
        # 仅本任务的同卡内部探测使用临时快照，不解除普通 Key 的自动化门禁。
        endpoint: Final = self.browser.service.gateway_environment(
            record.model_copy(update={"oauth_automation_operation": None})
        )
        cooling: Final = {item.model for item in record.model_cooldowns if item.retry_at > self.clock()}
        model: Final = row.item.model or next((item for item in endpoint.enabled_models if item not in cooling), None)
        if model is None or model not in endpoint.enabled_models or model in cooling or not endpoint.routable:
            await self._finish(row, "verification_failed", "凭据已保存，但选定模型不可路由或仍在冷却；请仅重试验收")
            return
        checking: Final = await self._save(
            row.model_copy(
                update={
                    "verification_started": True,
                    "item": row.item.model_copy(
                        update={
                            "stage": "verifying",
                            "model": model,
                            "message": "凭据已保存，正在验证同卡 Chat 与 Responses 实际返回",
                        }
                    ),
                }
            )
        )
        try:
            assert row.item.expires_at is not None
            async with asyncio.timeout(max(0.1, (row.item.expires_at - self.clock()).total_seconds())):
                inference: Final = asyncio.create_task(self.worker.verify(endpoint, model))
                cancellation: Final = asyncio.create_task(self._wait_for_cancel(row.item.id))
                try:
                    done, _ = await asyncio.wait((inference, cancellation), return_when=asyncio.FIRST_COMPLETED)
                    accepted: Final = None if cancellation in done else await inference
                finally:
                    inference.cancel()
                    cancellation.cancel()
                    await asyncio.gather(inference, cancellation, return_exceptions=True)
            if accepted is None:
                await self._finish(checking, "cancelled", "任务已取消；已中止本地请求，上游是否已处理无法保证")
                return
            result: Final = checking.model_copy(
                update={
                    "item": checking.item.model_copy(
                        update={
                            "chat_verified": accepted.chat,
                            "responses_verified": accepted.responses,
                        }
                    )
                }
            )
            await self._finish(
                result,
                "completed" if accepted.chat and accepted.responses else "verification_failed",
                "同卡 Chat 与 Responses 验收通过"
                if accepted.chat and accepted.responses
                else f"凭据已保存，但模型推理未通过（{accepted.failure_detail or '结果未知'}）；请仅重试验收，不重新登录",
            )
        except Exception:
            await self._finish(checking, "verification_failed", "模型验收中断或超时，结果未知；请仅重试验收")

    async def _wait_for_cancel(self, job_id: UUID) -> None:
        while not next(
            row for row in (await self.registry.repository.read()).jobs if row.item.id == job_id
        ).item.cancel_requested:
            await asyncio.sleep(0.25)

    async def _finish(self, row: StoredJob, stage: Stage, message: str) -> None:
        finished: Final = await self._save(
            row.model_copy(update={"item": row.item.model_copy(update={"stage": stage, "message": message})})
        )
        if finished.item.cancel_requested and stage != "cancelled":
            cancelled: Final = await self._save(
                finished.model_copy(
                    update={
                        "item": finished.item.model_copy(
                            update={
                                "stage": "cancelled",
                                "message": "任务已取消；已经发出的上游请求结果可能未知",
                            }
                        )
                    }
                )
            )
            await self._cleanup(cancelled)
            return
        await self._cleanup(finished)

    async def _cleanup(self, row: StoredJob) -> None:
        try:
            session: Final = (
                None
                if row.item.browser_session_id is None
                else await self.browser.sessions.get(row.item.browser_session_id)
            )
            if (
                session is not None
                and session.status is OAuthBrowserSessionStatus.CALLBACK_PENDING
                and self.clock() < min(session.expires_at, row.item.expires_at or session.expires_at)
            ):
                return
            record: Final = await self.registry.environments.get(row.item.environment_id)
            if (
                record is not None
                and record.oauth_automation_operation == operation(row)
                and record.oauth_state_consumed_at is None
                and record.oauth_state is not None
            ):
                result: Final = await self.browser.service.cancel_oauth_session(record.id, operation_id=operation(row))
                if isinstance(result, Failure):
                    return
            async with self.registry.ownership.operation(row.item.environment_id):
                current: Final = await self.registry.environments.get(row.item.environment_id)
                if row.item.browser_session_id is not None:
                    await self.browser.sessions.transition(
                        row.item.browser_session_id, OAuthBrowserSessionStatus.CANCELLED
                    )
                    await self.browser.runtime.remove_oauth_browser(row.item.browser_session_id)
                    await self.browser.sessions.mark_cleaned(row.item.browser_session_id)
                if current is not None and current.oauth_automation_operation == operation(row):
                    unlocked: Final = await self.registry.environments.save_if_version(
                        current.model_copy(update={"version": current.version + 1, "oauth_automation_operation": None}),
                        current.version,
                    )
                    if unlocked is None:
                        return
            await self._save(row.model_copy(update={"cleanup_pending": False, "session_ciphertext": None}))
        except Exception:
            # 保留 cleanup_pending；未确认清理之前不启动下一个账号。
            return

    async def action(
        self, job_id: UUID, action: Literal["cancel", "retry", "takeover", "resume"], *, model: str | None = None
    ) -> Result[JobView]:
        if action == "cancel":
            return await self.registry.cancel(job_id)
        async with self.registry.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "任务正在推进，请稍后刷新再操作")
            row: Final = next(
                (job for job in (await self.registry.repository.read()).jobs if job.item.id == job_id), None
            )
            if row is None:
                return Failure(FailureCode.NOT_FOUND, "任务不存在")
            if action == "retry":
                return await self._retry(row, model)
            if (
                row.item.stage not in {"running", "waiting", "manual"}
                or row.session_ciphertext is None
                or row.item.browser_session_id is None
                or row.item.cancel_requested
            ):
                return Failure(FailureCode.CONFLICT, "浏览器未就绪或已结束，不能接管或交回")
            await self._advance(row)
            latest: Final = next(job for job in (await self.registry.repository.read()).jobs if job.item.id == job_id)
            if latest.item.stage not in {"running", "waiting", "manual"}:
                return Failure(FailureCode.CONFLICT, "浏览器状态已改变，请刷新任务")
            try:
                secret: Final = SessionSecrets.model_validate_json(
                    StateCipher(self.registry.secrets).open(row.item.environment_id, row.session_ciphertext)
                )
                acknowledged: Final = await self.worker.command(
                    row.item.browser_session_id, secret.worker_token, action
                )
                if acknowledged.status not in {"running", "manual", "waiting"}:
                    return Failure(FailureCode.CONFLICT, "浏览器已结束，请刷新任务")
                updated: Final = await self._save(
                    latest.model_copy(
                        update={
                            "item": latest.item.model_copy(
                                update={
                                    "stage": acknowledged.status,
                                    "message": "已停止脚本，可以人工输入"
                                    if acknowledged.status == "manual"
                                    else "已关闭人工输入，继续自动化"
                                    if acknowledged.status == "running"
                                    else "账号或页面无法确认，仍需人工处理",
                                    "human_expires_at": await self._human_deadline(latest, acknowledged.expires_at)
                                    if acknowledged.status != "running"
                                    else None,
                                }
                            )
                        }
                    )
                )
                return Success(updated.item)
            except Exception:
                await self._finish(latest, "failed", "接管通道失败；未确认脚本停止，禁止人工输入")
                return Failure(FailureCode.UPSTREAM, "接管通道不可用，任务已停止")

    async def _retry(self, row: StoredJob, model: str | None = None) -> Result[JobView]:
        state: Final = await self.registry.repository.read()
        if row.item.stage not in {"failed", "expired", "cancelled", "verification_failed"} or row.cleanup_pending:
            return Failure(FailureCode.CONFLICT, "任务尚未结束或清理未完成，不能重试")
        if not state.settings.enabled or state.checked_image != self.worker.image:
            return Failure(FailureCode.CONFLICT, "请先通过当前镜像自检并开启自动化")
        if any(
            job.item.id != row.item.id
            and (
                job.item.stage in ACTIVE
                or job.item.environment_id == row.item.environment_id
                and job.item.stage == "queued"
            )
            for job in state.jobs
        ):
            return Failure(FailureCode.CONFLICT, "已有运行任务或同卡排队任务，请等待后再重试")
        async with (
            self.registry.ownership.operation(OAUTH_AUTOMATION_LOCK),
            self.registry.ownership.operation(row.item.environment_id),
            self.browser.service.proxy_profile_operation(row.item.proxy_profile_id),
        ):
            records: Final = await self.registry.environments.list()
            record: Final = next((record for record in records if record.id == row.item.environment_id), None)
            if (
                record is None
                or record.status is EnvironmentStatus.DELETING
                or not record.enabled
                or record.manual_cooldown
                or record.configuration_pending
                or record.proxy_profile_id != row.item.proxy_profile_id
                or any(record.oauth_automation_operation for record in records)
            ):
                return Failure(FailureCode.CONFLICT, "卡片、代理或运行状态不允许重试")
            verify_only: Final = (
                row.item.callback_consumed
                or row.item.credentials_saved
                or bool(record.auth_file_name or record.credential_fingerprints)
            )
            if model is not None and (not verify_only or model not in record.enabled_models):
                return Failure(FailureCode.CONFLICT, "只能选择当前卡片已启用的模型重试验收，不重新登录")
            if verify_only and (
                not record.auth_file_name
                or not record.credential_fingerprints
                or record.credential_email is None
                or self.registry.identity(record.credential_email) != row.account_digest
            ):
                return Failure(
                    FailureCode.CONFLICT, "回调已经消费或凭据状态不明，请先核实已有认证；不会再次登录覆盖凭据"
                )
            retry: Final = row.model_copy(
                update={
                    "cleanup_pending": verify_only,
                    "session_ciphertext": None,
                    "login_submitted": False,
                    "verification_started": False,
                    "item": row.item.model_copy(
                        update={
                            "stage": "validating" if verify_only else "queued",
                            "message": "仅重试同卡模型验收，不重新登录" if verify_only else "等待建立全新 OAuth 会话",
                            "browser_session_id": None,
                            "cancel_requested": False,
                            "chat_verified": False,
                            "responses_verified": False,
                            "configuration_version": record.observed_configuration_version,
                            "model": model or row.item.model,
                            "attempt": row.item.attempt + 1 if verify_only else row.item.attempt,
                            "expires_at": self.clock() + timedelta(seconds=state.settings.total_timeout_seconds)
                            if verify_only
                            else None,
                            "human_expires_at": None,
                            "updated_at": self.clock(),
                        }
                    ),
                }
            )
            async with self.registry.repository.transaction() as tx:
                await tx.save(
                    tx.snapshot.model_copy(
                        update={"jobs": tuple(retry if job.item.id == row.item.id else job for job in tx.snapshot.jobs)}
                    )
                )
            if (
                verify_only
                and await self.registry.environments.save_if_version(
                    record.model_copy(
                        update={"version": record.version + 1, "oauth_automation_operation": operation(retry)}
                    ),
                    record.version,
                )
                is None
            ):
                await self._save(
                    retry.model_copy(
                        update={
                            "cleanup_pending": False,
                            "item": retry.item.model_copy(
                                update={"stage": "verification_failed", "message": "卡片已改变，未启动验收"}
                            ),
                        }
                    )
                )
                return Failure(FailureCode.CONFLICT, "卡片已改变，请刷新")
            return Success(retry.item)

    async def screen(self, job_id: UUID) -> Result[AutomationScreen]:
        async with self.registry.repository.worker_lock() as acquired:
            if not acquired:
                return Failure(FailureCode.CONFLICT, "任务正在推进，请稍后打开画面")
            row: Final = next(
                (job for job in (await self.registry.repository.read()).jobs if job.item.id == job_id), None
            )
            if row is None:
                return Failure(FailureCode.NOT_FOUND, "任务不存在")
            if row.item.stage in ACTIVE:
                await self._advance(row)
            current: Final = next(job for job in (await self.registry.repository.read()).jobs if job.item.id == job_id)
            if current.item.stage not in {"running", "waiting", "manual"} or current.item.browser_session_id is None:
                return Failure(FailureCode.CONFLICT, "浏览器未就绪或已结束，不开放画面")
            grant: Final = await self.browser.sessions.renew_ticket(current.item.browser_session_id)
            if grant is None:
                return Failure(FailureCode.CONFLICT, "浏览器会话已过期")
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
                _LOGGER.warning("OAuth automation tick failed: %s", error.__class__.__name__)
            try:
                await asyncio.wait_for(stopped.wait(), timeout=2)
            except TimeoutError:
                continue
