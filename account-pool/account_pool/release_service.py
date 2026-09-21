"""本模块串行执行备份、恢复和清理，先持久化恢复点再替换业务服务。"""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Final, Protocol

from account_pool.release_compatibility import RollbackEvidence, compare_evidence, configuration_checks, evidence_state
from account_pool.release_models import (
    ReleaseAction,
    ReleaseBackup,
    ReleaseCommands,
    ReleaseConfirmation,
    ReleaseJob,
    ReleasePair,
    ReleaseVersion,
    ReleaseView,
    RollbackAlternative,
    RollbackCheck,
    RollbackInspection,
)
from account_pool.release_store import DEFAULT_GUIDE, ReleaseError, ReleaseStore, file_hash, write_private


class ReleaseRuntime(Protocol):
    def current(self) -> ReleasePair: ...
    def discover(self) -> tuple[ReleasePair, ...]: ...
    def compose(self) -> bytes: ...
    def running_compose(self) -> bytes: ...
    def export(self, pair: ReleasePair, destination: Path) -> None: ...
    def fingerprint(self, pair: ReleasePair) -> str: ...
    def evidence(self, pair: ReleasePair) -> RollbackEvidence: ...
    def load(self, pair: ReleasePair, archive: Path) -> None: ...
    def apply(self, pair: ReleasePair, configuration: bytes) -> None: ...
    def pull(self, tag: str) -> ReleasePair: ...


