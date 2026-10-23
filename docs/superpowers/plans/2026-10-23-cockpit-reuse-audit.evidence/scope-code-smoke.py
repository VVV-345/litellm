"""对源码副本运行固定预检输入，只使用内存仓库和模拟管理传输。"""
import asyncio
import importlib.util
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

root = Path.cwd()
capsule = Path(sys.argv[1])
sys.path[:0] = [str(root), str(root / 'account-pool'), str(root / 'account-pool/tests'),
               str(root / 'account-pool/tests/account_pool')]
from account_pool.card_keys import CardKeyService, issue_card_key
from account_pool.cockpit_admin import CockpitAdminClient, CockpitAdminConfig
from account_pool.domain import EnvironmentStatus, utc_now
from test_account_pool import MemoryRepository, _record
from test_management import MemoryKeys, MemoryPolicies
from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_cockpit_subject import NativeSubjectResult


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, capsule / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


endpoint = load('scope_endpoint_copy', 'litellm/proxy/management_endpoints/account_pool_endpoints.py')
preflight = load('scope_preflight_copy', 'account-pool/account_pool/cockpit_preflight.py')
a = UUID('00000000-0000-0000-0000-000000000001')
b = UUID('00000000-0000-0000-0000-000000000002')
revision = '"sha256-' + 'a' * 64 + '"'
calls = []


async def subject_reader(_):
    return NativeSubjectResult(status='verified', card_id=a, checked_at=datetime(2026, 10, 23, tzinfo=timezone.utc))


def manager_factory():
    def handler(request):
        calls.append({'path': request.url.path, 'body': json.loads(request.content)})
        return httpx.Response(200, json={
            'card_id': str(a), 'subject_kind': 'litellm_key', 'authorized_account_ids': [str(a)],
            'scope_valid': True, 'source_strategy': 'auto', 'policy_version': 0, 'settings_version': None,
            'persisted_manifest_revision': revision, 'accounts': [], 'blockers': [], 'ready_to_migrate': False,
            'pending_checks': ['native_runtime_authorization_unverified'],
        })
    return endpoint.AccountPoolManagerClient('http://manager.test', 'm' * 32,
                                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


app = FastAPI()
kwargs = {'native_subject_reader': subject_reader} if 'native_subject_reader' in inspect.signature(endpoint.create_account_pool_router).parameters else {}
app.include_router(endpoint.create_account_pool_router(manager_factory, **kwargs))
app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
with TestClient(app) as client:
    native = client.post('/account_pool/cockpit/native-preflight', json={'key_hash': 'a' * 64})
    invalid = client.post('/account_pool/cockpit/native-preflight', json={'key_hash': 'sk-PRIVATE'})


async def credential_check():
    records = tuple(_record(status=EnvironmentStatus.READY).model_copy(update={
        'id': value, 'credential_fingerprints': ('shared',),
    }) for value in (a, b))
    environments = MemoryRepository(records[0])
    environments.records = {record.id: record for record in records}
    keys = MemoryKeys()
    issued = issue_card_key(a, b, utc_now()).record
    keys.records[a] = issued
    def handler(request):
        return httpx.Response(200, headers={'ETag': revision}, json={
            'accounts': [{'id': str(a), 'authId': 'private.json', 'accessTokenOnly': True},
                         {'id': str(b), 'authId': 'sub/PRIVATE.json'}],
        } if request.url.path.endswith('manifest') else {
            'files': [{'id': 'private.json', 'etag': revision, 'size': 20}],
        })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await preflight.cockpit_preflight(
            preflight.CockpitPreflightRequest(card_id=a, subject_kind='legacy_card_key', expected_key_id=issued.key_id),
            environments, MemoryPolicies(), CardKeyService(keys),
            CockpitAdminClient(CockpitAdminConfig(url='http://cockpit.test', token=SecretStr('s' * 32)), http),
        )
    return list(result.accounts[0].blockers)


print(json.dumps({'native_status': native.status_code, 'native': native.json(), 'invalid_status': invalid.status_code,
                  'invalid': invalid.json(), 'credential_blockers': asyncio.run(credential_check()),
                  'manager_calls': calls, 'secret_exposed': 'PRIVATE' in native.text + invalid.text}, sort_keys=True))
