"""绑定号池入口协议并选择同协议转发，不绕过原生鉴权、计费或网关租约。"""
from __future__ import annotations

import sys
from typing import Final, Literal
from uuid import UUID

Protocol = Literal["chat", "responses", "messages", "images"]


def request_protocol(path: str) -> Protocol | None:
    normalized: Final = path.removeprefix("/v1")
    if normalized == "/chat/completions":
        return "chat"
    if normalized in ("/responses", "/responses/compact", "/realtime"):
        return "responses"
    if normalized == "/messages":
        return "messages"
    if normalized.startswith("/images/"):
        return "images"
    return None


def passthrough_for_base(api_base: str | None) -> bool:
    if not api_base or "litellm.proxy.management_endpoints.account_pool_native_routing" not in sys.modules:
        return False
    from litellm.proxy.management_endpoints.account_pool_integration import forwarding_base, pool_identity
    from litellm.proxy.management_endpoints.account_pool_native_routing import snapshots

    identity: Final = pool_identity.get()
    if identity is None:
        return False
    return any(
        snapshot.passthrough_enabled
        and (identity.card_id is None or str(identity.card_id) == account)
        and api_base.rstrip("/") == forwarding_base(UUID(account))
        for account, snapshot in snapshots.values.items()
    )
