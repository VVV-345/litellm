"""本模块独立运行部署任务，持有进程锁并鉴权，不随两个业务容器一起替换。"""

import asyncio
import os
import secrets
import time
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated, Final

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from account_pool.release_models import (
    ReleaseAction,
    ReleaseCommands,
    ReleaseConfirmation,
    ReleaseExecute,
    ReleaseId,
    ReleaseJob,
    ReleaseView,
)
from account_pool.release_runtime import DockerReleaseRuntime, ReleaseSettings
from account_pool.release_service import ReleaseService
from account_pool.release_store import ReleaseError, ReleaseStore, write_private


@contextmanager
def worker_lock(root: Path) -> Generator[None]:
    with (root / "worker.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def initialize(service: ReleaseService) -> None:
    marker: Final = service.store.root / "initialized"
    if marker.exists():
        return
    current: Final = service.current_or_none()
    if current is None:
        return
    revision, _ = service.store.settings()
    confirmation: Final = service.store.prepare(
        ReleaseAction(action="scan", revision=revision),
        "initialization",
        current.id,
        current.commit,
    )
    # 首次接管由部署授权触发，仍保留确认协议的等待时间和持久化任务。
    time.sleep(confirmation.delay_seconds)
    service.execute(confirmation.token, "initialization")
    write_private(marker, b"1")


async def run_queue(service: ReleaseService, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
        if await run_next(service):
            continue
        try:
            await asyncio.wait_for(stopped.wait(), timeout=1)
        except TimeoutError:
            pass


async def run_next(service: ReleaseService) -> bool:
    pending: Final = next((job for job in service.store.jobs() if job.status == "queued"), None)
    if pending is None:
        return False
    await asyncio.to_thread(service.run, pending)
    return True


def release_application(service: ReleaseService, token: str, *, initialize_backups: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        with worker_lock(service.store.root):
            await asyncio.to_thread(service.recover_interrupted)
            if initialize_backups and not any(job.status == "queued" for job in service.store.jobs()):
                await asyncio.to_thread(initialize, service)
            stopped: Final = asyncio.Event()
            task: Final = asyncio.create_task(run_queue(service, stopped))
            try:
                yield
            finally:
                stopped.set()
                await task

    app: Final = FastAPI(title="Project release worker", lifespan=lifespan, docs_url=None, redoc_url=None)

    def authenticate(
        authorization: Annotated[str, Header()] = "",
        x_release_actor: Annotated[str, Header(min_length=1, max_length=128)] = "server-cli",
    ) -> str:
        if not secrets.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "部署管理身份验证失败")
        return x_release_actor

    async def release_error(_request: Request, error: ReleaseError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status, headers={"Cache-Control": "no-store"})

    def health() -> dict[str, str]:
        return {"status": "ok"}

    def view(_actor: Annotated[str, Depends(authenticate)]) -> ReleaseView:
        return service.view()

    def prepare(action: ReleaseAction, actor: Annotated[str, Depends(authenticate)]) -> ReleaseConfirmation:
        return service.prepare(action, actor)

    def execute(body: ReleaseExecute, actor: Annotated[str, Depends(authenticate)]) -> ReleaseJob:
        return service.execute(body.token, actor)

    def commands(version_id: ReleaseId, _actor: Annotated[str, Depends(authenticate)]) -> ReleaseCommands:
        return service.commands(version_id)

    app.exception_handler(ReleaseError)(release_error)
    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/api/releases", view, methods=["GET"])
    app.add_api_route("/api/releases/prepare", prepare, methods=["POST"])
    app.add_api_route("/api/releases/execute", execute, methods=["POST"])
    app.add_api_route("/api/releases/{version_id}/commands", commands, methods=["GET"])
    return app


def create_app() -> FastAPI:
    settings: Final = ReleaseSettings(token=SecretStr(os.environ.get("ACCOUNT_POOL_RELEASE_TOKEN", "")))
    service: Final = ReleaseService(ReleaseStore(settings.root), DockerReleaseRuntime(settings), settings.reserve_bytes)
    return release_application(
        service, settings.token.get_secret_value(), initialize_backups=settings.initialize_backups
    )
