from collections.abc import Sequence
from typing import Final

from litellm.types.utils import ProviderSpecificHeader


class ProviderSpecificHeaderUtils:
    @staticmethod
    def get_provider_specific_headers(
        provider_specific_header: ProviderSpecificHeader | Sequence[ProviderSpecificHeader] | None,
        custom_llm_provider: str | None,
    ) -> dict:
        """
        Get the provider specific headers for the given custom llm provider.

        Accepts either a single ProviderSpecificHeader or a sequence of them. Each entry
        carries its own comma-separated provider list, so headers that are safe for several
        providers and headers that are safe for exactly one can travel on the same request
        without sharing a scope. Entries whose provider list does not contain
        `custom_llm_provider` contribute nothing.

        Returns:
            Dict: The provider specific headers for the given custom llm provider
        """
        if provider_specific_header is None or custom_llm_provider is None:
            return {}

        scoped_headers: Final = (
            (provider_specific_header,) if isinstance(provider_specific_header, dict) else provider_specific_header
        )

        matched_headers: Final = {}
        for scoped_header in scoped_headers:
            stored_providers = scoped_header.get("custom_llm_provider", "")
            provider_list = [p.strip() for p in stored_providers.split(",")]
            if custom_llm_provider in provider_list:
                matched_headers.update(scoped_header.get("extra_headers", {}))

        return matched_headers

    @staticmethod
    def forwarded_authorization(provider_specific_header: object, custom_llm_provider: str | None) -> str | None:
        if custom_llm_provider is None or provider_specific_header is None:
            return None
        header: ProviderSpecificHeader | Sequence[ProviderSpecificHeader]
        if isinstance(provider_specific_header, dict):
            header = provider_specific_header
        elif isinstance(provider_specific_header, Sequence) and not isinstance(provider_specific_header, (str, bytes)):
            if not all(isinstance(item, dict) for item in provider_specific_header):
                return None
            header = provider_specific_header
        else:
            return None
        try:
            headers: Final = ProviderSpecificHeaderUtils.get_provider_specific_headers(header, custom_llm_provider)
        except (AttributeError, TypeError):
            return None
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == "authorization" and isinstance(value, str) and value:
                return value
        return None
