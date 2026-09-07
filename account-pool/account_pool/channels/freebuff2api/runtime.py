"""本模块把 FreeBuff 配置和凭据交给公共 Compose 运行时，保持账号数据卷与网络身份稳定。"""

from pathlib import Path
from typing import Final
from uuid import UUID

import httpx

from account_pool.channels.freebuff2api.client import _HealthResponse, _ModelsResponse
from account_pool.compose_renderer import render_freebuff_compose, render_freebuff_credentials
from account_pool.compose_runtime import ComposeRuntime
from account_pool.domain import EnvironmentConfiguration, EnvironmentRecord, configured_proxy_url
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose

_CREDENTIAL_FILE_NAME: Final = "freebuff_credentials.json"


class FreeBuff2APIRuntime:
    def __init__(self, runtime: ComposeRuntime, secrets: EnvironmentSecretDeriver) -> None:
        self._runtime: Final = runtime
        self._secrets: Final = secrets

    def _compose(self, record: EnvironmentRecord, proxy_url: str) -> str:
        return render_freebuff_compose(
            record,
            self._runtime.settings,
            self._secrets.derive(record.id, SecretPurpose.GATEWAY),
            proxy_url=proxy_url,
        )

    async def provision(self, record: EnvironmentRecord) -> None:
        await self._runtime.provision_freebuff(record, compose=self._compose(record, configured_proxy_url(record)))

    async def apply_configuration(self, record: EnvironmentRecord, configuration: EnvironmentConfiguration) -> None:
        await self._runtime.apply_freebuff_compose(record, compose=self._compose(record, configuration.proxy_url))

    async def ensure_control_plane_connections(self, environment_id: UUID) -> None:
        await self._runtime.ensure_control_plane_connections(environment_id)

    async def set_running(self, record: EnvironmentRecord, running: bool) -> None:
        await self._runtime.set_running(record, running)

    async def remove(self, record: EnvironmentRecord) -> None:
        await self._runtime.remove(record)

    async def remove_compose(self, record: EnvironmentRecord) -> None:
        await self._runtime.remove_compose(record)

    async def remove_directory(self, environment_id: UUID) -> None:
        await self._runtime.remove_directory(environment_id)

    def environment_dir(self, environment_id: UUID) -> Path:
        return self._runtime.environment_dir(environment_id)

    async def write_credential(self, record: EnvironmentRecord, auth_token: str) -> None:
        # 凭据经 stdin 进入一次性容器，避免出现在进程参数中。
        payload: Final = render_freebuff_credentials(auth_token)
        script: Final = f"cat > /data/{_CREDENTIAL_FILE_NAME} && chmod 600 /data/{_CREDENTIAL_FILE_NAME}"
        await self._runtime.write_volume_files(
            record.id,
            image=self._runtime.settings.freebuff2api_image,
            script=script,
            stdin_content=payload,
            user="1000:1000",
        )
        await self._runtime.restart(record.id)

    async def health_check(self, record: EnvironmentRecord, client: httpx.AsyncClient) -> bool:
        base_url: Final = f"http://freebuff-{record.id.hex}:8787"
        headers: Final = {"Authorization": f"Bearer {self._secrets.derive(record.id, SecretPurpose.GATEWAY)}"}
        try:
            health: Final = await client.get(f"{base_url}/healthz", headers=headers)
            if health.status_code != httpx.codes.OK:
                return False
            # 刚写入凭据但尚无流量的账号计入 unknown，不能据此判死。
            parsed: Final = _HealthResponse.model_validate(health.json())
            if parsed.accounts == 0 or parsed.accounts != parsed.alive_accounts + parsed.unknown_accounts:
                return False
            models: Final = await client.get(f"{base_url}/v1/models", headers=headers)
            if models.status_code != httpx.codes.OK:
                return False
            return bool(_ModelsResponse.model_validate(models.json()).data)
        except (httpx.HTTPError, ValueError):
            return False
