from typing import Final
from uuid import UUID, uuid5

_LEGACY_NAMESPACE: Final = UUID("6ad855d6-8eb9-5ef5-b52c-610a24a7fc55")


def legacy_channel_id(account_id: str) -> UUID:
    return uuid5(_LEGACY_NAMESPACE, f"channel:{account_id}")


def legacy_binding_id(channel_id: UUID, deployment_id: str) -> UUID:
    return uuid5(_LEGACY_NAMESPACE, f"binding:{channel_id}:{deployment_id}")
