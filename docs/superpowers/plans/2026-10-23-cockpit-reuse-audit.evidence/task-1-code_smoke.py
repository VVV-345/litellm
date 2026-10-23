import importlib.util, json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
source=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('cockpit_transaction_endpoints',source)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def factory():
    return module.AccountPoolManagerClient('http://manager.test','m'*32,client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200,json={'configured':True,'account_count':2,'enabled_key_count':1,'credential_count':2,
        'persisted_manifest_revision':'"sha256-'+'a'*64+'"','runtime_activation':'restart_required_or_unverified','secret':'not-public'}))))
app=FastAPI()
app.include_router(module.create_account_pool_router(factory))
app.dependency_overrides[user_api_key_auth]=lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
with TestClient(app) as c:
    r=c.get('/account_pool/cockpit/status')
print(json.dumps({'request':'GET /account_pool/cockpit/status','status':r.status_code,'body':r.json(),'cache_control':r.headers.get('cache-control'),'secret_exposed':'not-public' in r.text},ensure_ascii=False,sort_keys=True))
