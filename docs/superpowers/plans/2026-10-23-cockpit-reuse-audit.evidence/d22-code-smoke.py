"""用固定本地管理请求核验策略端点副本，不访问真实 Cockpit 或供应商。"""
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

spec = importlib.util.spec_from_file_location('d22_management_copy', Path(sys.argv[1]))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
revision = '"sha256-' + 'a' * 64 + '"'
state = {'revision': revision, 'routing_strategy': 'auto', 'custom_rules': [], 'account_ids': ['account-a'],
         'editable': True, 'blocked_reason': None}
calls = []
async def request_manager(method, path, body):
    calls.append(method + ' ' + path)
    if method == 'PUT':
        request = json.loads(body)
        if request['revision'] != state['revision']:
            return httpx.Response(409, json={'detail': 'revision_conflict'})
        state.update(request)
        state['revision'] = '"sha256-' + 'b' * 64 + '"'
    return httpx.Response(200, json={**state, 'secret': 'PRIVATE'})
app = FastAPI()
app.include_router(module.create_management_router(request_manager, lambda user: None), prefix='/account_pool')
app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
body = {'revision': revision, 'routing_strategy': 'custom', 'custom_rules': [
    {'account_id': 'account-a', 'priority': 3, 'weight': 2, 'is_backup': False, 'is_preferred': True}]}
with TestClient(app) as client:
    read = client.get('/account_pool/cockpit/policy')
    saved = client.put('/account_pool/cockpit/policy', json=body)
    stale = client.put('/account_pool/cockpit/policy', json=body)
print(json.dumps({'input': body, 'read_status': read.status_code, 'save_status': saved.status_code,
    'stale_status': stale.status_code, 'save_body': saved.json(), 'manager_calls': calls,
    'secret_exposed': 'PRIVATE' in saved.text}, sort_keys=True))
