from datetime import timedelta

from account_pool import domain
from account_pool.policies import CodexPolicy, AccountPolicy
from account_pool.channels.cliproxyapi.settings_sync import _policy_auth_fields
from test_cliproxy_supplier_client import _record


def test_access_fallback_policy_defaults_on_and_preserves_explicit_disable():
    assert CodexPolicy().allow_valid_access_after_refresh_failure is True
    assert CodexPolicy(allow_valid_access_after_refresh_failure=False).allow_valid_access_after_refresh_failure is False
    from litellm.proxy.management_endpoints.account_pool_management_models import CodexPolicy as ProxyCodexPolicy
    assert ProxyCodexPolicy().allow_valid_access_after_refresh_failure is True
    enabled = CodexPolicy(allow_valid_access_after_refresh_failure=True)
    fields = _policy_auth_fields(_record(), AccountPolicy(codex=enabled))
    assert fields["codex_allow_valid_access_after_refresh_failure"] is True


def test_access_fallback_status_requires_evidence_and_current_expiry():
    status_type = getattr(domain, "AccessTokenFallbackStatus", None)
    assert status_type is not None, "typed fallback status missing"
    now = domain.utc_now()
    good = status_type(
        enabled=True,
        active=True,
        reason="temporary_access",
        failure_source="refresh",
        expires_at=now + timedelta(minutes=5),
        verified_models=("model-a",),
    )
    assert good.usable_for("model-a", now)
    assert not good.usable_for("model-b", now)
    assert not good.usable_for("model-a", now + timedelta(minutes=6))
    assert not good.model_copy(update={"enabled": False}).usable_for("model-a", now)
    assert not good.model_copy(update={"active": False}).usable_for("model-a", now)
    assert not good.model_copy(update={"failure_source": "access"}).usable_for("model-a", now)


import importlib
import pytest
from account_pool.domain import EnvironmentStatus, ModelCooldown, QuotaSnapshot, QuotaWindow, utc_now


def test_fallback_eligibility_keeps_independent_limits():
    try:
        module = importlib.import_module("account_pool.access_token_fallback")
    except ModuleNotFoundError:
        pytest.fail("fallback orchestration missing")
    record = _record().model_copy(
        update={
            "auth_file_name": "fixture.json",
            "auth_index": "fixture",
            "credential_fingerprints": ("fixture-binding",),
            "status": EnvironmentStatus.COOLING_DOWN,
            "last_error": "上游认证失效，请重新认证",
        }
    )
    assert module.fallback_eligible(record, "model-a")
    for updates in (
        {"enabled": False},
        {"manual_cooldown": True},
        {"auth_file_disabled": True},
        {"configuration_pending": True},
        {"last_error": "network failure"},
        {"cooldown_until": utc_now() + timedelta(minutes=1)},
        {
            "model_cooldowns": (
                ModelCooldown(model="model-a", reason="rate_limit", retry_at=utc_now() + timedelta(minutes=1)),
            )
        },
    ):
        assert not module.fallback_eligible(record.model_copy(update=updates), "model-a")
    assert not module.fallback_eligible(record, "other-model")


from contextlib import asynccontextmanager
from types import SimpleNamespace


class FallbackOwnership:
    def __init__(self, owned=True):
        self.owned = owned

    @asynccontextmanager
    async def operation(self, _id):
        yield

    async def owns(self, _id, fingerprints):
        return self.owned

    async def claim(self, _id, fingerprints):
        pass

    async def retain(self, _id, fingerprints=()):
        pass


class FallbackEnvironments:
    def __init__(self, record):
        self.record = record

    async def get(self, _id):
        return self.record

    async def list(self):
        return (self.record,)

    async def save_if_version(self, record, version):
        if self.record.version != version:
            return None
        self.record = record
        return record


class FallbackPolicies:
    def __init__(self, enabled=True):
        self.view = SimpleNamespace(
            version=1,
            runtime_status="synced",
            policy=AccountPolicy(codex=CodexPolicy(allow_valid_access_after_refresh_failure=enabled)),
        )

    async def get(self, _id):
        return self.view


