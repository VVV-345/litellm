"""本模块持久化版本备注、确认票据与执行记录，镜像归档保存在独立目录。"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from account_pool.release_models import (
    AutoUpdateSettings,
    AutoUpdateState,
    ReleaseAction,
    ReleaseBackup,
    ReleaseCandidate,
    ReleaseConfirmation,
    ReleaseJob,
)

DEFAULT_GUIDE: Final = """更新与回退
正常更新前自动备份当前运行版本；已有完整镜像归档不重复导出。启用数据库备份时会暂停业务并刷新两库快照，失败不替换旧快照。
选择备份版本，点击检查并回退，核对兼容性、功能影响与可选版本，通过后等待 5 秒确认。恢复直接使用备份镜像，不需要重新构建。
删除会真正删除备份文件，等待 10 秒后确认。当前运行版本不能删除。

从旧代码继续修改
先保存未提交的修改。推荐复制“创建修复分支”命令，从选中版本的 commit 开始修改。
git revert 适合在原分支撤销改动，保留历史。命令会先核对起点和提交范围；遇到合并提交请改用新分支。
修改并测试后提交新版本，正常构建发布。服务器回退不会自动切换电脑上的代码。
旧分支没有自动构建时，在 GitHub Actions 选择新版构建工作流，将 source_ref 填为修复分支或完整 commit。

