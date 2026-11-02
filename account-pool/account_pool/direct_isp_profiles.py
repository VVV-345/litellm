"""将可直连 ISP 投影为代理出口，凭据仅在后端解析，不复制到普通代理目录。"""

from collections.abc import Sequence
from datetime import date
from typing import Final
from urllib.parse import quote

from account_pool.config import direct_isp_id, validate_proxy_profile_url
from account_pool.domain import ProxyProfile
from account_pool.ports import ProxyProfileRepository
from account_pool.proxy_catalog import ISPProxyCatalogProfile, ISPProxyProfile, direct_subscription_profile
from account_pool.proxy_catalog_repository import ProxyCatalogRepository


def direct_isp_url(profile: ISPProxyCatalogProfile) -> str | None:
    resolved: Final = (
        profile if isinstance(profile, ISPProxyProfile) else direct_subscription_profile(profile)
    )
    if resolved is None or not resolved.enabled or resolved.expired(date.today()):
        return None
    if any(character in resolved.host for character in "/@?#%\\"):
        return None
    host: Final = f"[{resolved.host}]" if ":" in resolved.host and not resolved.host.startswith("[") else resolved.host
    credentials: Final = (
        f"{quote(resolved.username, safe='')}:{quote(resolved.password, safe='')}@"
        if resolved.username or resolved.password else ""
    )
    try:
        return validate_proxy_profile_url(
            f"{resolved.protocol}://{credentials}{host}:{resolved.port}", profile_id=f"isp-direct-{resolved.id}"
        )
    except ValueError:
        return None


class DirectISPProxyProfiles:
    def __init__(self, profiles: ProxyProfileRepository, catalog: ProxyCatalogRepository) -> None:
        self._profiles: Final = profiles
        self._catalog: Final = catalog

    async def list(self) -> tuple[ProxyProfile, ...]:
        existing: Final = await self._profiles.list()
        isps: Final = await self._catalog.list_isps()
        return (
            *(profile for profile in existing if not profile.id.startswith("isp-direct-")),
            *(
                ProxyProfile(id=f"isp-direct-{profile.id}", name=profile.name, protocol=url.split(":", 1)[0])
                for profile in isps for url in (direct_isp_url(profile),) if url is not None
            ),
        )

    async def get_url(self, profile_id: str) -> str | None:
        if not profile_id.startswith("isp-direct-"):
            return await self._profiles.get_url(profile_id)
        identifier: Final = direct_isp_id(profile_id)
        if identifier is None:
            return None
        profile: Final = await self._catalog.get_isp(identifier)
        return None if profile is None else direct_isp_url(profile)

    async def upsert_gateways(self, gateways: Sequence[tuple[str, str, str]]) -> int:
        return await self._profiles.upsert_gateways(gateways)

    async def delete_gateway(self, profile_id: str) -> bool:
        return await self._profiles.delete_gateway(profile_id)