class FallbackLeases:
    async def cooling(self):
        return ()


class FallbackUpstream:
    def __init__(self):
        self.calls = []
        self.during = None
        self.quota = QuotaSnapshot(
            observed_at=utc_now(),
            source="provider_api",
            refresh_status="complete",
            windows=(QuotaWindow(name="5h", remaining_percent=50, used_percent=50, window_minutes=300),),
        )
        self.models = ()

    async def access_quota(self, record):
        self.calls.append("quota")
        return self.quota, self.models

    async def verify_access_fallback(self, record, model):
        self.calls.append("verify")
        if self.during:
            self.during()
        return domain.AccessTokenFallbackVerification(
            passed=True, restored=True, reason="temporary_access", expires_at=utc_now() + timedelta(minutes=5)
        )

    async def access_fallback_status(self, record):
        self.calls.append("status")
        return domain.AccessTokenFallbackStatus(
            enabled=True,
            active=True,
            reason="temporary_access",
            failure_source="refresh",
            expires_at=utc_now() + timedelta(minutes=5),
            verified_models=("model-a",),
        )


@pytest.mark.asyncio
async def test_fallback_service_requires_policy_and_commits_only_current_proof():
    module = importlib.import_module("account_pool.access_token_fallback")
    record = _record().model_copy(
        update={
            "auth_file_name": "fixture.json",
            "auth_index": "fixture",
            "credential_fingerprints": ("fixture",),
            "status": EnvironmentStatus.COOLING_DOWN,
            "last_error": "上游认证失效，请重新认证",
        }
    )
    env = FallbackEnvironments(record)
    up = FallbackUpstream()
    policy = FallbackPolicies(False)
    service = module.AccessTokenFallbackService(env, policy, up, FallbackOwnership(), FallbackLeases())
    denied = await service.verify(record.id, "model-a")
    assert not denied.restored and up.calls == [] and env.record == record
    policy.view = FallbackPolicies().view
    restored = await service.verify(record.id, "model-a")
    assert restored.restored and env.record.status == EnvironmentStatus.READY
    assert env.record.access_token_fallback.usable_for("model-a", utc_now())
    assert up.calls == ["quota", "verify", "status"]
    env.record = record
    up.during = lambda: setattr(
        env, "record", record.model_copy(update={"version": record.version + 1, "enabled": False})
    )
    raced = await service.verify(record.id, "model-a")
    assert not raced.restored and env.record.enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "block", ["ownership", "stale_quota", "expired_window", "empty_quota", "exhausted_model", "excluded_model"]
)
async def test_fallback_rejects_independent_blocks_before_inference(block):
    from account_pool.access_token_fallback import AccessTokenFallbackService

    record = _record().model_copy(
        update={"auth_file_name": "fixture.json", "auth_index": "fixture", "credential_fingerprints": ("fixture",)}
    )
    environments = FallbackEnvironments(record)
    upstream = FallbackUpstream()
    ownership = FallbackOwnership(owned=block != "ownership")
    policies = FallbackPolicies()
    if block == "stale_quota":
        upstream.quota = upstream.quota.model_copy(update={"observed_at": utc_now() - timedelta(minutes=10)})
    if block == "expired_window":
        upstream.quota = upstream.quota.model_copy(
            update={
                "windows": (
                    upstream.quota.windows[0].model_copy(update={"resets_at": utc_now() - timedelta(seconds=1)}),
                )
            }
        )
    if block == "empty_quota":
        upstream.quota = QuotaSnapshot()
    if block == "exhausted_model":
        upstream.models = (
            domain.ModelQuotaSnapshot(
                model="model-a",
                quota=upstream.quota.model_copy(
                    update={"windows": (upstream.quota.windows[0].model_copy(update={"remaining_percent": 0}),)}
                ),
            ),
        )
    if block == "excluded_model":
        policies.view.policy = policies.view.policy.model_copy(update={"excluded_models": ("model-a",)})
    result = await AccessTokenFallbackService(environments, policies, upstream, ownership, FallbackLeases()).verify(
        record.id, "model-a"
    )
    assert not result.restored
    assert "verify" not in upstream.calls
    assert environments.record == record


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability",
    [
        None,
        {},
        {"supported": True, "version": True, "token_refresh": False},
        {"supported": True, "version": 1, "token_refresh": True},
    ],
)
async def test_fallback_capability_rejects_before_policy_writes(capability):
    import httpx
    from account_pool.channels.cliproxyapi.client import HttpCLIProxyClient
    from account_pool.channels.cliproxyapi.settings_sync import CLIProxySettingsSynchronizer
    from account_pool.shared.secrets import EnvironmentSecretDeriver
    from account_pool.policies import PolicyRuntimeRejected

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404 if capability is None else 200, json=capability)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HttpCLIProxyClient(EnvironmentSecretDeriver("fixture-secret" * 3), http)
        with pytest.raises(PolicyRuntimeRejected):
            await CLIProxySettingsSynchronizer(client).apply_policy(
                _record().model_copy(update={"auth_file_name": "fixture.json"}),
                AccountPolicy(codex=CodexPolicy(allow_valid_access_after_refresh_failure=True)),
            )
    assert [(r.method, r.url.path) for r in calls] == [("GET", "/v0/management/codex/access-token-fallback")]


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining,reset_past,expected", [(100, False, True), (100, True, True), (0, True, True), (0, False, False)])
async def test_fallback_verifies_with_fresh_account_quota_and_historical_model_cache(remaining, reset_past, expected):
    from account_pool.access_token_fallback import AccessTokenFallbackService

    historical = domain.ModelQuotaSnapshot(model="model-a", quota=QuotaSnapshot(
        observed_at=utc_now() - timedelta(days=1),
        windows=(QuotaWindow(name="5h", remaining_percent=remaining, used_percent=100-remaining,
            window_minutes=300, resets_at=utc_now()+timedelta(hours=-1 if reset_past else 1)),),
    ))
    record = _record().model_copy(update={"auth_file_name":"fixture.json", "auth_index":"fixture",
        "credential_fingerprints":("fixture",), "model_quotas":(historical,)})
    environments = FallbackEnvironments(record)
    upstream = FallbackUpstream()
    result = await AccessTokenFallbackService(environments, FallbackPolicies(), upstream, FallbackOwnership(), FallbackLeases()).verify(record.id, "model-a")
    assert result.restored is expected
    assert ("verify" in upstream.calls) is expected
    assert environments.record.model_quotas == (historical,)
    if expected:
        assert environments.record.access_token_fallback.usable_for("model-a", utc_now())


