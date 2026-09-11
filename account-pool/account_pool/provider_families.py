"""本模块声明号池供应商家族目录，目录只描述能力，不保存凭据或运行状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from account_pool.domain import SupplierKind


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
        description="支持多 API Key、模型前缀和自定义模型的直连上游",
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
        supplier=None,
        authentication="供应商认证",
        available=False,
        description="等待 Gemini 适配器",
    ),
    ProviderFamily(
        kind="vertex",
        display_name="Vertex",
        supplier=None,
        authentication="服务账号 JSON",
        available=False,
        description="等待 Vertex 适配器",
    ),
    ProviderFamily(
        kind="interactions_api",
        display_name="Interactions API",
        supplier=None,
        authentication="供应商认证",
        available=False,
        description="等待独立协议适配器",
    ),
)
