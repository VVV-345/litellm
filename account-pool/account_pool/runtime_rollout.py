"""显式发布时逐卡排空、备份和验证；失败保留门禁，绝不回放认证卷或修改用户启停。"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Final
from uuid import UUID

import httpx

from account_pool.channels.cliproxyapi.client import HttpCLIProxyClient
from account_pool.compose_runtime import ComposeRuntime
from account_pool.config import Settings
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import ChannelKind, EnvironmentRecord
from account_pool.ports import EnvironmentRepository
from account_pool.repository import PostgresEnvironmentRepository
from account_pool.runtime_rollout_repository import (
    CHECKPOINT,
    PostgresRuntimeRolloutRepository,
    RuntimeCheckpoint,
    require_rollout_ready,
)
from account_pool.shared.secrets import EnvironmentSecretDeriver


class RuntimeVerifier:
    def __init__(self, http: httpx.AsyncClient, secrets: EnvironmentSecretDeriver) -> None:
        self.http: Final = http
        self.client: Final = HttpCLIProxyClient(secrets, client=http)

    async def __call__(self, record: EnvironmentRecord, expected: str | None) -> str:
        if expected is not None:
            for capability in ("managed-identity", "access-token-fallback"):
                await self._require_management_auth(record, capability)
            await self.client.require_managed_identity(record)
            await self.client.require_access_token_fallback(record)
        digest: Final = await self.client.runtime_state_digest(record, require_unique=expected is not None)
        if expected is not None and not hmac.compare_digest(digest, expected):
            raise ValueError("runtime configuration, policy or credential changed during image replacement")
        return digest

    async def _require_management_auth(self, record: EnvironmentRecord, capability: str) -> None:
        async with asyncio.timeout(60):
            while True:
                try:
                    if (await self.http.get(
                        f"http://cliproxy-{record.id.hex}:8317/v0/management/codex/{capability}"
                    )).status_code != 401:
                        raise ValueError("runtime management capability must reject unauthenticated requests")
                    return
                except httpx.TransportError:
                    await asyncio.sleep(0.2)


class RuntimeRolloutError(ValueError):
    def __init__(self, environment_id: UUID, phase: str) -> None:
        super().__init__(f"card={environment_id} phase={phase} failed; maintenance gate retained")


class RuntimeRollout:
    def __init__(
        self,
        environments: EnvironmentRepository,
        checkpoints: PostgresRuntimeRolloutRepository,
        runtime: ComposeRuntime,
        ownership: CredentialOwnership,
        verifier: Callable[[EnvironmentRecord, str | None], Awaitable[str]],
        *,
        drain_timeout_seconds: float = 180,
    ) -> None:
        self.environments: Final = environments
        self.checkpoints: Final = checkpoints
        self.runtime: Final = runtime
        self.ownership: Final = ownership
        self.verifier: Final = verifier
        self.drain_timeout_seconds: Final = drain_timeout_seconds

    async def card(self, environment_id: UUID, release_id: str, image: str) -> RuntimeCheckpoint:
        if (re.fullmatch(r"[a-f0-9]{40}", release_id) is None
                or re.fullmatch(r"ghcr\.io/vvv-345/litellm-cliproxyapi@sha256:[a-f0-9]{64}", image) is None
                or self.runtime.settings.cli_proxy_image != image):
            raise ValueError("runtime rollout requires the fixed release commit and preflight image digest")
        # 归属锁串行化配置和凭据操作，但不锁住结算所需的环境行。
        async with self.ownership.operation(environment_id):
            checkpoint: Final = await self.checkpoints.begin(environment_id, release_id, image)
            if checkpoint.phase == "verified":
                return checkpoint
            try:
                await self.checkpoints.save(replace(checkpoint, phase="draining", failed_phase=None))
                async with asyncio.timeout(self.drain_timeout_seconds):
                    while await self.checkpoints.has_leases(environment_id):
                        await asyncio.sleep(0.1)
                record: Final = await self.environments.get(environment_id)
                if record is None:
                    raise ValueError("card not found")
                require_rollout_ready(record)
                directory: Final = self.runtime.settings.data_root / "runtime-rollouts" / release_id / environment_id.hex
                await self.checkpoints.save(replace(checkpoint, phase="backup"))

                async def capture(current: EnvironmentRecord) -> str:
                    return await self.verifier(current, None)

                expected: Final = await self.runtime.backup_runtime(record, directory, capture)
                await self.checkpoints.save(replace(checkpoint, phase="switching"))

                async def verify(current: EnvironmentRecord) -> None:
                    await self.verifier(current, expected)

                await self.runtime.rollout_runtime_image(record, directory, verify=verify)
                return await self.checkpoints.save(replace(
                    checkpoint, phase="verified", image_verified=True,
                    credential_state="preserved" if record.auth_file_name else "absent", failed_phase=None,
                ))
            except (Exception, asyncio.CancelledError):
                latest: Final = await self.checkpoints.get(environment_id)
                failed_phase: Final = "draining" if latest is None else latest.phase
                await asyncio.shield(self.checkpoints.save(replace(
                    checkpoint, phase="failed", failed_phase=failed_phase,
                )))
                raise RuntimeRolloutError(environment_id, failed_phase) from None


async def _run(release: str, image: str, card: UUID | None, status: UUID | None) -> None:
    settings: Final = Settings(cli_proxy_image=image)  # pyright: ignore[reportCallIssue]  # 其余必填设置来自 Manager 现有环境。
    environments: Final = PostgresEnvironmentRepository(settings.database_url)
    checkpoints: Final = PostgresRuntimeRolloutRepository(settings.database_url)
    if status is not None:
        checkpoint: Final = await checkpoints.get(status)
        sys.stdout.write("null\n" if checkpoint is None else CHECKPOINT.dump_json(checkpoint).decode() + "\n")
        return
    if card is None:
        records: Final = await environments.list()
        sys.stdout.write(json.dumps([str(record.id) for record in records if record.channel is ChannelKind.CLIPROXYAPI]) + "\n")
        return
    secrets: Final = EnvironmentSecretDeriver(settings.secret_seed)
    async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
        service: Final = RuntimeRollout(
            environments, checkpoints, ComposeRuntime(settings, secrets), CredentialOwnership(settings.database_url),
            RuntimeVerifier(http, secrets),
        )
        result: Final = await service.card(card, release, image)
        sys.stdout.write(CHECKPOINT.dump_json(result).decode() + "\n")


def main() -> None:
    parser: Final = argparse.ArgumentParser(description="Explicit, resumable per-card runtime release")
    parser.add_argument("--release", required=True)
    parser.add_argument("--image", required=True)
    mode: Final = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--card", type=UUID)
    mode.add_argument("--status", type=UUID)
    mode.add_argument("--list", action="store_true")
    arguments: Final = parser.parse_args()
    try:
        asyncio.run(_run(arguments.release, arguments.image, arguments.card, arguments.status))
    except Exception as error:
        safe: Final = str(error) if isinstance(error, RuntimeRolloutError) else type(error).__name__
        raise SystemExit(f"runtime rollout failed: {safe}; inspect persistent checkpoint before retry") from None


if __name__ == "__main__":
    main()
