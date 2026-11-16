"""本模块声明号池供应商家族目录，目录只描述能力，不保存凭据或运行状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from account_pool.domain import EnvironmentRecord, SupplierKind


@dataclass(frozen=True, slots=True)
class ProviderFamily:
    kind: str
    display_name: str
    supplier: SupplierKind | None
    authentication: str
    available: bool
    description: str


PROVIDER_FAMILIES: Final[tuple[ProviderFamily, ...]] = (
    ProviderFamily(
        kind="openai_compatible",
        display_name="OpenAI 兼容",
        supplier=SupplierKind.OPENAI_COMPATIBLE,
        authentication="Base URL + API Key",
        available=True,
        description="使用服务地址、API Key 和上游模型列表接入",
    ),
    ProviderFamily(
        kind="openai_codex",
        display_name="Codex",
        supplier=SupplierKind.OPENAI_CODEX,
        authentication="OAuth",
        available=True,
        description="CLIProxyAPI Codex OAuth",
    ),
    ProviderFamily(
        kind="anthropic_claude",
        display_name="Claude",
        supplier=SupplierKind.ANTHROPIC_CLAUDE,
        authentication="OAuth / API Key",
        available=True,
        description="CLIProxyAPI Claude 认证",
    ),
    ProviderFamily(
        kind="google_antigravity",
        display_name="Antigravity",
        supplier=SupplierKind.GOOGLE_ANTIGRAVITY,
        authentication="OAuth",
        available=True,
        description="CLIProxyAPI Antigravity OAuth",
    ),
    ProviderFamily(
        kind="kimi",
        display_name="Kimi",
        supplier=SupplierKind.KIMI,
        authentication="设备授权",
        available=True,
        description="CLIProxyAPI Kimi 设备授权",
    ),
    ProviderFamily(
        kind="xai",
        display_name="xAI",
        supplier=SupplierKind.XAI,
        authentication="OAuth / API Key",
        available=True,
        description="CLIProxyAPI xAI 认证",
    ),
    ProviderFamily(
        kind="gemini",
        display_name="Gemini",
        supplier=SupplierKind.GEMINI,
        authentication="API Key",
        available=True,
        description="CLIProxyAPI Gemini API Key",
    ),
    ProviderFamily(
        kind="vertex",
        display_name="Vertex",
        supplier=SupplierKind.VERTEX,
        authentication="服务账号 JSON",
        available=True,
        description="CLIProxyAPI Vertex 服务账号",
    ),
    ProviderFamily(
        kind="gemini_interactions",
        display_name="Interactions API",
        supplier=SupplierKind.GEMINI_INTERACTIONS,
        authentication="API Key",
        available=True,
        description="CLIProxyAPI Gemini Interactions API Key",
    ),
)

GLM_PROVIDER_FAMILY: Final = ProviderFamily(
    kind="glm",
    display_name="GLM（智谱 / Z.AI）",
    supplier=SupplierKind.OPENAI_COMPATIBLE,
    authentication="API Key",
    available=True,
    description="智谱国内站与 Z.AI 国际站的标准 API Key 接入",
)

_GLM_STANDARD_BASES: Final = frozenset(("https://open.bigmodel.cn/api/paas/v4", "https://api.z.ai/api/paas/v4"))


def provider_family_kind(record: EnvironmentRecord) -> str:
    config: Final = record.openai_compatible
    if config is not None and str(config.base_url).rstrip("/") in _GLM_STANDARD_BASES:
        return GLM_PROVIDER_FAMILY.kind
    return record.supplier.value
