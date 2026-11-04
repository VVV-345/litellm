"""单卡刷新使用合成凭据验证版本、归属和删除边界，不连接真实供应商。"""

import importlib
import pytest
from account_pool.domain import EnvironmentStatus
from test_cliproxy_supplier_client import _record
from test_access_token_fallback import FallbackOwnership, FallbackEnvironments


class Upstream:
    def __init__(self):
        self.calls = 0

    async def read_credential_identity(self, record):
        from account_pool.credential_ownership import CredentialIdentity

        return CredentialIdentity(("rotated-fixture",), "fixture@example.invalid", "fixture-account")

    async def refresh_credentials(self, record):
        self.calls += 1
        from account_pool.credential_refresh import CredentialRefreshResult

        return CredentialRefreshResult(saved=True, reason="saved")


@pytest.mark.asyncio
async def test_refresh_requires_current_version_and_never_revives_deleted_card():
    try:
        module = importlib.import_module("account_pool.credential_refresh")
    except ModuleNotFoundError:
        pytest.fail("credential refresh service missing")
    card = _record().model_copy(
        update={"auth_file_name": "fixture.json", "auth_index": "fixture", "credential_fingerprints": ("fixture",)}
    )
    env, up = FallbackEnvironments(card), Upstream()
    service = module.CredentialRefreshService(env, FallbackOwnership(), up)
    result = await service.refresh(card.id, card.version)
    assert result.saved and up.calls == 1
    assert env.record.version == card.version + 1
    assert env.record.status == card.status
    denied = await service.refresh(card.id, card.version)
    assert not denied.saved and up.calls == 1
    env.record = card.model_copy(update={"status": EnvironmentStatus.DELETING})
    denied = await service.refresh(card.id, card.version)
    assert not denied.saved and up.calls == 1


@pytest.mark.asyncio
async def test_refresh_does_not_claim_inference_or_clear_limits():
    module = importlib.import_module("account_pool.credential_refresh")
    card = _record().model_copy(
        update={
            "auth_file_name": "fixture.json",
            "auth_index": "fixture",
            "credential_fingerprints": ("fixture",),
            "status": EnvironmentStatus.COOLING_DOWN,
            "manual_cooldown": True,
            "last_error": "quota",
        }
    )
    env, up = FallbackEnvironments(card), Upstream()
    result = await module.CredentialRefreshService(env, FallbackOwnership(), up).refresh(card.id, card.version)
    assert result.saved
    assert env.record.status == card.status and env.record.manual_cooldown
    assert env.record.last_error == "quota"


@pytest.mark.asyncio
async def test_refresh_commits_rotated_identity_without_reenabling_card():
    from account_pool.credential_ownership import CredentialOwnership
    from account_pool.credential_refresh import CredentialRefreshService

    card = _record().model_copy(
        update={
            "auth_file_name": "fixture.json",
            "auth_index": "fixture",
            "credential_fingerprints": ("old-fixture",),
            "status": EnvironmentStatus.COOLING_DOWN,
            "manual_cooldown": True,
        }
    )
    env, up, ownership = FallbackEnvironments(card), Upstream(), CredentialOwnership()
    await ownership.claim(card.id, card.credential_fingerprints)
    result = await CredentialRefreshService(env, ownership, up).refresh(card.id, card.version)
    assert result.saved
    assert env.record.credential_fingerprints == ("rotated-fixture",)
    assert await ownership.owns(card.id, env.record.credential_fingerprints)
    assert not await ownership.owns(card.id, card.credential_fingerprints)
    assert env.record.manual_cooldown and env.record.status == card.status


@pytest.mark.asyncio
async def test_refresh_transport_rebinds_rotated_file_and_allows_next_identity_check():
    import json
    import httpx
    from account_pool.credential_ownership import CredentialOwnership, credential_identity
    from account_pool.credential_refresh import CredentialRefreshService
    from account_pool.channels.cliproxyapi.quota_reset import CodexResetClient
    from account_pool.channels.cliproxyapi.transport import CLIProxyTransport
    from account_pool.shared.secrets import EnvironmentSecretDeriver
    secrets = EnvironmentSecretDeriver("s" * 32)
    files = [{"access_token":"fixture-access-old", "refresh_token":"fixture-refresh-old", "email":"fixture@example.invalid"}]
    calls = []
    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/auth-files"):
            return httpx.Response(200,json={"files":[{"name":"fixture.json","auth_index":"fixture"}]})
        if request.url.path.endswith("/download"):
            return httpx.Response(200,json=files[-1])
        assert request.method == "POST" and request.url.path.endswith("/credential-refresh")
        files.append({**files[-1],"access_token":"fixture-access-new","refresh_token":"fixture-refresh-new"})
        return httpx.Response(200,json={"saved":True,"reason":"saved","status":{"last_result":"saved"}})
    ownership = CredentialOwnership()
    card = _record().model_copy(update={"auth_file_name":"fixture.json","auth_index":"fixture"})
    identity = credential_identity(json.dumps(files[0]).encode(), card.supplier.value,secrets)
    card = card.model_copy(update={"credential_fingerprints":identity.fingerprints})
    await ownership.claim(card.id, identity.fingerprints)
    env = FallbackEnvironments(card)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        upstream = CodexResetClient(http,secrets)
        result = await CredentialRefreshService(env,ownership,upstream).refresh(card.id,card.version)
        assert result.saved
        _,credential = await upstream._credential(env.record,CLIProxyTransport(http,secrets))
        assert credential.access_token == "fixture-access-new"
    assert sum(method=="POST" for method,_ in calls)==1
    assert env.record.credential_fingerprints != identity.fingerprints
