"""固定读取、显式应用和旧版本请求，核验管理源文件副本，不触达真实服务。"""
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

spec = importlib.util.spec_from_file_location("activation_management_copy", Path(sys.argv[1]))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
r1, r2 = ['"sha256-' + value * 64 + '"' for value in "ab"]
state = {"persisted_revision": r2, "active_revision": r1, "apply_supported": True, "blocked_reason": None}
calls = []

async def request_manager(method, path, body):
    calls.append(method + " " + path)
    if method == "POST":
        if json.loads(body)["revision"] != state["persisted_revision"]:
            return httpx.Response(409, json={"detail": "revision_conflict"})
        state["active_revision"] = r2
    return httpx.Response(200, json={**state, "secret": "PRIVATE"})

app = FastAPI()
app.include_router(module.create_management_router(request_manager, lambda user: None), prefix="/account_pool")
app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
with TestClient(app) as client:
    before = client.get("/account_pool/cockpit/policy/runtime")
    applied = client.post("/account_pool/cockpit/policy/apply", json={"revision": r2})
    stale = client.post("/account_pool/cockpit/policy/apply", json={"revision": r1})
print(json.dumps({"input": {"apply": r2, "stale": r1}, "read_status": before.status_code,
    "apply_status": applied.status_code, "stale_status": stale.status_code,
    "before": before.json(), "after": applied.json(), "calls": calls,
    "secret_exposed": "PRIVATE" in applied.text}, sort_keys=True))