class ReleaseService:
    def __init__(self, store: ReleaseStore, runtime: ReleaseRuntime, reserve_bytes: int = 1024**3) -> None:
        self.store: Final = store
        self.runtime: Final = runtime
        self.reserve_bytes: Final = reserve_bytes

    def view(self) -> ReleaseView:
        current: Final = self.current_or_none()
        problems: Final = () if current else ("无法识别当前运行的配套版本，请检查容器状态与部署项目名",)
        return self._view(current, problems)

    def current_or_none(self) -> ReleasePair | None:
        try:
            return self.runtime.current()
        except (ReleaseError, ValueError):
            return None

    def _view(self, current: ReleasePair | None, problems: tuple[str, ...]) -> ReleaseView:
        revision, guide = self.store.settings()
        entries: Final = self.store.entries()
        versions: Final = tuple(
            ReleaseVersion(
                pair=backup.pair,
                note=note,
                current=current is not None and backup.pair.id == current.id,
                backup=backup,
                available=self.available(backup),
                problem=None if self.available(backup) else "备份文件缺失或大小不符，请重新扫描备份",
            )
            for _, backup, note in entries
            if backup is not None
        )
        current_entry: Final = (
            (
                ReleaseVersion(
                    pair=current,
                    current=True,
                    note=next((note for identifier, _, note in entries if identifier == current.id), ""),
                ),
            )
            if current is not None and not any(version.current for version in versions)
            else ()
        )
        jobs: Final = self.store.jobs()
        return ReleaseView(
            current=current,
            versions=current_entry + versions,
            guide=guide,
            default_guide=DEFAULT_GUIDE,
            revision=revision,
            location=str(self.store.root / "backups"),
            free_bytes=shutil.disk_usage(self.store.root).free,
            job=jobs[0] if jobs else None,
            problems=problems,
        )

    def available(self, backup: ReleaseBackup) -> bool:
        directory: Final = self.store.path(backup.pair.id)
        archive: Final = directory / "images.tar.gz"
        config: Final = directory / "compose.json"
        return (
            archive.is_file()
            and not archive.is_symlink()
            and archive.stat().st_size == backup.archive_bytes
            and config.is_file()
            and not config.is_symlink()
        )

    def verify(self, backup: ReleaseBackup) -> Path:
        directory: Final = self.store.path(backup.pair.id)
        if not self.available(backup):
            raise ReleaseError("备份文件缺失或大小不符，未切换版本")
        if (
            file_hash(directory / "images.tar.gz") != backup.archive_sha256
            or file_hash(directory / "compose.json") != backup.compose_sha256
        ):
            raise ReleaseError("备份校验失败，文件可能损坏，未切换版本")
        return directory

    def prepare(self, action: ReleaseAction, actor: str) -> ReleaseConfirmation:
        if action.action != "apply" and (action.force or action.force_acknowledgement):
            raise ReleaseError("只有程序回退支持强制确认")
        if not action.force and action.force_acknowledgement:
            raise ReleaseError("普通回退不接受强制确认内容")
        view: Final = self.view()
        if view.job and view.job.status in ("queued", "running"):
            raise ReleaseError("已有任务正在执行，请等待完成")
        if view.revision != action.revision:
            raise ReleaseError("页面数据已更新，请刷新后重试")
        if action.action in ("apply", "delete", "note"):
            selected: Final = next((version for version in view.versions if version.pair.id == action.version_id), None)
            if selected is None:
                raise ReleaseError("版本不存在")
            if action.action in ("apply", "delete") and selected.current:
                raise ReleaseError("当前运行版本不能重复应用或删除")
            if action.action in ("apply", "delete") and view.current is None:
                raise ReleaseError("无法识别当前版本，暂停应用和删除")
            if action.action == "apply" and not selected.available:
                raise ReleaseError("该版本没有完整的镜像备份")
        if action.action == "deploy" and (not action.tag or view.current is None):
            raise ReleaseError("请指定新版本 commit 标签并检查当前运行状态")
        if action.action == "recover" and not (
            view.job and view.job.status == "failed" and view.job.phase == "需要人工恢复" and view.job.recovery_id
        ):
            raise ReleaseError("没有需要人工恢复的失败任务")
        if action.action in ("apply", "delete", "scan", "deploy", "recover") and action.text:
            raise ReleaseError("此操作不接受说明内容")
        if action.action not in ("apply", "delete", "note") and action.version_id is not None:
            raise ReleaseError("此操作不接受目标版本")
        if action.action != "deploy" and action.tag is not None:
            raise ReleaseError("此操作不接受镜像标签")
        if action.action == "note" and len(action.text) > 500:
            raise ReleaseError("版本备注最多 500 字")
        if action.action == "apply" and view.current is not None:
            report, state = self.inspect_rollback(view.current, action.version_id or "", alternatives=True)
            if self.runtime.current().id != view.current.id or self.store.settings()[0] != action.revision:
                raise ReleaseError("检查期间版本或备份发生变化，请重新检查")
            if action.force and (not report.force_allowed or action.force_acknowledgement != report.target_commit):
                raise ReleaseError("强制回退条件不满足，或确认版本不一致；请重新检查")
            if report.status != "compatible" and not action.force:
                return ReleaseConfirmation(
                    token="", action=action, delay_seconds=5, current_commit=view.current.commit, rollback=report
                )
            prepared: Final = self.store.prepare(action, actor, view.current.id, view.current.commit, state)
            return prepared.model_copy(update={"rollback": report})
        return self.store.prepare(
            action, actor, view.current.id if view.current else None, view.current.commit if view.current else None
        )

    def inspect_rollback(
        self,
        current: ReleasePair,
        version_id: str,
        *,
        alternatives: bool = False,
    ) -> tuple[RollbackInspection, str]:
        saved: Final = self.store.backup(version_id)
        if saved is None:
            raise ReleaseError("目标备份不存在")
        configuration: Final = self.runtime.running_compose()
        try:
            evidence: Final = self.runtime.evidence(current)
        except (ReleaseError, ValueError):
            return RollbackInspection(
                current_commit=current.commit,
                target_commit=saved.pair.commit,
                status="unverified",
                checks=(
                    RollbackCheck(
                        key="current",
                        title="当前版本检查依据",
                        status="unverified",
                        detail="无法读取当前镜像的结构与读写代码，请检查部署服务后重试。",
                    ),
                ),
                impacts=("当前版本检查未完成，暂时无法比较功能影响。",),
            ), ""
        checks, impacts, state = self._inspect_backup(saved, evidence, configuration)
        status: Final = (
            "blocked"
            if any(item.status == "blocked" for item in checks)
            else ("unverified" if any(item.status == "unverified" for item in checks) else "compatible")
        )
        candidates: Final = (
            tuple(
                (backup, note)
                for _, backup, note in self.store.entries()
                if backup and backup.pair.id not in (current.id, version_id) and self.available(backup)
            )
            if alternatives
            else ()
        )
        # 每次最多核对五个近期备份，未检查的版本不列为推荐，避免拖住管理接口。
        recommendations: Final = tuple(
            RollbackAlternative(version_id=backup.pair.id, commit=backup.pair.commit, note=note)
            for backup, note in candidates[:5]
            for candidate_checks, _, _ in (self._inspect_backup(backup, evidence, configuration),)
            if all(item.status == "compatible" for item in candidate_checks)
        )
        return RollbackInspection(
            current_commit=current.commit,
            target_commit=saved.pair.commit,
            status=status,
            force_allowed=bool(state) and status == "unverified" and all(item.status != "blocked" for item in checks),
            checks=checks,
            impacts=impacts,
            alternatives=recommendations,
            alternatives_checked=min(5, len(candidates)),
            alternatives_total=len(candidates),
        ), state

    def _inspect_backup(
        self,
        saved: ReleaseBackup,
        current: RollbackEvidence,
        configuration: bytes,
    ) -> tuple[tuple[RollbackCheck, ...], tuple[str, ...], str]:
        try:
            directory: Final = self.verify(saved)
        except ReleaseError as error:
            return (
                (RollbackCheck(key="backup", title="镜像备份完整性", status="blocked", detail=str(error)),),
                ("备份无法读取，尚不能确认目标版本的功能差异。",),
                "",
            )
        try:
            try:
                target: Final = self.runtime.evidence(saved.pair)
            except (ReleaseError, ValueError):
                self.runtime.load(saved.pair, directory / "images.tar.gz")
                return self._inspect_loaded_backup(saved, current, configuration, directory)
            return self._inspect_loaded_backup(saved, current, configuration, directory, target)
        except (ReleaseError, ValueError, KeyError):
            return (
                (
                    RollbackCheck(
                        key="evidence",
                        title="镜像兼容性证据",
                        status="unverified",
                        detail="目标镜像检查依据无法读取，不能强制跳过备份和启动检查。",
                    ),
                ),
                ("目标镜像缺少可读取的检查依据，无法确认具体功能影响。",),
                "",
            )

    def _inspect_loaded_backup(
        self,
        saved: ReleaseBackup,
        current: RollbackEvidence,
        configuration: bytes,
        directory: Path,
        evidence: RollbackEvidence | None = None,
    ) -> tuple[tuple[RollbackCheck, ...], tuple[str, ...], str]:
        try:
            target: Final = evidence if evidence is not None else self.runtime.evidence(saved.pair)
            target_config: Final = (directory / "compose.json").read_bytes()
            checks: Final = (
                RollbackCheck(
                    key="backup",
                    title="镜像备份完整性",
                    status="compatible",
                    detail="两份镜像归档与部署配置的校验和通过。",
                ),
                *compare_evidence(current, target),
                *configuration_checks(configuration, target_config),
                RollbackCheck(
                    key="fingerprint",
                    title="备份结构记录",
                    status="compatible",
                    detail="已从目标镜像重新提取检查依据；旧备份的历史指纹仅作记录，不覆盖原备份。",
                ),
            )
            missing: Final = tuple(
                f"{feature}：目标镜像未检测到对应实现，回退后该功能可能不可用。"
                for feature in current.features
                if feature not in target.features
            )
            impacts: Final = missing + (
                "功能检查覆盖密钥显示、完整日志、请求耗时、卡片路由与版本管理接口；其他历史行为差异尚未验证。",
                "回退时会短暂中断请求，健康检查失败会尝试恢复原运行版本。",
                "仅替换程序镜像，继续使用当前部署配置、数据库、认证文件和日志，不恢复数据快照。",
            )
            return (
                checks,
                impacts,
                evidence_state(current, configuration, evidence_state(target, target_config, saved.archive_sha256)),
            )
        except (ReleaseError, ValueError, KeyError):
            return (
                (
                    RollbackCheck(
                        key="evidence",
                        title="镜像兼容性证据",
                        status="unverified",
                        detail="本机目标镜像或结构文件无法读取，请先扫描备份并确认镜像可用。",
                    ),
                ),
                ("目标镜像缺少可读取的检查依据，无法确认具体功能影响。",),
                "",
            )

    def execute(self, token: str, actor: str) -> ReleaseJob:
        view: Final = self.view()
        return self.store.consume(token, actor, view.current.id if view.current else None)

    def backup(self, pair: ReleasePair, configuration: bytes, *, imported: bool = False) -> ReleaseBackup:
        existing: Final = self.store.backup(pair.id)
        if existing:
            try:
                self.verify(existing)
                refreshed_fingerprint: Final = self.runtime.fingerprint(pair)
                if (
                    imported or hashlib.sha256(configuration).hexdigest() == existing.compose_sha256
                ) and refreshed_fingerprint == existing.schema_fingerprint:
                    return existing
                # 同一镜像的配置可能已修改，只更新运行配置，不重复导出大归档。
                refreshed: Final = existing.model_copy(
                    update={
                        "compose_sha256": existing.compose_sha256
                        if imported
                        else hashlib.sha256(configuration).hexdigest(),
                        "configuration_source": existing.configuration_source if imported else "running",
                        "schema_fingerprint": refreshed_fingerprint,
                    }
                )
                if not imported:
                    write_private(self.store.path(pair.id) / "compose.json", configuration)
                write_private(self.store.path(pair.id) / "manifest.json", refreshed.model_dump_json().encode())
                self.store.save_backup(refreshed)
                return refreshed
            except ReleaseError:
                pass
        required: Final = sum(image.size for image in pair.images) + self.reserve_bytes
        if shutil.disk_usage(self.store.root).free < required:
            raise ReleaseError("可用空间不足，备份已停止，当前运行版本保持不变")
        directory: Final = self.store.path(pair.id)
        directory.mkdir(exist_ok=True, mode=0o700)
        pending: Final = directory / "images.pending.gz"
        try:
            fingerprint: Final = self.runtime.fingerprint(pair)
            self.runtime.export(pair, pending)
            if not pending.is_file() or pending.stat().st_size == 0:
                raise ReleaseError("镜像归档为空，备份失败")
            archive_hash: Final = file_hash(pending)
            pending.replace(directory / "images.tar.gz")
            write_private(directory / "compose.json", configuration)
            record: Final = ReleaseBackup(
                pair=pair,
                created_at=time.time(),
                archive_bytes=(directory / "images.tar.gz").stat().st_size,
                archive_sha256=archive_hash,
                compose_sha256=file_hash(directory / "compose.json"),
                schema_fingerprint=fingerprint,
                configuration_source="imported_current" if imported else "running",
            )
            write_private(directory / "manifest.json", record.model_dump_json().encode())
            self.store.save_backup(record)
            return record
        finally:
            pending.unlink(missing_ok=True)

    def phase(self, job: ReleaseJob, phase: str, recovery_id: str | None = None) -> ReleaseJob:
        updated: Final = job.model_copy(
            update={"phase": phase, "updated_at": time.time(), "recovery_id": recovery_id or job.recovery_id}
        )
        self.store.save_job(updated)
        return updated

    def restore(self, backup: ReleaseBackup) -> None:
        directory: Final = self.verify(backup)
        self.runtime.load(backup.pair, directory / "images.tar.gz")
        self.runtime.apply(backup.pair, (directory / "compose.json").read_bytes())

    def run(self, job: ReleaseJob) -> None:
        active: Final = job.model_copy(update={"status": "running", "updated_at": time.time()})
        self.store.save_job(active)
        try:
            self._perform(active)
        except Exception as error:  # noqa: BLE001  # 保存故障状态并尝试恢复，不能让后台任务静默消失。
            latest: Final = next(item for item in self.store.jobs() if item.id == active.id)
            safe: Final = (
                str(error)
                if isinstance(error, ReleaseError)
                else "执行失败，请检查部署管理服务；未显示可能包含凭据的底层错误"
            )
            if latest.recovery_id and latest.phase in ("替换服务并检查健康", "恢复原运行版本"):
                self._recover(latest, safe)
            else:
                self.store.save_job(
                    latest.model_copy(update={"status": "failed", "message": safe, "updated_at": time.time()})
                )
            return
        completed: Final = next(item for item in self.store.jobs() if item.id == active.id)
        self.store.save_job(
            completed.model_copy(update={"status": "succeeded", "phase": "完成", "updated_at": time.time()})
        )

    def _perform(self, job: ReleaseJob) -> None:
        action: Final = job.action
        if action.action in ("note", "guide"):
            self.store.edit(action)
            return
        if action.action == "recover":
            observed: Final = self.current_or_none()
            if (observed.id if observed else None) != job.expected_current_id:
                raise ReleaseError("确认后运行版本已变化，请重新操作")
            failed: Final = next((item for item in self.store.jobs() if item.id != job.id), None)
            saved_recovery: Final = (
                self.store.backup(failed.recovery_id)
                if failed and failed.status == "failed" and failed.phase == "需要人工恢复" and failed.recovery_id
                else None
            )
            if saved_recovery is None:
                raise ReleaseError("没有可用的恢复点")
            self.phase(job, "恢复原运行版本", saved_recovery.pair.id)
            self.restore(saved_recovery)
            return
        current: Final = self.runtime.current()
        if current.id != job.expected_current_id:
            raise ReleaseError("确认后运行版本已变化，请重新操作")
        if action.action == "scan":
            self.phase(job, "备份当前运行版本")
            self.backup(current, self.runtime.running_compose())
            for pair in self.runtime.discover():
                if pair.id != current.id:
                    self.phase(job, f"备份历史镜像 {pair.commit[:10]}")
                    self.backup(pair, self.runtime.running_compose(), imported=True)
            return
        if action.action == "delete":
            if not action.version_id or action.version_id == current.id:
                raise ReleaseError("当前运行版本不能删除")
            self.phase(job, "删除镜像备份文件")
            directory: Final = self.store.path(action.version_id)
            if directory.exists():
                # 只删除受控备份目录的固定文件，不递归跟随路径或符号链接。
                expected: Final = {"images.tar.gz", "images.pending.gz", "compose.json", "manifest.json"}
                if any(path.name not in expected or path.is_dir() or path.is_symlink() for path in directory.iterdir()):
                    raise ReleaseError("备份目录包含异常文件，停止删除")
                for path in directory.iterdir():
                    path.unlink()
                directory.rmdir()
            self.store.remove(action.version_id)
            return
        target, configuration = self._target(job, current)
        if target.id == current.id:
            raise ReleaseError("目标版本已在运行")
        self.phase(job, "备份当前运行版本")
        recovery: Final = self.backup(current, self.runtime.running_compose())
        # 新版若会改变数据库结构，故障时不能自动切换旧镜像，因此先阻止这类在线替换。
        if action.action == "deploy" and self.runtime.fingerprint(target) != recovery.schema_fingerprint:
            raise ReleaseError("版本间数据库结构或持久化配置格式不同，已备份当前版本；请先完成数据兼容处理")
        if self.runtime.current().id != current.id:
            raise ReleaseError("备份期间运行版本已被外部操作更改，停止替换")
        if action.action == "apply":
            self._require_rollback_check(job, current)
        self.phase(job, "替换服务并检查健康", recovery.pair.id)
        self.runtime.apply(target, configuration)

    def _target(self, job: ReleaseJob, current: ReleasePair) -> tuple[ReleasePair, bytes]:
        action: Final = job.action
        if action.action == "deploy":
            self.phase(job, "拉取新版本镜像")
            return self.runtime.pull(action.tag or ""), self.runtime.compose()
        saved: Final = self.store.backup(action.version_id or "")
        if saved is None:
            raise ReleaseError("目标备份不存在")
        self._require_rollback_check(job, current)
        self.phase(job, "校验并导入备份镜像")
        directory: Final = self.verify(saved)
        self.runtime.load(saved.pair, directory / "images.tar.gz")
        return saved.pair, self.runtime.running_compose()

    def _require_rollback_check(self, job: ReleaseJob, current: ReleasePair) -> None:
        report, state = self.inspect_rollback(current, job.action.version_id or "")
        accepted: Final = (
            report.force_allowed and job.action.force_acknowledgement == report.target_commit
            if job.action.force
            else report.status == "compatible"
        )
        if not accepted or not state or state != job.rollback_state:
            raise ReleaseError("回退检查未通过或配置已变化，请重新检查并确认；未替换服务")

    def _recover(self, job: ReleaseJob, message: str) -> None:
        self.phase(job, "恢复原运行版本")
        backup: Final = self.store.backup(job.recovery_id or "")
        try:
            if backup is None:
                raise ReleaseError("恢复点不存在")
            self.restore(backup)
        except Exception:  # noqa: BLE001  # 自动恢复失败后保留恢复点，交由服务器命令继续处理。
            self.store.save_job(
                job.model_copy(
                    update={
                        "status": "failed",
                        "phase": "需要人工恢复",
                        "message": message + "；自动恢复失败，请使用服务器恢复入口",
                        "updated_at": time.time(),
                    }
                )
            )
            return
        self.store.save_job(
            job.model_copy(
                update={"status": "recovered", "phase": "已恢复原版本", "message": message, "updated_at": time.time()}
            )
        )

    def recover_interrupted(self) -> None:
        for job in self.store.jobs():
            if job.status != "running":
                continue
            if job.recovery_id and job.phase in ("替换服务并检查健康", "恢复原运行版本"):
                self._recover(job, "部署管理服务重启，已恢复操作前版本")
            else:
                self.store.save_job(
                    job.model_copy(
                        update={"status": "interrupted", "message": "任务被中断，请重新操作", "updated_at": time.time()}
                    )
                )

    def commands(self, version_id: str) -> ReleaseCommands:
        selected: Final = next((version for version in self.view().versions if version.pair.id == version_id), None)
        if selected is None:
            raise ReleaseError("版本不存在", 404)
        commit: Final = selected.pair.commit
        prefix: Final = "# 在项目仓库中执行（PowerShell）\ngit rev-parse --show-toplevel\nif ($LASTEXITCODE -ne 0) { throw '请先进入项目仓库' }\nif (git status --porcelain) { throw '请先提交或 git stash 保存修改' }\n"
        branch: Final = prefix + f"git switch -c codex/fix-{commit[:10]} {commit}\n"
        revert: Final = prefix + (
            f"if ((git rev-parse HEAD) -eq '{commit}') {{ throw '代码已在目标版本，无需撤销' }}\n"
            f"git merge-base --is-ancestor {commit} HEAD\nif ($LASTEXITCODE -ne 0) {{ throw '目标版本不是当前分支祖先，请新建修复分支' }}\n"
            f"if (git rev-list --merges {commit}..HEAD) {{ throw '范围包含合并提交，请使用创建修复分支' }}\n"
            f"git log --oneline {commit}..HEAD\n"
            "if ((Read-Host '将撤销以上提交，输入 REVERT 继续') -cne 'REVERT') { throw '已取消' }\n"
            f"git revert --no-commit {commit}..HEAD\n"
            "if ($LASTEXITCODE -ne 0) { throw '撤销有冲突，请解决后继续，或执行 git revert --abort' }\n"
            f"git commit -m 'revert: 恢复到版本 {commit[:10]} 的代码'\n"
        )
        return ReleaseCommands(branch=branch, revert=revert)