@pytest.mark.asyncio
async def test_discovery_proves_enabled_models_without_manual_model_test():
    from account_pool.access_token_fallback import AccessTokenFallbackService

    class DiscoveryUpstream(FallbackUpstream):
        def __init__(self):
            super().__init__()
            self.verified = []
            self.attempted = []

        async def verify_access_fallback(self, record, model):
            self.attempted.append(model)
            if model == "unavailable":
                return domain.AccessTokenFallbackVerification(passed=False, restored=False, reason="incomplete")
            self.verified.append(model)
            return await super().verify_access_fallback(record, model)

        async def access_fallback_status(self, record):
            return (await super().access_fallback_status(record)).model_copy(update={"verified_models": tuple(self.verified)})

    record = _record().model_copy(update={
        "auth_file_name": "fixture.json", "auth_index": "fixture", "credential_fingerprints": ("fixture",),
        "enabled_models": ("model-a", "unavailable", "model-b"),
        "access_token_fallback": domain.AccessTokenFallbackStatus(enabled=True, failure_source="refresh", expires_at=utc_now()+timedelta(hours=1)),
    })
    env, up = FallbackEnvironments(record), DiscoveryUpstream()
    service = AccessTokenFallbackService(env, FallbackPolicies(), up, FallbackOwnership(), FallbackLeases())
    await service.discover()
    assert env.record.access_token_fallback.verified_models == ("model-a", "model-b")
    assert not env.record.access_token_fallback.usable_for("unavailable", utc_now())
    assert env.record.enabled_models == record.enabled_models
    up.attempted.clear()
    await service.discover()
    assert up.attempted == ["unavailable"]


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [
    {"enabled": False}, {"manual_cooldown": True}, {"configuration_pending": True},
    {"access_token_fallback": domain.AccessTokenFallbackStatus(enabled=False, failure_source="refresh")},
    {"access_token_fallback": domain.AccessTokenFallbackStatus(enabled=True, failure_source="access")},
    {"access_token_fallback": domain.AccessTokenFallbackStatus(enabled=True, failure_source="refresh", expires_at=utc_now()-timedelta(minutes=1))},
])
async def test_automatic_discovery_never_bypasses_independent_restrictions(updates):
    from account_pool.access_token_fallback import AccessTokenFallbackService
    record = _record().model_copy(update={
        "auth_file_name": "fixture.json", "auth_index": "fixture", "credential_fingerprints": ("fixture",),
        "access_token_fallback": domain.AccessTokenFallbackStatus(enabled=True, failure_source="refresh", expires_at=utc_now()+timedelta(hours=1)),
        **updates,
    })
    up = FallbackUpstream()
    await AccessTokenFallbackService(FallbackEnvironments(record), FallbackPolicies(), up, FallbackOwnership(), FallbackLeases()).discover()
    assert up.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("default_policy", [False, True])
