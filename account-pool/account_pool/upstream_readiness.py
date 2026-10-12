"""只读检查两类上游工作流的分支和注册状态，不把 Token 存在误认为配置完成。"""

from dataclasses import dataclass
from typing import Final
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError


class Commit(BaseModel):
    sha: str


class Branch(BaseModel):
    commit: Commit


class Workflow(BaseModel):
    state: str


@dataclass(frozen=True, slots=True)
class Readiness:
    issues: tuple[str, ...]
    stale: bool


async def inspect_readiness(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    repository: str,
    sync_branch: str,
    production_branch: str,
    workflow: str,
    workflow_ref: str,
    has_token: bool,
    base_sha: str | None,
) -> Readiness:
    async def get(path: str, ref: str | None = None) -> httpx.Response:
        return await client.get(
            f"/repos/{repository}/{path}",
            headers=headers,
            params={} if ref is None else {"ref": ref},
        )

    if not has_token:
        return Readiness(("token_missing",), False)
    if production_branch == "CLIProxyAPI分支" and (
        repository != "VVV-345/litellm"
        or workflow_ref != production_branch
        or sync_branch != "codex/litellm-upstream-sync"
        or workflow != "litellm-upstream-sync.yml"
    ):
        return Readiness(("workflow_target_mismatch",), True)
    try:
        production: Final = await get("branches/" + quote(production_branch, safe=""))
        sync: Final = await get("branches/" + quote(sync_branch, safe=""))
        source: Final = await get("contents/.github/workflows/" + quote(workflow, safe=""), workflow_ref)
        registered: Final = await get("actions/workflows/" + quote(workflow, safe=""))
        if any(response.status_code not in (200, 404) for response in (production, sync, source, registered)):
            return Readiness(("configuration_unavailable",), True)
        checks: Final = (
            ("production_branch_missing", production.is_success),
            ("sync_branch_missing", sync.is_success),
            ("workflow_missing", source.is_success),
            (
                "workflow_not_registered",
                registered.is_success and Workflow.model_validate_json(registered.content).state == "active",
            ),
        )
        issues: Final = tuple(name for name, passed in checks if not passed)
        stale: Final = bool(
            base_sha and production.is_success and Branch.model_validate_json(production.content).commit.sha != base_sha
        )
        return Readiness(issues, stale)
    except (httpx.HTTPError, ValidationError):
        return Readiness(("configuration_unavailable",), True)
