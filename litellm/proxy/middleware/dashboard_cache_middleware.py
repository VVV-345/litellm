import re
from typing import Final

from pydantic import TypeAdapter
from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from litellm.proxy._types import LiteLLMRoutes

_MANAGEMENT_ROUTES: Final = frozenset(
    (*LiteLLMRoutes.management_routes.value, *LiteLLMRoutes.info_routes.value, *LiteLLMRoutes.admin_viewer_routes.value)
)
_VERSIONED_ASSET: Final = re.compile(r"(?:^|[-.])[0-9a-f]{8,}(?:[-.]|$)")
_MANAGEMENT_PATTERNS: Final = tuple(compile_path(route)[0] for route in _MANAGEMENT_ROUTES if "{" in route)
_BINARY_SUFFIXES: Final = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot")
_PATH: Final = TypeAdapter(str)
_STATUS: Final = TypeAdapter(int)
_HEADERS: Final = TypeAdapter(tuple[tuple[bytes, bytes], ...])


class DashboardCacheMiddleware:
    def __init__(self, app: ASGIApp, root_path: str = "") -> None:
        self.app = app
        self.root_path = root_path.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_path: Final = _PATH.validate_python(scope["path"])
        path: Final = (
            raw_path[len(self.root_path):]
            if self.root_path and raw_path.startswith(self.root_path + "/")
            else raw_path
        )
        static: Final = path == "/ui" or path.startswith(("/ui/", "/_next/", "/litellm-asset-prefix/_next/"))
        management: Final = (
            path in _MANAGEMENT_ROUTES
            or any(pattern.fullmatch(path) for pattern in _MANAGEMENT_PATTERNS)
            or (path.startswith("/account_pool/") and not path.startswith("/account_pool/internal/"))
            or path in ("/.well-known/litellm-ui-config", "/litellm/.well-known/litellm-ui-config")
        )
        if not static and not management:
            await self.app(scope, receive, send)
            return

        async def send_with_cache_policy(message: Message) -> None:
            if message["type"] != "http.response.start":
                await send(message)
                return
            status: Final = _STATUS.validate_python(message["status"])
            versioned: Final = "/_next/static/" in path and _VERSIONED_ASSET.search(path.rsplit("/", 1)[-1]) is not None
            immutable: Final = versioned and (not self.root_path or path.endswith(_BINARY_SUFFIXES))
            policy: Final = (
                b"no-store"
                if management or status >= 400 or scope["method"] not in ("GET", "HEAD")
                else b"public, max-age=31536000, immutable"
                if immutable
                else b"no-cache"
            )
            headers: Final = [
                (name, value) for name, value in _HEADERS.validate_python(message.get("headers", ()))
                if name.lower() not in (b"cache-control", b"expires")
            ]
            await send({**message, "headers": [*headers, (b"cache-control", policy)]})

        await self.app(scope, receive, send_with_cache_policy)
