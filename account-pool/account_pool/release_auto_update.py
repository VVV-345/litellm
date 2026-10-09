"""本模块在发布器队列空闲时检查镜像，复用受控部署，不替换自身或绕过兼容性检查。"""

from typing import Final, Protocol

from account_pool.release_models import AutoUpdateSettings, AutoUpdateView, ReleaseCandidate, ReleasePair
from account_pool.release_service import ReleaseService
from account_pool.release_store import ReleaseError


class ReleaseSource(Protocol):
    def latest(self, current: ReleasePair) -> ReleaseCandidate | None: ...


class AutoUpdater:
    def __init__(self, service: ReleaseService, source: ReleaseSource) -> None:
        self.service: Final = service
        self.source: Final = source

    def view(self) -> AutoUpdateView:
        state: Final = self.service.store.auto_update()
        current: Final = self.service.current_or_none()
        job: Final = next((item for item in self.service.store.jobs() if item.id == state.last_job_id), None)
        return AutoUpdateView.model_validate(
            {
                **state.model_dump(),
                "current_commit": current.commit if current else None,
                "database_backups_enabled": self.service.database is not None,
                **({"status": "deploying", "message": job.phase} if job and job.status == "running" else {}),
            }
        )

    def configure(self, settings: AutoUpdateSettings, actor: str) -> AutoUpdateView:
        self.service.store.reconcile_auto_job()
        if settings.enabled and self.service.database is None:
            raise ReleaseError("自动部署需要先配置数据库备份")
        self.service.store.configure_auto_update(settings, actor)
        return self.view()

    def check(self) -> AutoUpdateView:
        self.service.store.request_auto_check()
        return self.view()

    def tick(self) -> None:
        self.service.store.reconcile_auto_job()
        checked: Final = self.service.store.claim_auto_check()
        if checked is None:
            return
        try:
            current: Final = self.service.runtime.current()
            candidate: Final = self.source.latest(current)
            if candidate is None:
                self.service.store.finish_auto_check(checked, "waiting", "开发分支尚无完整成功发布的新镜像")
                return
            running: Final = {item.service: item.image_id for item in current.images}
            same: Final = all(running.get(item.service) in (item.image_id, item.digest) for item in candidate.images)
            if same:
                self.service.store.finish_auto_check(checked, "current", "远程镜像与运行镜像相同，已跳过", candidate)
                return
            if not checked.enabled:
                self.service.store.finish_auto_check(checked, "available", "发现新镜像，自动部署未开启", candidate)
                return
            if self.service.database is None:
                raise ReleaseError("数据库备份未配置，未启动自动部署")
            queued: Final = self.service.store.enqueue_auto_update(checked, candidate, current.id)
            if queued is None:
                self.service.store.finish_auto_check(
                    checked, "waiting", "设置或发布状态已变化，本轮未启动部署", candidate
                )
        except Exception as error:
            # 不返回 HTTP/Docker 原始响应；失败检查保留旧业务并等待下轮，不静默终止队列。
            message: Final = str(error) if isinstance(error, ReleaseError) else "镜像检查失败，请检查发布器日志和网络"
            self.service.store.finish_auto_check(checked, "error", message)
