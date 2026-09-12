"""本模块将号池全局设置和单卡策略同步到 CLIProxyAPI。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Final

import yaml
from pydantic import JsonValue, TypeAdapter

from account_pool.channels.cliproxyapi.client import HttpCLIProxyClient, JSONValue
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.domain import EnvironmentRecord
from account_pool.policies import AccountPolicy
from account_pool.settings import AccountPoolSettings

_JSON_VALUE_ADAPTER: Final = TypeAdapter(JsonValue)
_YAML_DOCUMENT_ADAPTER: Final = TypeAdapter(dict[str, object])


class CLIProxySettingsSynchronizer:
    def __init__(self, client: HttpCLIProxyClient, suppliers: SupplierRegistry | None = None) -> None:
        self._client: Final = client
        self._suppliers: Final = suppliers or SupplierRegistry.default()

    async def apply_global_settings(self, record: EnvironmentRecord, settings: AccountPoolSettings) -> None:
        route_strategy: Final = {
            "auto": "round-robin",
            "priority": "fill-first",
            "random": "round-robin",
            "quota": "weighted-round-robin",
        }[settings.default_route]
        scalar_fields: Final[tuple[tuple[str, JSONValue], ...]] = (
            ("/v0/management/debug", settings.debug_logging_enabled),
            ("/v0/management/logging-to-file", settings.file_logging_enabled),
            ("/v0/management/usage-statistics-enabled", settings.usage_statistics_enabled),
            ("/v0/management/request-log", settings.request_log_enabled),
            ("/v0/management/ws-auth", settings.websocket_auth_enabled or settings.websocket_enabled),
            ("/v0/management/quota-exceeded/switch-project", settings.quota_switch_project),
            ("/v0/management/quota-exceeded/switch-preview-model", settings.quota_switch_preview_model),
            ("/v0/management/request-retry", settings.request_retry),
            ("/v0/management/max-retry-credentials", settings.max_retry_credentials),
            ("/v0/management/max-retry-interval", settings.max_retry_interval),
            ("/v0/management/logs-max-total-size-mb", settings.logs_max_total_size_mb),
            ("/v0/management/error-logs-max-files", settings.error_logs_max_files),
            ("/v0/management/force-model-prefix", settings.force_model_prefix),
            ("/v0/management/routing/strategy", route_strategy),
        )
        await asyncio.gather(*(self._client.put_value(record, path, value) for path, value in scalar_fields))
        await self._client.put_json(record, "/v0/management/oauth-excluded-models", self._excluded_models(settings))
        await self._client.put_json(record, "/v0/management/oauth-model-alias", self._model_aliases(settings))
        await self._client.put_json(
            record,
            "/v0/management/oauth-request-scoped-errors",
            self._request_scoped_errors(settings),
        )
        await self._apply_payload_settings(record, settings)

    async def apply_policy(self, record: EnvironmentRecord, policy: AccountPolicy) -> None:
        route_strategy: Final = {
            "auto": "round-robin",
            "priority": "fill-first",
            "random": "round-robin",
            "quota": "weighted-round-robin",
            "plan": "fill-first",
            "expiry": "fill-first",
            "custom": "round-robin",
        }[policy.routing.strategy]
        await self._client.put_value(record, "/v0/management/routing/strategy", route_strategy)
        await self._client.put_value(record, "/v0/management/request-retry", policy.routing.max_attempts)
        document: Final = _yaml_document(await self._client.get_config_yaml(record))
        merged_document: Final = _policy_yaml_document(document, policy)
        if merged_document != document:
            await self._client.put_config_yaml(
                record,
                yaml.safe_dump(merged_document, sort_keys=False, allow_unicode=False),
            )
        if record.auth_file_name is None:
            return
        fields: Final = _policy_auth_fields(policy)
        if fields:
            await self._client.patch_auth_file_fields(record, record.auth_file_name, fields)

    async def _apply_payload_settings(self, record: EnvironmentRecord, settings: AccountPoolSettings) -> None:
        payload: Final = _JSON_VALUE_ADAPTER.validate_json(settings.payload.model_dump_json(by_alias=True))
        document: Final = _yaml_document(await self._client.get_config_yaml(record))
        await self._client.put_config_yaml(
            record,
            yaml.safe_dump({**document, "payload": payload}, sort_keys=False, allow_unicode=False),
        )

    def _excluded_models(self, settings: AccountPoolSettings) -> JSONValue:
        return {
            definition.excluded_models_key: list(settings.oauth_excluded_models)
            for definition in self._suppliers.definitions.values()
            if definition.uses_oauth_model_exclusions
        }

    @staticmethod
    def _model_aliases(settings: AccountPoolSettings) -> JSONValue:
        return {
            key: [{"name": name, "alias": alias} for name, alias in value]
            for key, value in settings.oauth_model_aliases.items()
        }

    @staticmethod
    def _request_scoped_errors(settings: AccountPoolSettings) -> JSONValue:
        return {
            provider: [_JSON_VALUE_ADAPTER.validate_json(rule.model_dump_json(by_alias=True)) for rule in entries]
            for provider, entries in settings.oauth_request_scoped_errors.items()
        }


def _yaml_document(content: str) -> dict[str, object]:
    return _YAML_DOCUMENT_ADAPTER.validate_python(yaml.safe_load(content) or {})


def _policy_auth_fields(policy: AccountPolicy) -> Mapping[str, object]:
    if policy.codex is not None:
        return {
            "codex_fingerprint_mode": policy.codex.identity_fingerprint_mode,
            "codex_cli_only": policy.codex.cli_only,
            "codex_cli_only_allow_app_server": policy.codex.allow_app_server,
        }
    if policy.claude is not None:
        return {
            "fingerprint_profile": policy.claude.fingerprint_profile,
            "cloak_mode": policy.claude.cloak_mode,
            "rebuild_mid_system_message": policy.claude.rebuild_mid_system_message,
        }
    if policy.kimi is not None:
        return {"fingerprint_profile": policy.kimi.fingerprint_profile}
    return {}


def _policy_yaml_document(document: Mapping[str, object], policy: AccountPolicy) -> dict[str, object]:
    codex: Final = policy.codex
    xai: Final = policy.xai
    antigravity: Final = policy.antigravity
    codex_section: Final = _yaml_section(document, "codex")
    xai_section: Final = _yaml_section(document, "xai")
    antigravity_section: Final = _yaml_section(document, "antigravity")
    return {
        **document,
        **(
            {
                "codex": {
                    **codex_section,
                    "identity-confuse": codex.identity_confuse,
                    "disable-codex-cloaking": codex.disable_codex_cloaking,
                }
            }
            if codex is not None
            else {}
        ),
        **({"xai": {**xai_section, "inject-x-search": xai.inject_x_search}} if xai is not None else {}),
        **(
            {
                "antigravity": {**antigravity_section, "sensitive-words": list(antigravity.sensitive_words)},
                "antigravity-signature-cache-enabled": antigravity.signature_cache_enabled,
                "antigravity-signature-bypass-strict": antigravity.signature_bypass_strict,
            }
            if antigravity is not None
            else {}
        ),
    }


def _yaml_section(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    value: Final = document.get(key)
    return value if isinstance(value, dict) else {}


__all__ = ("CLIProxySettingsSynchronizer",)
