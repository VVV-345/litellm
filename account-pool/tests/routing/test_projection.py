"""验证解析价格到运行时候选的精确映射、歧义处理和计费模式。"""

from decimal import Decimal
from typing import Final
from uuid import UUID

from account_pool.models import AccountConfig, CostEvidenceKind, DeploymentConfig
from account_pool.parsing.models import (
    BillingMode,
    BillingRoute,
    EffectivePrices,
    MeteredData,
    MeteredGroup,
    MeteredModelPrice,
    ModelIdentity,
    ParsedChannelData,
    SubscriptionData,
    SubscriptionStatus,
)
from account_pool.routing import project_routing_deployments

_BINDING_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_ROUTE_ID: Final = UUID("20000000-0000-0000-0000-000000000001")


def _account() -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=2,
        deployments=(
            DeploymentConfig(
                public_model="public-a",
                provider_model="provider-a",
                litellm_model_id="deployment-a",
                binding_id=_BINDING_ID,
            ),
        ),
    )


def _price(
    input_price: str | None,
    output_price: str | None,
    *,
    public_model: str | None = "public-a",
    currency: str = "USD",
    normalized: bool = True,
) -> MeteredModelPrice:
    effective: Final = EffectivePrices(
        input_price=None if input_price is None else Decimal(input_price),
        output_price=None if output_price is None else Decimal(output_price),
    )
    return MeteredModelPrice(
        provider_model_id="provider-a",
        public_model_name=public_model,
        currency=currency,
        unit="million_tokens",
        input_price=effective.input_price,
        output_price=effective.output_price,
        effective_prices=effective,
        normalized_per_million_tokens=effective if normalized else None,
    )


def _parsed(
    groups: tuple[MeteredGroup, ...],
    routes: tuple[BillingRoute, ...] = (),
) -> ParsedChannelData:
    return ParsedChannelData(metered=MeteredData(groups=groups), billing_routes=routes)


def test_prefers_normalized_price_and_sums_available_token_directions() -> None:
    parsed: Final = _parsed((MeteredGroup(group_id="standard", models=(_price("2", "8"),)),))

    deployment: Final = project_routing_deployments(_account(), parsed)[0]

    assert deployment.billing_mode == "provider_decided"
    assert deployment.cost_evidence is not None
    assert deployment.cost_evidence.kind == CostEvidenceKind.NORMALIZED_PER_MILLION_TOKENS
    assert deployment.cost_evidence.effective_cost == Decimal("10")
    assert deployment.cost_evidence.provider_group_id == "standard"


def test_preserves_partial_price_as_explicit_cost_evidence() -> None:
    parsed: Final = _parsed((MeteredGroup(group_id="standard", models=(_price("2", None),)),))

    evidence: Final = project_routing_deployments(_account(), parsed)[0].cost_evidence

    assert evidence is not None
    assert evidence.effective_cost == Decimal("2")
    assert evidence.partial is True


def test_different_group_prices_are_not_guessed_when_provider_decides_billing() -> None:
    parsed: Final = _parsed(
        (
            MeteredGroup(group_id="standard", models=(_price("2", "8"),)),
            MeteredGroup(group_id="premium", models=(_price("1", "4"),)),
        )
    )

    deployment: Final = project_routing_deployments(_account(), parsed)[0]

    assert deployment.billing_route_id is None
    assert deployment.cost_evidence is None


def test_bound_metered_route_selects_matching_provider_group() -> None:
    route: Final = BillingRoute(
        route_id=_ROUTE_ID,
        deployment_binding_id=_BINDING_ID,
        mode=BillingMode.METERED,
        provider_group_id="premium",
    )
    parsed: Final = _parsed(
        (
            MeteredGroup(group_id="standard", models=(_price("2", "8"),)),
            MeteredGroup(group_id="premium", models=(_price("1", "4"),)),
        ),
        (route,),
    )

    deployment: Final = project_routing_deployments(_account(), parsed)[0]

    assert deployment.billing_route_id == str(_ROUTE_ID)
    assert deployment.billing_mode == "metered"
    assert deployment.cost_evidence is not None
    assert deployment.cost_evidence.effective_cost == Decimal("5")
    assert deployment.cost_evidence.provider_group_id == "premium"


