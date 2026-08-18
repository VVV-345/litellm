"""封装 GLM 官方 HTTP 调用，并限制目标为官方域名。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from account_pool.provider_services.glm.manifest import GLM_OFFICIAL_API_BASE
from account_pool.provider_services.glm.schemas import GlmModelList


@dataclass(frozen=True, slots=True)
class GlmModelsSuccess:
    api_base: str
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GlmModelsFailure:
    api_base: str
    message: str


GlmModelsResult = GlmModelsSuccess | GlmModelsFailure


def normalize_glm_api_base(value: str) -> str | None:
    candidate: Final = value.strip().rstrip("/") or GLM_OFFICIAL_API_BASE
    parsed: Final = urlparse(candidate)
    if parsed.scheme != "https" or parsed.netloc != "open.bigmodel.cn":
        return None
    if parsed.path.rstrip("/") != "/api/paas/v4" or parsed.query or parsed.fragment:
        return None
    return candidate


async def fetch_glm_models(client: httpx.AsyncClient, api_base: str, api_key: str) -> GlmModelsResult:
    normalized: Final = normalize_glm_api_base(api_base)
    if normalized is None:
        return GlmModelsFailure(
            api_base=api_base,
            message="GLM 官方模块仅允许 https://open.bigmodel.cn/api/paas/v4",
        )
    try:
        response: Final = await client.get(
            f"{normalized}/models",
            headers={"authorization": f"Bearer {api_key}", "accept": "application/json"},
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return GlmModelsFailure(api_base=normalized, message="无法连接 GLM 官方模型列表接口")
    if response.status_code in {401, 403}:
        return GlmModelsFailure(api_base=normalized, message="GLM API Key 无效或没有模型列表权限")
    if response.status_code >= 400:
        return GlmModelsFailure(api_base=normalized, message=f"GLM 模型列表返回 HTTP {response.status_code}")
    try:
        payload: Final = GlmModelList.model_validate_json(response.content)
    except ValidationError:
        return GlmModelsFailure(api_base=normalized, message="GLM 模型列表响应格式无法识别")
    models: Final = tuple(sorted(frozenset(item.id for item in payload.data if item.id)))
    if not models:
        return GlmModelsFailure(api_base=normalized, message="GLM API Key 当前没有可见模型")
    return GlmModelsSuccess(api_base=normalized, models=models)
