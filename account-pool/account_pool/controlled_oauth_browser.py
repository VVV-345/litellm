"""复用手动与自动授权的浏览器启动流程，保留原有会话、代理和一次性回调边界。"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

from account_pool.credential_ownership import OAUTH_AUTOMATION_LOCK, CredentialOwnership
from account_pool.domain import AuthorizationFlow, EnvironmentStatus
from account_pool.oauth_browser import (
    OAuthBrowserAutomation,
    OAuthBrowserRuntime,
    OAuthBrowserSession,
    OAuthBrowserSessionGrant,
    OAuthBrowserSessionService,
    OAuthBrowserSessionStatus,
    cleanup_oauth_browser_sessions,
)
from account_pool.ports import EnvironmentRepository, ProxyProfileRepository
from account_pool.service import EnvironmentService
from account_pool.shared.result import Failure, FailureCode, Result, Success


def _loopback_proxy(url: str) -> bool:
    host: Final = (urlsplit(url).hostname or "").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ControlledOAuthBrowser:
    environments: EnvironmentRepository
    profiles: ProxyProfileRepository
    sessions: OAuthBrowserSessionService
    runtime: OAuthBrowserRuntime
    service: EnvironmentService
    ownership: CredentialOwnership

    async def current_binding(self, session: OAuthBrowserSession) -> bool:
        record: Final = await self.environments.get(session.environment_id)
        if (
            record is None
            or record.status is EnvironmentStatus.DELETING
            or record.proxy_profile_id != session.proxy_profile_id
        ):
            return False
        url: Final = await self.profiles.get_url(session.proxy_profile_id)
        return url is not None and self.sessions.proxy_binding_matches(session, session.proxy_profile_id, url)

    async def fail(self, session_id: UUID) -> None:
        await self.sessions.transition(session_id, OAuthBrowserSessionStatus.FAILED)
        await cleanup_oauth_browser_sessions(self.sessions, self.runtime)

    async def start(
        self,
        environment_id: UUID,
        *,
        automation: OAuthBrowserAutomation | None = None,
        operation_id: str | None = None,
        on_reserved: Callable[[OAuthBrowserSessionGrant], Awaitable[None]] | None = None,
    ) -> Result[OAuthBrowserSessionGrant]:
        await cleanup_oauth_browser_sessions(self.sessions, self.runtime)
        async with self.ownership.operation(OAUTH_AUTOMATION_LOCK), self.ownership.operation(environment_id):
            if automation is None and any(
                record.oauth_automation_operation is not None for record in await self.environments.list()
            ):
                return Failure(FailureCode.CONFLICT, "授权自动化正在运行，请等待或取消后再手动登录")
            record: Final = await self.environments.get(environment_id)
            if record is None or record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.proxy_profile_id is None:
                return Failure(FailureCode.CONFLICT, "a proxy profile is required")
            if record.authorization_flow is not AuthorizationFlow.BROWSER_OAUTH:
                return Failure(FailureCode.CONFLICT, "browser OAuth is not available")
            url: Final = await self.profiles.get_url(record.proxy_profile_id)
            if url is None:
                return Failure(FailureCode.CONFLICT, "the selected proxy is unavailable")
            if automation is not None and _loopback_proxy(url):
                return Failure(FailureCode.CONFLICT, "自动化出口代理不能使用容器内回环地址，请选择可达的普通代理")
            started: Final = await self.sessions.start(environment_id, record.proxy_profile_id, url)
        if isinstance(started, Failure):
            return started
        grant: Final = started.value
        try:
            if on_reserved is not None:
                await on_reserved(grant)
            authorization: Final = (
                await self.service.authorize_environment(
                    environment_id,
                    operation_id=operation_id,
                    require_empty=True,
                )
                if automation
                else await self.service.authorize_environment(
                    environment_id,
                    operation_id=f"browser:{grant.session.id.hex}",
                )
            )
            if isinstance(authorization, Failure):
                await self.fail(grant.session.id)
                return Failure(authorization.code, "OAuth browser authorization could not be prepared")
            if authorization.value.flow is not AuthorizationFlow.BROWSER_OAUTH:
                await self.fail(grant.session.id)
                return Failure(FailureCode.CONFLICT, "browser OAuth is not available")
            async with self.ownership.operation(environment_id):
                authorized: Final = await self.environments.get(environment_id)
                callback: Final = None if authorized is None else self.service.oauth_callback_target(authorized)
                if callback is None or not await self.current_binding(grant.session):
                    await self.fail(grant.session.id)
                    return Failure(FailureCode.CONFLICT, "the proxy binding changed")
                if automation is None:
                    await self.runtime.start_oauth_browser(
                        grant.session,
                        proxy_url=url,
                        authorization_url=str(authorization.value.authorization_url),
                        callback_token=grant.callback_token,
                        callback_port=callback[0],
                        callback_path=callback[1],
                    )
                else:
                    await self.runtime.start_oauth_browser(
                        grant.session,
                        proxy_url=url,
                        authorization_url=str(authorization.value.authorization_url),
                        callback_token=grant.callback_token,
                        callback_port=callback[0],
                        callback_path=callback[1],
                        automation=automation,
                    )
                active: Final = await self.sessions.activate(grant.session.id)
                if active is None:
                    await self.runtime.remove_oauth_browser(grant.session.id)
                    await cleanup_oauth_browser_sessions(self.sessions, self.runtime)
                    return Failure(FailureCode.CONFLICT, "OAuth browser session ended")
            return Success(OAuthBrowserSessionGrant(active, grant.ticket, grant.callback_token))
        except asyncio.CancelledError:
            await self.sessions.transition(grant.session.id, OAuthBrowserSessionStatus.FAILED)
            await asyncio.shield(cleanup_oauth_browser_sessions(self.sessions, self.runtime))
            raise
        except Exception:
            await self.fail(grant.session.id)
            return Failure(FailureCode.UPSTREAM, "OAuth browser session could not be started")