def test_bound_subscription_route_has_zero_marginal_cost() -> None:
    route: Final = BillingRoute(
        route_id=_ROUTE_ID,
        deployment_binding_id=_BINDING_ID,
        mode=BillingMode.SUBSCRIPTION,
    )

    parsed: Final = ParsedChannelData(
        subscription=SubscriptionData(
            status=SubscriptionStatus.ACTIVE,
            models=(ModelIdentity(provider_model_id="provider-a", public_model_name="public-a"),),
        ),
        billing_routes=(route,),
    )

    deployment: Final = project_routing_deployments(_account(), parsed)[0]

    assert deployment.billing_mode == "subscription"
    assert deployment.cost_evidence is not None
    assert deployment.cost_evidence.kind == CostEvidenceKind.SUBSCRIPTION_INCLUDED
    assert deployment.cost_evidence.effective_cost == 0


def test_subscription_zero_cost_only_applies_to_selected_models() -> None:
    first: Final = _account().deployments[0]
    second: Final = first.model_copy(
        update={
            "public_model": "public-b",
            "provider_model": "provider-b",
            "litellm_model_id": "deployment-b",
            "binding_id": UUID("10000000-0000-0000-0000-000000000002"),
        }
    )
    account: Final = _account().model_copy(update={"deployments": (first, second)})
    parsed: Final = ParsedChannelData(
        subscription=SubscriptionData(
            status=SubscriptionStatus.ACTIVE,
            models=(ModelIdentity(provider_model_id="provider-a"),),
            balance=Decimal("20"),
        )
    )

    deployments: Final = project_routing_deployments(account, parsed)

    assert deployments[0].cost_evidence is not None
    assert deployments[0].cost_evidence.kind == CostEvidenceKind.SUBSCRIPTION_INCLUDED
    assert deployments[1].cost_evidence is None


def test_expired_or_unmapped_subscription_does_not_claim_zero_cost() -> None:
    route: Final = BillingRoute(
        route_id=_ROUTE_ID,
        deployment_binding_id=_BINDING_ID,
        mode=BillingMode.SUBSCRIPTION,
    )
    parsed: Final = ParsedChannelData(
        subscription=SubscriptionData(
            status=SubscriptionStatus.EXPIRED,
            models=(ModelIdentity(provider_model_id="provider-a", public_model_name="public-a"),),
        ),
        billing_routes=(route,),
    )

    deployment: Final = project_routing_deployments(_account(), parsed)[0]

    assert deployment.billing_route_id == str(_ROUTE_ID)
    assert deployment.cost_evidence is None


def test_request_parameter_route_is_not_claimed_executable_before_selector_support() -> None:
    route: Final = BillingRoute(
        route_id=_ROUTE_ID,
        deployment_binding_id=_BINDING_ID,
        mode=BillingMode.METERED,
        provider_group_id="premium",
        request_parameter_ref="provider:premium",
    )
    parsed: Final = _parsed((MeteredGroup(group_id="premium", models=(_price("1", "4"),)),), (route,))

    deployment: Final = project_routing_deployments(_account(), parsed)[0]

    assert deployment.billing_route_id is None
    assert deployment.billing_mode == "provider_decided"


def test_explicit_public_model_mismatch_does_not_fall_back_to_provider_name() -> None:
    parsed: Final = _parsed(
        (MeteredGroup(group_id="standard", models=(_price("2", "8", public_model="other-public"),)),)
    )

    assert project_routing_deployments(_account(), parsed)[0].cost_evidence is None


def test_litellm_model_name_matches_public_model_not_provider_model() -> None:
    price: Final = _price("2", "8", public_model=None).model_copy(update={"litellm_model_name": "public-a"})
    parsed: Final = _parsed((MeteredGroup(group_id="standard", models=(price,)),))

    evidence: Final = project_routing_deployments(_account(), parsed)[0].cost_evidence

    assert evidence is not None
    assert evidence.effective_cost == Decimal("10")
