"""把解析器计费数据安全投影为调度器可比较的 Deployment 成本证据。"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from account_pool.models import (
    AccountConfig,
    CostEvidenceKind,
    DeploymentConfig,
    DeploymentCostEvidence,
    RuntimeBillingMode,
)
from account_pool.parsing.models import (
    BillingMode,
    BillingRoute,
    EffectivePrices,
    MeteredGroup,
    MeteredModelPrice,
    ParsedChannelData,
)
from account_pool.parsing.subscription import subscription_includes_model


def project_routing_deployments(
    account: AccountConfig,
    parsed: ParsedChannelData,
) -> tuple[DeploymentConfig, ...]:
    return tuple(_project_deployment(deployment=deployment, parsed=parsed) for deployment in account.deployments)


def _project_deployment(deployment: DeploymentConfig, parsed: ParsedChannelData) -> DeploymentConfig:
    route: Final = _direct_route(deployment=deployment, routes=parsed.billing_routes)
    billing_mode: Final = _billing_mode(route)
    included: Final = subscription_includes_model(
        parsed.subscription,
        public_model=deployment.public_model,
        provider_model=deployment.provider_model,
    )
    if included and (route is None or route.mode == BillingMode.SUBSCRIPTION):
        return deployment.model_copy(
            update={
                "billing_route_id": None if route is None else str(route.route_id),
                "billing_mode": RuntimeBillingMode.SUBSCRIPTION,
                "cost_evidence": DeploymentCostEvidence(
                    kind=CostEvidenceKind.SUBSCRIPTION_INCLUDED,
                    effective_cost=Decimal("0"),
                    provider_group_id=None if route is None else route.provider_group_id,
                    billing_mode=RuntimeBillingMode.SUBSCRIPTION,
                ),
            }
        )
    prices: Final = _matching_prices(deployment=deployment, parsed=parsed, route=route)
    selected: Final = _unambiguous_price(prices)
    evidence: Final = None if selected is None else _cost_evidence(selected[0], selected[1], billing_mode)
    return deployment.model_copy(
        update={
            "billing_route_id": None if route is None else str(route.route_id),
            "billing_mode": billing_mode,
            "cost_evidence": evidence,
        }
    )


def _direct_route(
    deployment: DeploymentConfig,
    routes: tuple[BillingRoute, ...],
) -> BillingRoute | None:
    if deployment.binding_id is None:
        return None
    directly_bound: Final = tuple(
        route
        for route in routes
        if route.deployment_binding_id == deployment.binding_id and route.request_parameter_ref is None
    )
    return directly_bound[0] if len(directly_bound) == 1 else None


def _matching_prices(
    deployment: DeploymentConfig,
    parsed: ParsedChannelData,
    route: BillingRoute | None,
) -> tuple[tuple[MeteredGroup, MeteredModelPrice], ...]:
    metered: Final = parsed.metered
    if metered is None or (route is not None and route.mode != BillingMode.METERED):
        return ()
    groups: Final = (
        metered.groups
        if route is None or route.provider_group_id is None
        else tuple(group for group in metered.groups if group.group_id == route.provider_group_id)
    )
    return tuple(
        (group, price)
        for group in groups
        for price in group.models
        if _matches_deployment(price=price, deployment=deployment)
    )


def _matches_deployment(price: MeteredModelPrice, deployment: DeploymentConfig) -> bool:
    if price.public_model_name is not None:
        return price.public_model_name == deployment.public_model
    if price.litellm_model_name is not None:
        return price.litellm_model_name == deployment.public_model
    return price.provider_model_id in (deployment.provider_model, deployment.public_model)


def _unambiguous_price(
    prices: tuple[tuple[MeteredGroup, MeteredModelPrice], ...],
) -> tuple[MeteredGroup, MeteredModelPrice] | None:
    if len(prices) == 1:
        return prices[0]
    if not prices:
        return None
    signatures: Final = frozenset(_price_signature(price) for _, price in prices)
    return prices[0] if len(signatures) == 1 else None


def _price_signature(price: MeteredModelPrice) -> tuple[object, ...]:
    normalized: Final = price.normalized_per_million_tokens
    values: Final = normalized if normalized is not None else price.effective_prices
    unit: Final = "million_tokens" if normalized is not None else price.unit
    return (
        price.currency.casefold(),
        unit.casefold(),
        values.input_price,
        values.output_price,
        values.cache_read_price,
        values.cache_write_price,
    )


def _cost_evidence(
    group: MeteredGroup,
    price: MeteredModelPrice,
    billing_mode: RuntimeBillingMode,
) -> DeploymentCostEvidence | None:
    normalized: Final = price.normalized_per_million_tokens
    values: Final = normalized if normalized is not None else price.effective_prices
    cost: Final = _token_cost(values)
    if cost is None:
        return None
    return DeploymentCostEvidence(
        kind=(
            CostEvidenceKind.NORMALIZED_PER_MILLION_TOKENS
            if normalized is not None
            else CostEvidenceKind.EFFECTIVE_PRICES
        ),
        currency=price.currency.upper(),
        unit="million_tokens" if normalized is not None else price.unit,
        input_price=values.input_price,
        output_price=values.output_price,
        cache_read_price=values.cache_read_price,
        cache_write_price=values.cache_write_price,
        effective_cost=cost,
        partial=(values.input_price is None) != (values.output_price is None),
        provider_group_id=group.group_id,
        billing_mode=billing_mode,
    )


def _token_cost(values: EffectivePrices) -> Decimal | None:
    known: Final = tuple(value for value in (values.input_price, values.output_price) if value is not None)
    return sum(known, Decimal("0")) if known else None


def _billing_mode(route: BillingRoute | None) -> RuntimeBillingMode:
    if route is None:
        return RuntimeBillingMode.PROVIDER_DECIDED
    return RuntimeBillingMode(route.mode)
