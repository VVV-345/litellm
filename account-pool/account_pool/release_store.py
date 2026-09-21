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

from account_pool.release_models import ReleaseAction, ReleaseBackup, ReleaseConfirmation, ReleaseJob

DEFAULT_GUIDE: Final = """更新与回退
正常更新前自动备份当前运行版本；已有完整备份就跳过。备份失败不会替换服务。
选择备份版本，点击检查并回退，核对兼容性、功能影响与可选版本，通过后等待 5 秒确认。恢复直接使用备份镜像，不需要重新构建。
删除会真正删除备份文件，等待 10 秒后确认。当前运行版本不能删除。

从旧代码继续修改
先保存未提交的修改。推荐复制“创建修复分支”命令，从选中版本的 commit 开始修改。
git revert 适合在原分支撤销改动，保留历史。命令会先核对起点和提交范围；遇到合并提交请改用新分支。
修改并测试后提交新版本，正常构建发布。服务器回退不会自动切换电脑上的代码。
旧分支没有自动构建时，在 GitHub Actions 选择新版构建工作流，将 source_ref 填为修复分支或完整 commit。

数据与恢复
镜像备份不包含数据库、认证文件和聊天日志。回退保留业务数据，数据库结构不兼容时不能直接应用。
历史镜像导入使用导入时的部署配置，页面会标明配置来源。
回退到没有版本管理页面的旧版本时，可在服务器部署目录运行 python3 releasectl.py status 或 apply 版本ID。
"""


class ReleaseError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        self.status: Final = status
        super().__init__(message)


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
            )
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, 0, ?)", (DEFAULT_GUIDE,))

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
        delay: Final = 10 if action.action == "delete" or action.force else 5
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

    def consume(self, token: str, actor: str, current_id: str | None) -> ReleaseJob:
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
            checked: Final = TypeAdapter[tuple[str] | None](tuple[str] | None).validate_python(
                db.execute(
                    "SELECT state FROM rollback_checks WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),)
                ).fetchone()
            )
            rollback_state: Final = checked[0] if checked else None
            if action.action == "apply" and rollback_state is None:
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
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (job.model_dump_json(), job.id))


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
