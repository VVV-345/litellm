"""共享套餐模型归属判断，供成本与额度投影使用。"""

from __future__ import annotations

from account_pool.parsing.models import ModelIdentity, SubscriptionData, SubscriptionStatus


def subscription_includes_model(
    subscription: SubscriptionData | None,
    *,
    public_model: str,
    provider_model: str | None,
) -> bool:
    if subscription is None or subscription.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL):
        return False
    return any(
        _identity_matches(identity, public_model=public_model, provider_model=provider_model)
        for identity in subscription.models
    )


def _identity_matches(
    identity: ModelIdentity,
    *,
    public_model: str,
    provider_model: str | None,
) -> bool:
    if identity.public_model_name is not None:
        return identity.public_model_name == public_model
    if identity.litellm_model_name is not None:
        return identity.litellm_model_name == public_model
    return identity.provider_model_id in (public_model, provider_model)
