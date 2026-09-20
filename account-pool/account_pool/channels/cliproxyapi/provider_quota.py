"""执行供应商额度探测与回退，复用同一传输和凭据锁，保持供应商请求顺序。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final
from urllib.parse import quote
from uuid import UUID
from weakref import WeakValueDictionary

import httpx

from account_pool.channels.cliproxyapi.protocol import (
    _APICallResponse,
    _AuthFile,
    _ProviderAPICallResult,
)
from account_pool.channels.cliproxyapi.provider_requests import (
    _ANTIGRAVITY_PROD_BASE_URL,
    _antigravity_base_urls,
    _chatgpt_timezone_offset_minutes,
    _codex_subscription_headers,
    _codex_subscription_needs_fallback,
    _codex_usage_headers,
    _request_id,
    _safe_endpoint,
    _upstream_code,
)
from account_pool.channels.cliproxyapi.quota_state import (
    _annotate_refresh,
)
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.cliproxyapi.transport import JSONValue, ManagementRequest
from account_pool.domain import (
    EnvironmentRecord,
    ProviderEndpointFailure,
    ProviderHTTPMethod,
    QuotaSnapshot,
    SupplierKind,
)
from account_pool.provider_quota import (
    parse_claude_usage_quota,
    parse_codex_account_info,
    parse_codex_usage_quota,
    parse_xai_quota,
    parse_xai_user_id,
)
from account_pool.quota import (
    ProviderQuotaError,
    ProviderQuotaRefresh,
    parse_antigravity_assist,
    parse_antigravity_onboard_project,
    parse_antigravity_quota,
)


@dataclass(frozen=True, slots=True)
class ProviderQuotaClient:
    _request: ManagementRequest
    _credential_locks: WeakValueDictionary[tuple[UUID, str], asyncio.Lock]

    async def refresh_provider_quota(
        self,
        record: EnvironmentRecord,
        supplier: SupplierDefinition,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh | None:
        if supplier.kind is SupplierKind.OPENAI_CODEX:
            return await self.refresh_codex_quota(record, auth_file)
        if supplier.kind is SupplierKind.ANTHROPIC_CLAUDE:
            return await self.refresh_claude_quota(record, auth_file)
        if supplier.kind is SupplierKind.XAI:
            return await self.refresh_xai_quota(record, auth_file)
        if supplier.kind is SupplierKind.GOOGLE_ANTIGRAVITY:
            return await self.refresh_antigravity_quota(record, auth_file)
        unsupported: Final = {
            SupplierKind.KIMI: "Kimi does not expose a documented subscription quota API",
            SupplierKind.GEMINI: "Gemini API keys do not expose personal subscription quota windows",
            SupplierKind.GEMINI_INTERACTIONS: "Gemini API keys do not expose personal subscription quota windows",
            SupplierKind.VERTEX: "Vertex service accounts do not expose personal subscription quota windows",
        }.get(supplier.kind)
        return (
            None
            if unsupported is None
            else ProviderQuotaRefresh(quota=QuotaSnapshot(refresh_status="unsupported", refresh_error=unsupported))
        )

    async def refresh_codex_quota(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh:
        observed_at: Final = datetime.now(timezone.utc)
        identity: Final = auth_file.id_token
        identity_account_id: Final = None if identity is None else identity.chatgpt_account_id
        account_check_result: Final = await self.provider_api_call(
            record,
            auth_file,
            "GET",
            (
                "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
                f"?timezone_offset_min={_chatgpt_timezone_offset_minutes()}"
            ),
            _codex_subscription_headers("/backend-api/accounts/check/v4-2023-04-27"),
        )
        account_info: Final = parse_codex_account_info(
            account_check_result.body,
            identity_account_id,
            observed_at,
        )
        account_parse_failure: Final = (
            ProviderEndpointFailure(
                method="GET",
                endpoint="https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
                message="response did not contain a usable active account",
                status_code=200,
            )
            if account_check_result.body is not None and account_info is None
            else None
        )
        account_id: Final = (
            account_info.account_id
            if account_info is not None and account_info.account_id is not None
            else identity_account_id
        )
        headers: Final = _codex_usage_headers(account_id)
        usage_result: Final = await self.provider_api_call(
            record,
            auth_file,
            "GET",
            "https://chatgpt.com/backend-api/wham/usage",
            headers,
        )
        reset_credits_result: Final = await self.provider_api_call(
            record,
            auth_file,
            "GET",
            "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits",
            headers,
        )
        if usage_result.failure is not None:
            fatal_failures: Final = tuple(
                failure
                for failure in (
                    usage_result.failure,
                    account_check_result.failure,
                    account_parse_failure,
                    reset_credits_result.failure,
                )
                if failure is not None
            )
            raise ProviderQuotaError(fatal_failures, "Codex quota refresh failed")
        subscription_result: Final = (
            await self.provider_api_call(
                record,
                auth_file,
                "GET",
                f"https://chatgpt.com/backend-api/subscriptions?account_id={quote(account_id, safe='')}",
                _codex_subscription_headers("/backend-api/subscriptions", account_id),
            )
            if account_id is not None and _codex_subscription_needs_fallback(account_info, identity, observed_at)
            else None
        )
        failures: Final = tuple(
            failure
            for failure in (
                account_check_result.failure,
                account_parse_failure,
                reset_credits_result.failure,
                None if subscription_result is None else subscription_result.failure,
            )
            if failure is not None
        )
        refreshed: Final = parse_codex_usage_quota(
            usage_result.body or "",
            observed_at,
            account_info=account_info,
            subscription_body=None if subscription_result is None else subscription_result.body,
            reset_credits_body=reset_credits_result.body,
        )
        if refreshed is None:
            parse_failure: Final = ProviderEndpointFailure(
                method="GET",
                endpoint="https://chatgpt.com/backend-api/wham/usage",
                message="response did not contain usable quota fields",
                status_code=200,
            )
            raise ProviderQuotaError((*failures, parse_failure), "Codex quota refresh failed")
        usage_shape_failure: Final = (
            ProviderEndpointFailure(
                method="GET",
                endpoint="https://chatgpt.com/backend-api/wham/usage",
                message="response did not contain usable quota windows",
                status_code=200,
            )
            if not refreshed.quota.windows
            else None
        )
        annotated_failures: Final = failures if usage_shape_failure is None else (*failures, usage_shape_failure)
        return _annotate_refresh(refreshed, annotated_failures)

    async def refresh_claude_quota(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh:
        headers: Final = {
            "Authorization": "Bearer $TOKEN$",
            "Accept": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code",
        }
        usage_result, profile_result = await asyncio.gather(
            self.provider_api_call(
                record,
                auth_file,
                "GET",
                "https://api.anthropic.com/api/oauth/usage",
                headers,
            ),
            self.provider_api_call(
                record,
                auth_file,
                "GET",
                "https://api.anthropic.com/api/oauth/profile",
                headers,
            ),
        )
        failures: Final = tuple(
            failure for result in (usage_result, profile_result) if (failure := result.failure) is not None
        )
        if usage_result.body is None and profile_result.body is None:
            raise ProviderQuotaError(failures, "Claude quota refresh failed")
        refreshed: Final = parse_claude_usage_quota(
            usage_result.body,
            profile_result.body,
            datetime.now(timezone.utc),
        )
        if refreshed is None:
            parse_failure: Final = ProviderEndpointFailure(
                method="GET",
                endpoint="https://api.anthropic.com/api/oauth/usage",
                message="responses did not contain usable subscription or quota fields",
                status_code=200,
            )
            raise ProviderQuotaError((*failures, parse_failure), "Claude quota refresh failed")
        return _annotate_refresh(refreshed, failures)

    async def refresh_xai_quota(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh:
        headers: Final = {
            "Authorization": "Bearer $TOKEN$",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-grok-client-version": "0.2.120",
            "x-grok-cli-version": "0.2.120",
            "x-grok-client-surface": "grok-cli",
            "x-grok-client-identifier": "grok-shell",
            "User-Agent": "grok-cli/0.2.120",
        }
        weekly_result, monthly_result, user_result, task_result = await asyncio.gather(
            self.provider_api_call(
                record,
                auth_file,
                "GET",
                "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
                headers,
            ),
            self.provider_api_call(
                record,
                auth_file,
                "GET",
                "https://cli-chat-proxy.grok.com/v1/billing",
                headers,
            ),
            self.provider_api_call(
                record,
                auth_file,
                "GET",
                "https://cli-chat-proxy.grok.com/v1/user?include=subscription",
                headers,
            ),
            self.provider_api_call(
                record,
                auth_file,
                "GET",
                "https://grok.com/rest/tasks/usage",
                {**headers, "User-Agent": "Grok Build"},
            ),
        )
        user_id: Final = parse_xai_user_id(user_result.body)
        subscription_result: Final = await self.provider_api_call(
            record,
            auth_file,
            "GET",
            "https://grok.com/rest/subscriptions",
            {**headers, **({"x-userid": user_id} if user_id is not None else {})},
        )
        results: Final = (weekly_result, monthly_result, user_result, subscription_result, task_result)
        failures: Final = tuple(result.failure for result in results if result.failure is not None)
        refreshed: Final = parse_xai_quota(
            weekly_result.body,
            monthly_result.body,
            datetime.now(timezone.utc),
            user_body=user_result.body,
            subscriptions_body=subscription_result.body,
            task_usage_body=task_result.body,
        )
        if refreshed is None:
            parse_failure: Final = ProviderEndpointFailure(
                method="GET",
                endpoint="https://cli-chat-proxy.grok.com/v1/billing",
                message="responses did not contain usable subscription or quota fields",
                status_code=200 if any(result.body is not None for result in results) else None,
            )
            raise ProviderQuotaError((*failures, parse_failure), "xAI quota refresh failed")
        if not refreshed.quota.windows and not (refreshed.quota.prepaid_balance or 0) > 0:
            missing_quota: Final = ProviderEndpointFailure(
                method="GET",
                endpoint="https://cli-chat-proxy.grok.com/v1/billing?format=credits",
                status_code=200 if weekly_result.body is not None else None,
                upstream_code="quota_fields_missing",
                message="Grok 未返回有效额度，仅取得账号或计费信息；保留已有额度缓存，缺失额度显示未知",
            )
            annotated: Final = _annotate_refresh(refreshed, (missing_quota, *failures))
            return ProviderQuotaRefresh(
                quota=annotated.quota.model_copy(update={"observed_at": None, "refresh_status": "failed"})
            )
        return _annotate_refresh(refreshed, failures)

    async def refresh_antigravity_quota(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
    ) -> ProviderQuotaRefresh:
        headers: Final = {
            "Authorization": "Bearer $TOKEN$",
            "Accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/1.20.5 windows/amd64",
        }
        load_headers: Final = {
            **headers,
            "User-Agent": "antigravity/1.20.5 windows/amd64 google-api-nodejs-client/10.3.0",
            "Accept-Encoding": "gzip, deflate, br",
            "x-goog-api-client": "gl-node/22.21.1",
        }
        preferred_project: Final = auth_file.project_id
        load_payload: Final[dict[str, JSONValue]] = {
            "metadata": {
                "ideName": "antigravity",
                "ideType": "ANTIGRAVITY",
                "ideVersion": "1.20.5",
                "pluginVersion": "1.20.5",
                "platform": "WINDOWS_AMD64",
                "updateChannel": "stable",
                "pluginType": "GEMINI",
                **({"duetProject": preferred_project} if preferred_project is not None else {}),
            },
            "mode": "FULL_ELIGIBILITY_CHECK",
            **({"cloudaicompanionProject": preferred_project} if preferred_project is not None else {}),
        }
        candidate_base_urls: Final = _antigravity_base_urls(auth_file)
        assist_base_url, assist_result = await self.provider_api_call_first_success(
            record,
            auth_file,
            "POST",
            candidate_base_urls,
            "/v1internal:loadCodeAssist",
            load_headers,
            data=load_payload,
        )
        assist: Final = parse_antigravity_assist(assist_result.body)
        quota_base_url: Final = (
            _ANTIGRAVITY_PROD_BASE_URL
            if assist is not None and assist.uses_gcp_tos is True and len(candidate_base_urls) > 1
            else assist_base_url
        )
        initial_project_id: Final = auth_file.project_id or (None if assist is None else assist.project_id)
        onboard_result: Final = (
            await self.onboard_antigravity_user(
                record,
                auth_file,
                assist_base_url,
                assist.onboard_tier_id,
                load_payload["metadata"],
            )
            if initial_project_id is None and assist is not None and assist.onboard_tier_id is not None
            else (None, ())
        )
        project_id: Final = initial_project_id or onboard_result[0]
        onboard_failures: Final = onboard_result[1]
        if project_id is None:
            project_failures: Final = (
                (*onboard_failures, assist_result.failure)
                if assist_result.failure is not None
                else onboard_failures
                if onboard_failures
                else (
                    ProviderEndpointFailure(
                        method="POST",
                        endpoint=f"{assist_base_url}/v1internal:loadCodeAssist",
                        message="response did not contain a project ID",
                        status_code=200,
                    ),
                )
            )
            raise ProviderQuotaError(project_failures, "Antigravity quota refresh failed")
        models_result, summary_result = await asyncio.gather(
            self.provider_api_call(
                record,
                auth_file,
                "POST",
                f"{quota_base_url}/v1internal:fetchAvailableModels",
                headers,
                data={"project": project_id},
            ),
            self.provider_api_call(
                record,
                auth_file,
                "POST",
                f"{quota_base_url}/v1internal:retrieveUserQuotaSummary",
                headers,
                data={"project": project_id},
            ),
        )
        if models_result.failure is not None:
            model_failures: Final = tuple(
                failure
                for failure in (models_result.failure, assist_result.failure, summary_result.failure)
                if failure is not None
            )
            raise ProviderQuotaError(model_failures, "Antigravity quota refresh failed")
        refreshed: Final = parse_antigravity_quota(
            models_result.body or "",
            assist,
            datetime.now(timezone.utc),
            summary_body=summary_result.body,
        )
        if refreshed is None:
            parse_failure: Final = ProviderEndpointFailure(
                method="POST",
                endpoint=f"{quota_base_url}/v1internal:fetchAvailableModels",
                message="response did not contain usable model quota fields",
                status_code=200,
            )
            raise ProviderQuotaError((parse_failure,), "Antigravity quota refresh failed")
        optional_failures: Final = tuple(
            failure
            for failure in (*onboard_failures, assist_result.failure, summary_result.failure)
            if failure is not None
        )
        return _annotate_refresh(refreshed, optional_failures)

    async def onboard_antigravity_user(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        base_url: str,
        tier_id: str,
        metadata: JSONValue,
    ) -> tuple[str | None, tuple[ProviderEndpointFailure, ...]]:
        headers: Final = {
            "Authorization": "Bearer $TOKEN$",
            "Accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/1.20.5 windows/amd64 google-api-nodejs-client/10.3.0",
            "Accept-Encoding": "gzip",
        }
        start_result: Final = await self.provider_api_call(
            record,
            auth_file,
            "POST",
            f"{base_url}/v1internal:onboardUser",
            headers,
            data={"tierId": tier_id, "metadata": metadata},
        )
        if start_result.failure is not None:
            return None, (start_result.failure,)
        project_id, operation, done = parse_antigravity_onboard_project(start_result.body)
        if project_id is not None:
            return project_id, ()
        if done or operation is None:
            return None, (
                ProviderEndpointFailure(
                    method="POST",
                    endpoint=f"{base_url}/v1internal:onboardUser",
                    message="response did not contain a project ID or pollable operation",
                    status_code=200,
                ),
            )
        return await self.poll_antigravity_onboard_operation(record, auth_file, base_url, operation, headers)

    async def poll_antigravity_onboard_operation(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        base_url: str,
        operation: str,
        headers: Mapping[str, str],
        attempts_remaining: int = 60,
    ) -> tuple[str | None, tuple[ProviderEndpointFailure, ...]]:
        endpoint: Final = f"{base_url}/v1internal/{operation.lstrip('/')}"
        if attempts_remaining <= 0:
            return None, (
                ProviderEndpointFailure(
                    method="GET",
                    endpoint=endpoint,
                    message="onboard operation did not complete before timeout",
                ),
            )
        result: Final = await self.provider_api_call(record, auth_file, "GET", endpoint, headers)
        if result.failure is not None:
            return None, (result.failure,)
        project_id, _, done = parse_antigravity_onboard_project(result.body)
        if project_id is not None:
            return project_id, ()
        if done:
            return None, (
                ProviderEndpointFailure(
                    method="GET",
                    endpoint=endpoint,
                    message="completed operation did not contain a project ID",
                    status_code=200,
                ),
            )
        await asyncio.sleep(0.5)
        return await self.poll_antigravity_onboard_operation(
            record,
            auth_file,
            base_url,
            operation,
            headers,
            attempts_remaining - 1,
        )

    async def provider_api_call_first_success(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        method: ProviderHTTPMethod,
        base_urls: tuple[str, ...],
        path: str,
        headers: Mapping[str, str],
        *,
        data: JSONValue | None = None,
    ) -> tuple[str, _ProviderAPICallResult]:
        base_url, *remaining_base_urls = base_urls
        result: Final = await self.provider_api_call(
            record,
            auth_file,
            method,
            f"{base_url}{path}",
            headers,
            data=data,
        )
        if result.body is not None or not remaining_base_urls:
            return base_url, result
        return await self.provider_api_call_first_success(
            record,
            auth_file,
            method,
            tuple(remaining_base_urls),
            path,
            headers,
            data=data,
        )

    async def provider_api_call(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        method: ProviderHTTPMethod,
        url: str,
        headers: Mapping[str, str],
        *,
        data: JSONValue | None = None,
    ) -> _ProviderAPICallResult:
        # 同一凭据的管理请求可能轮换 refresh token，等待者必须等前一请求完成后再读取上游凭据。
        lock: Final = self._credential_locks.setdefault(
            (record.id, auth_file.auth_index or auth_file.name), asyncio.Lock()
        )
        async with lock:
            return await self.provider_api_call_serialized(record, auth_file, method, url, headers, data=data)

    async def provider_api_call_serialized(
        self,
        record: EnvironmentRecord,
        auth_file: _AuthFile,
        method: ProviderHTTPMethod,
        url: str,
        headers: Mapping[str, str],
        *,
        data: JSONValue | None = None,
    ) -> _ProviderAPICallResult:
        if auth_file.auth_index is None:
            return _ProviderAPICallResult(
                failure=ProviderEndpointFailure(
                    method=method,
                    endpoint=_safe_endpoint(url),
                    message="credential is missing auth_index",
                )
            )
        try:
            response: Final = await self._request(
                record,
                "POST",
                "/v0/management/api-call",
                json={
                    "auth_index": auth_file.auth_index,
                    "method": method,
                    "url": url,
                    "header": dict(headers),
                    "data": "" if data is None else json.dumps(data, separators=(",", ":")),
                },
            )
            payload: Final = _APICallResponse.model_validate(response.json())
        except httpx.HTTPStatusError as error:
            return _ProviderAPICallResult(
                failure=ProviderEndpointFailure(
                    method=method,
                    endpoint=_safe_endpoint(url),
                    message="CLIProxyAPI management request failed",
                    status_code=error.response.status_code,
                )
            )
        except (httpx.HTTPError, ValueError) as error:
            return _ProviderAPICallResult(
                failure=ProviderEndpointFailure(
                    method=method,
                    endpoint=_safe_endpoint(url),
                    message=error.__class__.__name__,
                )
            )
        if payload.status_code < 200 or payload.status_code >= 300:
            challenged: Final = payload.status_code == 403 and any(
                name.lower() == "cf-mitigated" and any(value.lower() == "challenge" for value in values)
                for name, values in payload.header.items()
            )
            return _ProviderAPICallResult(
                failure=ProviderEndpointFailure(
                    method=method,
                    endpoint=_safe_endpoint(url),
                    message=(
                        "Cloudflare 浏览器验证拦截，未返回额度数据"
                        if challenged
                        else "provider quota endpoint rejected the request"
                    ),
                    status_code=payload.status_code,
                    request_id=_request_id(payload.header),
                    upstream_code="cloudflare_challenge" if challenged else _upstream_code(payload.body),
                )
            )
        return _ProviderAPICallResult(body=payload.body)
