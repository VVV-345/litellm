"""以固定管理请求验证端点副本；上游仅使用本地 HTTP fixture，不访问供应商。"""
import importlib.util
import json
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path.cwd()))
from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth

source = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('d2_transaction_endpoints', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
card = '00000000-0000-0000-0000-000000000001'
body = {'card_id': card, 'subject_kind': 'legacy_card_key', 'expected_key_id': card, 'candidate_ids': [card]}
calls = []
def handler(request):
    calls.append(request.method + ' ' + request.url.path)
    return httpx.Response(200, json={
        'card_id': card, 'subject_kind': 'legacy_card_key', 'authorized_account_ids': [card],
        'scope_valid': True, 'source_strategy': 'auto', 'policy_version': 1, 'settings_version': None,
        'persisted_manifest_revision': '"sha256-' + 'a' * 64 + '"', 'accounts': [],
        'blockers': ['strategy_mapping_unverified'], 'ready_to_migrate': False,
        'pending_checks': ['runtime_eligibility_unverified'], 'raw_manifest': 'PRIVATE',
    })
def factory():
    return module.AccountPoolManagerClient('http://manager.test', 'm' * 32,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
app = FastAPI()
app.include_router(module.create_account_pool_router(factory))
app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
with TestClient(app) as client:
    response = client.post('/account_pool/cockpit/preflight', json=body)
print(json.dumps({'input': body, 'http_status': response.status_code, 'body': response.json(),
    'manager_requests': calls, 'cache_control': response.headers.get('cache-control'),
    'secret_exposed': 'PRIVATE' in response.text}, sort_keys=True))
