"""计算授权到期时间并替换回调 state，不消费或持久化授权状态。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from account_pool.domain import AuthorizationFlow, EnvironmentRecord, EnvironmentStatus, SupplierKind, utc_now


def saved_login_can_replace(record: EnvironmentRecord, email: str, now: datetime) -> bool:
    """仅替换已确认失效、当前没有可路由凭据的同账号绑定。"""
    fallback: Final = record.access_token_fallback
    return (
        record.supplier is SupplierKind.OPENAI_CODEX
        and record.authorization_flow is AuthorizationFlow.BROWSER_OAUTH
        and record.enabled and not record.manual_cooldown and not record.auth_file_disabled
        and not record.configuration_pending and not record.credential_refresh.save_pending
        and record.desired_configuration_version == record.observed_configuration_version
        and record.status in (EnvironmentStatus.COOLING_DOWN, EnvironmentStatus.ERROR, EnvironmentStatus.READY)
        and record.credential_email is not None and record.credential_email.casefold() == email.casefold()
        and (record.email_rebinding_job_id is not None or not any(fallback.usable_for(model, now) for model in record.enabled_models))
        and (
            (
                record.status is not EnvironmentStatus.READY
                and (
                    record.credential_refresh.schedule_reason == "reauthorization_required"
                    or record.last_error == "上游认证失效，请重新认证"
                )
            )
            or fallback.failure_source == "refresh" and (
                fallback.reason in ("access_expired", "access_rejected")
                or fallback.expires_at is not None and fallback.expires_at <= now
            )
        )
    )


def _authorization_expires_at(flow: AuthorizationFlow, expires_in_seconds: int | None) -> datetime:
    duration: Final = (
        expires_in_seconds if flow is AuthorizationFlow.DEVICE_CODE and expires_in_seconds is not None else 300
    )
    return utc_now() + timedelta(seconds=min(max(duration, 1), 3600))


def _replace_state(authorization_url: str, state: str) -> str:
    """只替换 OAuth URL 的 state 参数，保留上游其余参数并避免把 state 拼进日志。"""
    try:
        parsed: Final = urlsplit(authorization_url)
        query: Final = tuple(
            (key, state if key == "state" else value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        )
        final_query: Final = query if any(key == "state" for key, _ in query) else (*query, ("state", state))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(final_query), parsed.fragment))
    except ValueError:
        return authorization_url
