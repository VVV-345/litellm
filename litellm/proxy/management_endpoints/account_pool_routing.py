"""本模块执行卡片范围内的模型与路由策略，不连接网络或保存用户请求。"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final
from urllib.parse import urlsplit

from starlette.datastructures import Headers

from litellm.proxy.management_endpoints.account_pool_gateway_contracts import Candidate, Resolution
from litellm.proxy.management_endpoints.account_pool_management_models import AccountPolicy


@dataclass(frozen=True, slots=True)
class Route:
    account: Candidate
    model: str


@dataclass(frozen=True, slots=True)
class Rejected:
    status: int
    message: str


def protocol_policy(
    policy: AccountPolicy,
    path: str,
    headers: Headers,
    image_generation: bool,
) -> Rejected | None:
    if (path.startswith("/v1/images/") or image_generation) and policy.transport.image_generation == "disabled":
        return Rejected(403, "Image generation is disabled for this account")
    codex: Final = policy.codex
    if codex is None:
        return None
    if codex.identity_fingerprint_mode != "off":
        return Rejected(501, "The installed upstream adapter does not support the requested identity mode")
    if path == "/v1/responses/compact" and not codex.responses_compact_enabled:
        return Rejected(403, "Responses Compact is disabled for this account")
    if not codex.cli_only:
        return None
    agent: Final = headers.get("user-agent", "").lower()
    originator: Final = headers.get("originator", "").lower()
    official: Final = agent.startswith(("codex_cli_rs/", "codex-tui/")) or originator in ("codex_cli_rs", "codex-tui")
    app_server: Final = codex.allow_app_server and (
        originator == "codex_app" or originator in codex.allow_app_server_clients
    )
    return None if official or app_server else Rejected(403, "This account requires an allowed Codex client")


def target_model(policy: AccountPolicy, model: str) -> str:
    return next((item.target for item in policy.model_aliases if item.alias == model), model)


def quota_available(account: Candidate, policy: AccountPolicy) -> bool:
    reserve: Final = max(policy.routing.quota_reserve_percent, account.policy.routing.quota_reserve_percent)
    if account.remaining_percent is not None and account.remaining_percent <= reserve:
        return False
    if reserve == 0:
        return True
    max_age: Final = min(policy.routing.quota_snapshot_max_age, account.policy.routing.quota_snapshot_max_age)
    return (
        account.remaining_percent is not None
        and account.quota_observed_at is not None
        and (datetime.now(timezone.utc) - account.quota_observed_at).total_seconds() <= max_age
    )


def routes(
    resolution: Resolution,
    model: str,
    path: str,
    headers: Headers,
    image_generation: bool = False,
) -> tuple[Route, ...] | Rejected:
    card_codex: Final = resolution.policy.codex
    if path == "/v1/responses/compact" and (
        card_codex is None or not card_codex.responses_compact_enabled
    ):
        return Rejected(403, "Responses Compact is disabled for this card")
    rejected: Final = protocol_policy(resolution.policy, path, headers, image_generation)
    if rejected:
        return rejected
    if resolution.policy.routing.strategy in ("plan", "expiry"):
        return Rejected(501, "Plan and subscription expiry routing require verified provider metadata")
    target: Final = target_model(resolution.policy, model)
    if model in resolution.policy.excluded_models or target in resolution.policy.excluded_models:
        return Rejected(403, "Model is excluded by the card policy")
    eligible: Final = tuple(
        Route(account, mapped)
        for account in resolution.candidates
        if (mapped := target_model(account.policy, target)) in account.enabled_models
        and model not in account.policy.excluded_models
        and target not in account.policy.excluded_models
        and mapped not in account.policy.excluded_models
        and quota_available(account, resolution.policy)
        and protocol_policy(account.policy, path, headers, image_generation) is None
    )
    if not eligible:
        return Rejected(503, "No bound account currently supports this model and policy")
    preferred: Final = resolution.policy.routing.preferred_account_ids

    def rank(route: Route) -> tuple[bool, bool, int, float]:
        account: Final = route.account
        priority: Final = float(-account.policy.routing.priority)
        fresh: Final = (
            account.quota_observed_at is not None
            and (datetime.now(timezone.utc) - account.quota_observed_at).total_seconds()
            <= resolution.policy.routing.quota_snapshot_max_age
        )
        quota: Final = -account.remaining_percent if fresh and account.remaining_percent is not None else 1.0
        preference: Final = preferred.index(account.id) if account.id in preferred else len(preferred)
        return (
            account.id != resolution.sticky_account_id,
            account.policy.routing.is_backup,
            preference,
            quota if resolution.policy.routing.strategy == "quota" else priority,
        )

    ordered: Final = tuple(sorted(eligible, key=rank))
    if resolution.policy.routing.strategy != "random" or resolution.sticky_account_id == ordered[0].account.id:
        return ordered
    primaries: Final = tuple(route for route in ordered if not route.account.policy.routing.is_backup) or ordered
    chosen: Final = random.choices(
        primaries, weights=tuple(route.account.policy.routing.weight for route in primaries), k=1
    )[0]
    return (chosen, *(route for route in ordered if route.account.id != chosen.account.id))


def upstream_url(account: Candidate, path: str) -> str:
    base: Final = urlsplit(account.api_base)
    prefix: Final = "cliproxy" if account.channel == "cliproxyapi" else "freebuff"
    port: Final = 8317 if account.channel == "cliproxyapi" else 8787
    if base.scheme != "http" or base.hostname != f"{prefix}-{account.id.hex}" or base.port != port:
        raise ValueError("Unexpected account pool target")
    if base.username or base.password or base.query or base.fragment or base.path not in ("", "/v1"):
        raise ValueError("Unexpected account pool target path")
    return f"http://{prefix}-{account.id.hex}:{port}{path}"
