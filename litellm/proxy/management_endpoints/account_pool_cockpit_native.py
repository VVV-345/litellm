from __future__ import annotations

import os
import time
from collections.abc import Callable
from functools import lru_cache, partial
from typing import Final, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from typing_extensions import Self

from litellm.proxy.management_endpoints.account_pool_cockpit_admin import (
    CockpitAdminClient,
    CockpitAdminConfig,
    CockpitFailure,
    validate_http_origin,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_credential_service import CockpitCredentialReceiptService
from litellm.proxy.management_endpoints.account_pool_cockpit_native_observation import NativeCredentialObservation
from litellm.proxy.management_endpoints.account_pool_cockpit_receipt_contracts import (
    CredentialReceiptRejection,
    NativeCredentialBindings,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_runtime import (
    CockpitPolicyRuntime,
    RuntimeFailure,
    read_cockpit_runtime,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_scope_service import (
    CockpitScopeManifest,
    CockpitScopeService,
    CockpitSubjectRouteState,
    CockpitSubjectScopeView,
    ScopeFailure,
    SubjectReader,
    subject_routing_token,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_subject_reader import NativeTrustedSubjectReader


def native_cockpit_enabled() -> bool:
    backend: Final = os.getenv("ACCOUNT_POOL_BACKEND", "manager")
    if backend not in ("manager", "cockpit"):
        raise ValueError("ACCOUNT_POOL_BACKEND must be manager or cockpit")
    return backend == "cockpit"


class NativeCockpitError(Exception):
    def __init__(self, status: int = 503, code: str = "unavailable") -> None:
        self.status: Final = status
        self.code: Final = code
        super().__init__(code)


class NativeCockpitConfig(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    admin_url: str = Field(repr=False)
    admin_token: SecretStr = Field(min_length=32)
    data_url: str = Field(repr=False)
    receipt_key: SecretStr = Field(min_length=32)
    routing_seed: SecretStr = Field(min_length=32)

    @field_validator("admin_url", "data_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return validate_http_origin(value)

    @model_validator(mode="after")
    def independent_secrets(self) -> Self:
        CockpitAdminConfig(url=self.admin_url, token=self.admin_token)
        if len({secret.get_secret_value() for secret in (self.admin_token, self.receipt_key, self.routing_seed)}) != 3:
            raise ValueError("Admin token, receipt key and routing seed must be independent")
        return self

    @classmethod
    def from_environment(cls) -> NativeCockpitConfig:
        try:
            return cls(
                admin_url=os.environ["ACCOUNT_POOL_COCKPIT_ADMIN_URL"],
                admin_token=SecretStr(os.environ["ACCOUNT_POOL_COCKPIT_ADMIN_TOKEN"]),
                data_url=os.environ["ACCOUNT_POOL_COCKPIT_DATA_URL"],
                receipt_key=SecretStr(os.environ["ACCOUNT_POOL_COCKPIT_RECEIPT_KEY"]),
                routing_seed=SecretStr(os.environ["ACCOUNT_POOL_COCKPIT_ROUTING_SEED"]),
            )
        except (KeyError, ValueError):
            raise NativeCockpitError(503, "native_configuration_invalid") from None


class NativeCockpitAccountView(BaseModel):
    account_id: UUID
    credential_present: bool


class NativeScopeStatus(BaseModel):
    scope: CockpitSubjectScopeView
    runtime: CockpitPolicyRuntime | None
    runtime_state: Literal["active", "pending", "unavailable"]


def _observation_matches(
    observation: NativeCredentialBindings | CredentialReceiptRejection,
    scope: CockpitSubjectScopeView | CockpitSubjectRouteState,
) -> bool:
    return (
        isinstance(observation, NativeCredentialBindings)
        and observation.manifest_revision == scope.persisted_manifest_revision
        and observation.scope.subject_hash == scope.subject_hash
        and scope.account_ids == (str(observation.target_account_id),)
        and frozenset(observation.scope.models) == frozenset(scope.models)
    )


class NativeCockpitServices:
    def __init__(
        self,
        config: NativeCockpitConfig,
        *,
        http: httpx.AsyncClient | None = None,
        subjects: SubjectReader | None = None,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._owns_http: Final = http is None
        self._http: Final = (
            http if http is not None else httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False)
        )
        self.target: Final = CockpitAdminClient(
            CockpitAdminConfig(url=config.admin_url, token=config.admin_token), self._http
        )
        self.subjects: Final = subjects if subjects is not None else NativeTrustedSubjectReader()
        self.observations: Final = NativeCredentialObservation(self.subjects, self.target)
        self.receipts: Final = CockpitCredentialReceiptService(self.observations, config.receipt_key, clock)
        self.scope: Final = CockpitScopeService(
            self.receipts,
            self.target,
            partial(subject_routing_token, config.routing_seed),
            config.data_url,
            self.subjects,
        )

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def accounts(self) -> tuple[NativeCockpitAccountView, ...]:
        snapshot: Final = await self.target.read_manifest()
        files: Final = await self.target.list_auth_files()
        if isinstance(snapshot, CockpitFailure) or isinstance(files, CockpitFailure):
            raise NativeCockpitError()
        manifest: Final = CockpitScopeManifest.model_validate(snapshot.data)
        return tuple(
            NativeCockpitAccountView(
                account_id=UUID(account.id),
                credential_present=any(item.id == account.authId and item.size > 0 for item in files),
            )
            for account in manifest.accounts or ()
        )

    async def view(self, subject_hash: str) -> NativeScopeStatus:
        scope: Final = await self.scope.read(subject_hash)
        if isinstance(scope, ScopeFailure):
            raise NativeCockpitError(scope.status, scope.code)
        observation: Final = await self.observations.read(subject_hash) if scope.state == "active" else None
        current: Final = (
            scope.model_copy(update={"state": "blocked"})
            if observation is not None and not _observation_matches(observation, scope)
            else scope
        )
        runtime: Final = await read_cockpit_runtime(self.target)
        if isinstance(runtime, RuntimeFailure):
            return NativeScopeStatus(scope=current, runtime=None, runtime_state="unavailable")
        live: Final = (
            current.state == "active"
            and runtime.active_revision == runtime.persisted_revision == scope.persisted_manifest_revision
        )
        return NativeScopeStatus(scope=current, runtime=runtime, runtime_state="active" if live else "pending")

    async def route_state(self, subject_hash: str) -> CockpitSubjectRouteState:
        state: Final = await self.scope.route_state(subject_hash)
        if isinstance(state, ScopeFailure):
            raise NativeCockpitError(state.status, state.code)
        if state.state != "active":
            return state
        observation: Final = await self.observations.read(subject_hash)
        runtime: Final = await read_cockpit_runtime(self.target)
        if (
            not _observation_matches(observation, state)
            or isinstance(runtime, RuntimeFailure)
            or runtime.active_revision != state.persisted_manifest_revision
            or runtime.persisted_revision != state.persisted_manifest_revision
        ):
            return state.model_copy(update={"state": "blocked", "api_key": None, "origin": None})
        return state


@lru_cache(maxsize=1)
def _configured_services() -> NativeCockpitServices:
    return NativeCockpitServices(NativeCockpitConfig.from_environment())


def native_cockpit_services() -> NativeCockpitServices:
    if not native_cockpit_enabled():
        raise NativeCockpitError(503, "native_backend_disabled")
    return _configured_services()


async def close_native_cockpit_services() -> None:
    if _configured_services.cache_info().currsize:
        await _configured_services().close()
        _configured_services.cache_clear()
