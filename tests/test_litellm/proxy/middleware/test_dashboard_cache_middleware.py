from pathlib import Path
from typing import Final

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from litellm.proxy.middleware.dashboard_cache_middleware import DashboardCacheMiddleware


@pytest.mark.parametrize("prefix", ("/ui/_next", "/_next", "/litellm-asset-prefix/_next"))
@pytest.mark.parametrize("root", ("", "/proxy"))
def test_static_cache_policy(tmp_path: Path, prefix: str, root: str) -> None:
    assets: Final = tmp_path / "static"
    assets.mkdir()
    (assets / "page-12345678.js").write_text("test", encoding="utf-8")
    (assets / "font.abcdef12.woff2").write_bytes(b"font")
    (tmp_path / "index.html").write_text("<html>test</html>", encoding="utf-8")
    (tmp_path / "route.txt").write_text("rsc", encoding="utf-8")
    app: Final = Starlette(routes=[Mount(prefix, app=StaticFiles(directory=tmp_path, html=True))])
    client: Final = TestClient(DashboardCacheMiddleware(app, root_path=root), root_path=root)
    url: Final = f"{root}{prefix}/static/page-12345678.js"
    first: Final = client.get(url)
    policy: Final = "no-cache" if root else "public, max-age=31536000, immutable"
    assert first.status_code == 200
    assert first.headers["cache-control"] == policy
    assert client.head(url).headers["cache-control"] == policy
    cached: Final = client.get(url, headers={"If-None-Match": first.headers["etag"]})
    assert cached.status_code == 304
    assert cached.headers["cache-control"] == policy
    assert "immutable" in client.get(f"{root}{prefix}/static/font.abcdef12.woff2").headers["cache-control"]
    assert client.get(f"{root}{prefix}/index.html").headers["cache-control"] == "no-cache"
    assert client.get(f"{root}{prefix}/route.txt").headers["cache-control"] == "no-cache"
    missing: Final = client.get(f"{root}{prefix}/static/missing-abcdef12.js")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("status", (200, 401, 403, 500))
@pytest.mark.parametrize("path", ("/key/list", "/account_pool/environments", "/.well-known/litellm-ui-config"))
def test_management_responses_never_persist(path: str, status: int) -> None:
    async def endpoint(request: Request) -> Response:
        return Response(status_code=status, headers={"Cache-Control": "public, max-age=600"})

    app: Final = Starlette(routes=[Route(path, endpoint)])
    response: Final = TestClient(DashboardCacheMiddleware(app)).get(path, headers={"x-custom-key": "test"})
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ("/v1/chat/completions", "/account_pool/internal/forward/chat/completions"))
def test_inference_stream_and_cache_policy_are_unchanged(path: str) -> None:
    async def endpoint(request: Request) -> StreamingResponse:
        return StreamingResponse(iter((b"data: first\n\n", b"data: [DONE]\n\n")), headers={"Cache-Control": "private"})

    app: Final = Starlette(routes=[Route(path, endpoint, methods=["POST"])])
    response: Final = TestClient(DashboardCacheMiddleware(app)).post(path)
    assert response.headers["cache-control"] == "private"
    assert response.content == b"data: first\n\ndata: [DONE]\n\n"