数据与恢复
默认镜像备份不包含数据库。ACCOUNT_POOL_RELEASE_DATABASE_BACKUPS=true 后，扫描当前版本与兼容切换前会创建两库逻辑快照并试还原；历史镜像导入不补造快照。
普通回退保留当前业务数据。恢复程序与数据是独立操作，须输入 RESTORE 加完整快照编号；两库回到快照时刻，之后数据不保留在运行库。认证文件与磁盘日志不恢复。
数据库恢复前先保存当前镜像和两库数据；失败时尝试恢复该恢复点，失败或中断未解决时禁止其他发布操作。快照不能代替迁移兼容性审查。
历史镜像导入使用导入时的部署配置，页面会标明配置来源。
回退到没有版本管理页面的旧版本时，可在服务器部署目录运行 python3 releasectl.py status 或 apply 版本ID。
"""


class ReleaseError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        self.status: Final = status
        super().__init__(message)


class ReleaseDownloadError(ReleaseError):
    """镜像下载尚未影响旧服务，允许下一轮重新检查后重试。"""


class ReleaseStore:
    def __init__(self, root: Path, clock: Callable[[], float] = time.time) -> None:
        self.root: Final = root.resolve()
        self.clock: Final = clock
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        (self.root / "backups").mkdir(exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, revision INTEGER, guide TEXT);"
                "CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, backup TEXT, note TEXT NOT NULL DEFAULT '');"
                "CREATE TABLE IF NOT EXISTS confirmations (token TEXT PRIMARY KEY, actor TEXT, payload TEXT, "
                "current_id TEXT, not_before REAL, expires REAL);"
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT);"
                "CREATE TABLE IF NOT EXISTS rollback_checks (token TEXT PRIMARY KEY, state TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS auto_update (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);"
            )
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, 0, ?)", (DEFAULT_GUIDE,))
            db.execute("INSERT OR IGNORE INTO auto_update VALUES (1, ?)", (AutoUpdateState().model_dump_json(),))

    @staticmethod
    def _auto_state(db: sqlite3.Connection) -> AutoUpdateState:
        raw: Final = TypeAdapter(tuple[str]).validate_python(
            db.execute("SELECT payload FROM auto_update WHERE id=1").fetchone()
        )[0]
        return AutoUpdateState.model_validate_json(raw)

    @staticmethod
    def _save_auto(db: sqlite3.Connection, state: AutoUpdateState) -> AutoUpdateState:
        db.execute("UPDATE auto_update SET payload=? WHERE id=1", (state.model_dump_json(),))
        return state

    @staticmethod
    def _busy(db: sqlite3.Connection) -> bool:
        return (
            db.execute(
                "SELECT 1 FROM jobs WHERE json_extract(payload,'$.status') IN ('queued','running') LIMIT 1"
            ).fetchone()
            is not None
        )

    def auto_update(self) -> AutoUpdateState:
        with self.connection() as db:
            return self._auto_state(db)

    def recover_auto_check(self) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state: Final = self._auto_state(db)
            if state.status == "checking":
                self._save_auto(
                    db,
                    state.model_copy(
                        update={
                            "status": "error",
                            "revision": state.revision + 1,
                            "message": "上次镜像检查被中断，未替换服务",
                            "check_requested": False,
                            "next_check_at": self.clock() if state.enabled else None,
                        }
                    ),
                )

    def configure_auto_update(self, settings: AutoUpdateSettings, actor: str) -> AutoUpdateState:
        if settings.enabled and not settings.acknowledge_downtime:
            raise ReleaseError("开启自动部署必须确认可能中断请求")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state: Final = self._auto_state(db)
            if settings.revision != state.revision:
                raise ReleaseError("自动更新设置已变化，请刷新后重新保存")
            return self._save_auto(
                db,
                state.model_copy(
                    update={
                        "enabled": settings.enabled,
                        "interval_minutes": settings.interval_minutes,
                        "revision": state.revision + 1,
                        "status": "waiting" if settings.enabled else "disabled",
                        "message": "",
                        "next_check_at": self.clock() if settings.enabled else None,
                        "check_requested": False,
                        "authorized_by": actor,
                    }
                ),
            )

    def request_auto_check(self) -> AutoUpdateState:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state: Final = self._auto_state(db)
            if self._busy(db) or state.status == "checking":
                raise ReleaseError("已有检查或发布任务，请等待完成")
            if state.last_checked_at is not None and self.clock() - state.last_checked_at < 60:
                raise ReleaseError("请至少间隔一分钟再手动检查")
            return self._save_auto(db, state.model_copy(update={"check_requested": True}))

    def claim_auto_check(self) -> AutoUpdateState | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state: Final = self._auto_state(db)
            now: Final = self.clock()
            due: Final = state.enabled and (state.next_check_at is None or state.next_check_at <= now)
            if self._busy(db) or state.last_job_id or not (due or state.check_requested):
                return None
            return self._save_auto(
                db,
                state.model_copy(
                    update={
                        "status": "checking",
                        "last_checked_at": now,
                        "check_requested": False,
                        "next_check_at": now + state.interval_minutes * 60 if state.enabled else None,
                        "message": "正在检查已发布镜像",
                    }
                ),
            )

    def finish_auto_check(
        self, checked: AutoUpdateState, status: str, message: str, candidate: ReleaseCandidate | None = None
    ) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state: Final = self._auto_state(db)
            if state.revision == checked.revision:
                self._save_auto(
                    db,
                    AutoUpdateState.model_validate(
                        {
                            **state.model_dump(),
                            "status": status,
                            "message": message,
                            "candidate_commit": candidate.commit if candidate else None,
                        }
                    ),
                )

    def enqueue_auto_update(
        self, checked: AutoUpdateState, candidate: ReleaseCandidate, current_id: str
    ) -> ReleaseJob | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state: Final = self._auto_state(db)
            # 用户关闭开关或手动回退后，网络请求返回的旧检查结果不能重新授权部署。
            if not state.enabled or state.revision != checked.revision or self._busy(db):
                return None
            latest: Final = TypeAdapter[tuple[str] | None](tuple[str] | None).validate_python(
                db.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 1").fetchone()
            )
            if latest and ReleaseJob.model_validate_json(latest[0]).phase == "需要人工恢复":
                raise ReleaseError("上次发布需要人工恢复，不能自动部署")
            revision: Final = TypeAdapter(tuple[int]).validate_python(
                db.execute("SELECT revision FROM settings WHERE id=1").fetchone()
            )[0]
            now: Final = self.clock()
            job: Final = ReleaseJob(
                id=secrets.token_hex(16),
                status="queued",
                phase="自动更新等待执行",
                action=ReleaseAction(action="deploy", tag=candidate.commit, revision=revision, candidate=candidate),
                expected_current_id=current_id,
                created_at=now,
                updated_at=now,
                automatic=True,
            )
            db.execute("INSERT INTO jobs VALUES (?,?)", (job.id, job.model_dump_json()))
            self._save_auto(
                db,
                state.model_copy(
                    update={
                        "status": "queued",
                        "candidate_commit": candidate.commit,
                        "last_job_id": job.id,
                        "message": "已固定镜像摘要，等待受控部署",
                    }
                ),
            )
            return job

    def reconcile_auto_job(self) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state: Final = self._auto_state(db)
            if not state.last_job_id:
                return
            row: Final = TypeAdapter[tuple[str] | None](tuple[str] | None).validate_python(
                db.execute("SELECT payload FROM jobs WHERE id=?", (state.last_job_id,)).fetchone()
            )
            job: Final = ReleaseJob.model_validate_json(row[0]) if row else None
            if job and job.status in ("queued", "running"):
                return
            succeeded: Final = job is not None and job.status == "succeeded"
            retryable: Final = job is not None and job.retryable and job.status == "failed"
            keep_enabled: Final = state.enabled and (succeeded or retryable)
            self._save_auto(
                db,
                state.model_copy(
                    update={
                        "last_job_id": None,
                        "enabled": keep_enabled,
                        "revision": state.revision if succeeded or retryable else state.revision + 1,
                        "status": ("waiting" if state.enabled else "disabled")
                        if succeeded
                        else ("error" if retryable else "paused"),
                        "message": "更新完成"
                        if succeeded
                        else (
                            (job.message if job else "发布记录缺失")
                            + ("；等待下轮检查" if keep_enabled else "；自动更新已暂停")
                        ),
                        "next_check_at": self.clock() + state.interval_minutes * 60 if keep_enabled else None,
                    }
                ),
            )

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        db: Final = sqlite3.connect(self.root / "versions.sqlite3", timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def path(self, version_id: str) -> Path:
        from account_pool.release_models import ReleaseId

        valid: Final = TypeAdapter[str](ReleaseId).validate_python(version_id)
        destination: Final = self.root / "backups" / valid
        if destination.is_symlink() or destination.resolve().parent != self.root / "backups":
            raise ReleaseError("备份目录无效")
        return destination

    def settings(self) -> tuple[int, str]:
        with self.connection() as db:
            return TypeAdapter(tuple[int, str]).validate_python(
                db.execute("SELECT revision, guide FROM settings WHERE id=1").fetchone()
            )

    def entries(self) -> tuple[tuple[str, ReleaseBackup | None, str], ...]:
        with self.connection() as db:
            rows: Final = TypeAdapter(tuple[tuple[str, str | None, str], ...]).validate_python(
                db.execute("SELECT id, backup, note FROM versions ORDER BY rowid DESC").fetchall()
            )
        return tuple(
            (identifier, ReleaseBackup.model_validate_json(raw) if raw else None, note)
            for identifier, raw, note in rows
        )

    def backup(self, version_id: str) -> ReleaseBackup | None:
        return next((backup for identifier, backup, _ in self.entries() if identifier == version_id), None)

    def save_backup(self, backup: ReleaseBackup) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO versions(id, backup) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET backup=excluded.backup",
                (backup.pair.id, backup.model_dump_json()),
            )
            db.execute("UPDATE settings SET revision=revision+1 WHERE id=1")

    def remove(self, version_id: str) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM versions WHERE id=?", (version_id,))
            db.execute("UPDATE settings SET revision=revision+1 WHERE id=1")

    def edit(self, action: ReleaseAction) -> None:
        with self.connection() as db:
            if action.action == "note":
                db.execute(
                    "INSERT INTO versions(id,note) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET note=excluded.note",
                    (action.version_id, action.text),
                )
            elif action.action == "guide":
                db.execute("UPDATE settings SET guide=? WHERE id=1", (action.text,))
            db.execute("UPDATE settings SET revision=revision+1 WHERE id=1")

    def prepare(
        self,
        action: ReleaseAction,
        actor: str,
        current_id: str | None,
        current_commit: str | None,
        rollback_state: str | None = None,
    ) -> ReleaseConfirmation:
        token: Final = secrets.token_hex(32)
        delay: Final = 10 if action.action in ("delete", "restore_data") or action.force else 5
        now: Final = self.clock()
        with self.connection() as db:
            db.execute("DELETE FROM confirmations WHERE expires<?", (now,))
            db.execute("DELETE FROM rollback_checks WHERE token NOT IN (SELECT token FROM confirmations)")
            db.execute(
                "INSERT INTO confirmations VALUES (?,?,?,?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    actor,
                    action.model_dump_json(),
                    current_id,
                    now + delay,
                    now + 300,
                ),
            )
            if rollback_state:
                db.execute(
                    "INSERT INTO rollback_checks VALUES (?,?)",
                    (hashlib.sha256(token.encode()).hexdigest(), rollback_state),
                )
        return ReleaseConfirmation(token=token, action=action, delay_seconds=delay, current_commit=current_commit)

    def consume(self, token: str, actor: str, current_id: str | None, acknowledgement: str = "") -> ReleaseJob:
        now: Final = self.clock()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row: Final = TypeAdapter[tuple[str, str, str | None, float, float] | None](
                tuple[str, str, str | None, float, float] | None
            ).validate_python(
                db.execute(
                    "SELECT actor,payload,current_id,not_before,expires FROM confirmations WHERE token=?",
                    (hashlib.sha256(token.encode()).hexdigest(),),
                ).fetchone()
            )
            if row is None or row[0] != actor or now > row[4]:
                raise ReleaseError("确认已失效，请重新操作")
            if now < row[3]:
                raise ReleaseError("请等待确认倒计时结束")
            revision: Final = TypeAdapter(tuple[int]).validate_python(
                db.execute("SELECT revision FROM settings WHERE id=1").fetchone()
            )[0]
            action: Final = ReleaseAction.model_validate_json(row[1])
            if action.action == "restore_data" and acknowledgement != f"RESTORE {action.snapshot_id}":
                raise ReleaseError("恢复数据库必须输入 RESTORE 加完整快照编号，确认放弃快照之后的数据")
            checked: Final = TypeAdapter[tuple[str] | None](tuple[str] | None).validate_python(
                db.execute(
                    "SELECT state FROM rollback_checks WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),)
                ).fetchone()
            )
            rollback_state: Final = checked[0] if checked else None
            if action.action in ("apply", "restore_data") and rollback_state is None:
                raise ReleaseError("该回退尚未通过兼容性检查，请重新检查")
            if revision != action.revision or row[2] != current_id:
                raise ReleaseError("版本或备注已变化，请刷新后重新确认")
            pending: Final = TypeAdapter[tuple[int] | None](tuple[int] | None).validate_python(
                db.execute(
                    "SELECT 1 FROM jobs WHERE json_extract(payload,'$.status') IN ('queued','running') LIMIT 1"
                ).fetchone()
            )
            if pending:
                raise ReleaseError("已有任务正在执行，请等待完成")
            job: Final = ReleaseJob(
                id=secrets.token_hex(16),
                action=action,
                status="queued",
                phase="等待执行",
                created_at=now,
                updated_at=now,
                expected_current_id=current_id,
                rollback_state=rollback_state,
            )
            db.execute("INSERT INTO jobs VALUES (?,?)", (job.id, job.model_dump_json()))
            if action.action in ("apply", "restore_data", "recover"):
                state: Final = self._auto_state(db)
                self._save_auto(
                    db,
                    state.model_copy(
                        update={
                            "enabled": False,
                            "revision": state.revision + 1,
                            "status": "paused",
                            "message": "手动回退或恢复已暂停自动更新",
                            "next_check_at": None,
                            "check_requested": False,
                        }
                    ),
                )
            db.execute("DELETE FROM confirmations WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
            db.execute("DELETE FROM rollback_checks WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
        return job

    def jobs(self) -> tuple[ReleaseJob, ...]:
        with self.connection() as db:
            rows: Final = TypeAdapter(tuple[tuple[str], ...]).validate_python(
                db.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 100").fetchall()
            )
        return tuple(ReleaseJob.model_validate_json(row[0]) for row in rows)

    def save_job(self, job: ReleaseJob) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (job.model_dump_json(), job.id))
            if (
                not job.automatic
                and job.action.action == "deploy"
                and job.status in ("failed", "recovered", "interrupted")
                and not job.retryable
            ):
                state: Final = self._auto_state(db)
                self._save_auto(
                    db,
                    state.model_copy(
                        update={
                            "enabled": False,
                            "revision": state.revision + 1,
                            "status": "paused",
                            "message": "手动发布未成功，自动更新已暂停",
                            "next_check_at": None,
                            "check_requested": False,
                        }
                    ),
                )

    def start_job(self, job: ReleaseJob) -> ReleaseJob | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row: Final = TypeAdapter[tuple[str] | None](tuple[str] | None).validate_python(
                db.execute("SELECT payload FROM jobs WHERE id=?", (job.id,)).fetchone()
            )
            if not row or ReleaseJob.model_validate_json(row[0]).status != "queued":
                return None
            enabled: Final = not job.automatic or self._auto_state(db).enabled
            started: Final = job.model_copy(
                update={
                    "status": "running" if enabled else "interrupted",
                    "updated_at": self.clock(),
                    "message": "" if enabled else "自动更新已关闭，未启动替换",
                }
            )
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (started.model_dump_json(), job.id))
            return started if enabled else None


def write_private(path: Path, content: bytes) -> None:
    temporary: Final = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        temporary.chmod(0o600)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