async def test_fallback_reuses_current_model_proof_but_preserves_independent_limits(default_policy):
    from account_pool.access_token_fallback import AccessTokenFallbackService
    record = _record().model_copy(update={"auth_file_name": "fixture.json", "auth_index": "fixture", "credential_fingerprints": ("fixture",)})
    env, up, policies = FallbackEnvironments(record), FallbackUpstream(), FallbackPolicies()
    if default_policy:
        policies.view.policy = AccountPolicy()
    service = AccessTokenFallbackService(env, policies, up, FallbackOwnership(), FallbackLeases())
    first = await service.verify(record.id, "model-a")
    assert first.restored
    up.calls.clear()
    second = await service.verify(record.id, "model-a")
    assert second.passed and second.restored
    assert up.calls == []
    policies.view = FallbackPolicies(False).view
    denied = await service.verify(record.id, "model-a")
    assert not denied.restored and up.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["expiry", "cooldown", "quota", "model_quota"])
async def test_current_model_proof_never_bypasses_new_restrictions(restriction):
    from account_pool.access_token_fallback import AccessTokenFallbackService
    record = _record().model_copy(update={"auth_file_name": "fixture.json", "auth_index": "fixture", "credential_fingerprints": ("fixture",)})
    env, up = FallbackEnvironments(record), FallbackUpstream()
    service = AccessTokenFallbackService(env, FallbackPolicies(), up, FallbackOwnership(), FallbackLeases())
    assert (await service.verify(record.id, "model-a")).restored
    empty = up.quota.model_copy(update={"windows": (QuotaWindow(name="5h", window_minutes=300, remaining_percent=0, used_percent=100, resets_at=utc_now()+timedelta(hours=1)),)})
    if restriction == "expiry":
        env.record = env.record.model_copy(update={"access_token_fallback": env.record.access_token_fallback.model_copy(update={"expires_at": utc_now()-timedelta(minutes=1)})})
        up.quota = empty
    elif restriction == "cooldown":
        env.record = env.record.model_copy(update={"manual_cooldown": True})
    elif restriction == "quota":
        env.record = env.record.model_copy(update={"quota": empty})
        up.quota = empty
    else:
        env.record = env.record.model_copy(update={"model_quotas": (domain.ModelQuotaSnapshot(model="model-a", quota=empty),)})
    up.calls.clear()
    assert not (await service.verify(record.id, "model-a")).restored
    assert "verify" not in up.calls
