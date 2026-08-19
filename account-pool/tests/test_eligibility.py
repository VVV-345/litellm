"""验证渠道、模型和 Deployment 范围资格状态的转换与隔离。"""

from typing import Final

from account_pool.eligibility import (
    EligibilityScope,
    EligibilitySource,
    EligibilityState,
    EligibilitySubject,
    activate_exclusion,
    candidate_exclusion,
    clear_candidate,
    effective_state,
    retain_configured_exclusions,
    upsert_exclusion,
)
from account_pool.eligibility.redis import decode_exclusions, eligibility_key
from account_pool.models import AccountConfig, DeploymentConfig


def test_scope_matching_isolates_channel_model_and_deployment() -> None:
    channel: Final = activate_exclusion(
        scope=EligibilityScope.CHANNEL,
        source=EligibilitySource.HEALTH,
        account_id="channel-a",
        model="model-a",
        deployment_id="deployment-a",
        billing_route_id=None,
        reason_code="credential_invalid",
        starts_at=100,
        retry_at=None,
    )
    model: Final = activate_exclusion(
        scope=EligibilityScope.MODEL,
        source=EligibilitySource.HEALTH,
        account_id="channel-b",
        model="model-a",
        deployment_id="deployment-b-a",
        billing_route_id=None,
        reason_code="model_not_found",
        starts_at=100,
        retry_at=None,
    )
    deployment: Final = activate_exclusion(
        scope=EligibilityScope.DEPLOYMENT,
        source=EligibilitySource.RESTRICTION,
        account_id="channel-c",
        model="model-a",
        deployment_id="deployment-c-a",
        billing_route_id=None,
        reason_code="rate_limited",
        starts_at=100,
        retry_at=200,
    )
    exclusions: Final = (channel, model, deployment)

    assert candidate_exclusion(exclusions, "channel-a", "model-b", "deployment-a-b", None, 150) == channel
    assert candidate_exclusion(exclusions, "channel-b", "model-a", "deployment-b-a", None, 150) == model
    assert candidate_exclusion(exclusions, "channel-b", "model-b", "deployment-b-b", None, 150) is None
    assert candidate_exclusion(exclusions, "channel-c", "model-a", "deployment-c-a", None, 150) == deployment
    assert candidate_exclusion(exclusions, "channel-c", "model-a", "deployment-c-b", None, 150) is None


def test_retry_time_enters_half_open_without_clearing_evidence() -> None:
    exclusion: Final = activate_exclusion(
        scope=EligibilityScope.DEPLOYMENT,
        source=EligibilitySource.CAPACITY,
        account_id="channel-a",
        model="model-a",
        deployment_id="deployment-a",
        billing_route_id=None,
        reason_code="concurrency_limited",
        starts_at=100,
        retry_at=120,
    )

    assert effective_state(exclusion, 119) == EligibilityState.ACTIVE
    assert effective_state(exclusion, 120) == EligibilityState.HALF_OPEN
    assert candidate_exclusion((exclusion,), "channel-a", "model-a", "deployment-a", None, 120) is None


def test_success_clears_only_evidence_for_the_successful_candidate() -> None:
    model_a: Final = activate_exclusion(
        scope=EligibilityScope.MODEL,
        source=EligibilitySource.HEALTH,
        account_id="channel-a",
        model="model-a",
        deployment_id="deployment-a",
        billing_route_id=None,
        reason_code="model_not_found",
        starts_at=100,
        retry_at=None,
    )
    deployment_b: Final = activate_exclusion(
        scope=EligibilityScope.DEPLOYMENT,
        source=EligibilitySource.RESTRICTION,
        account_id="channel-a",
        model="model-b",
        deployment_id="deployment-b",
        billing_route_id=None,
        reason_code="rate_limited",
        starts_at=100,
        retry_at=200,
    )

    cleared: Final = clear_candidate((model_a, deployment_b), "channel-a", "model-a", "deployment-a", None)

    assert cleared[0].state == EligibilityState.CLEARED
    assert cleared[1].state == EligibilityState.ACTIVE
    assert candidate_exclusion(cleared, "channel-a", "model-a", "deployment-a", None, 150) is None
    assert candidate_exclusion(cleared, "channel-a", "model-b", "deployment-b", None, 150) == deployment_b


def test_new_signal_reactivates_same_scope_without_duplicate_state() -> None:
    original: Final = activate_exclusion(
        scope=EligibilityScope.MODEL,
        source=EligibilitySource.HEALTH,
        account_id="channel-a",
        model="model-a",
        deployment_id="deployment-a",
        billing_route_id=None,
        reason_code="model_not_found",
        starts_at=100,
        retry_at=None,
    )
    cleared: Final = clear_candidate((original,), "channel-a", "model-a", "deployment-a", None)
    repeated: Final = activate_exclusion(
        scope=EligibilityScope.MODEL,
        source=EligibilitySource.HEALTH,
        account_id="channel-a",
        model="model-a",
        deployment_id="deployment-a",
        billing_route_id=None,
        reason_code="model_not_found",
        starts_at=200,
        retry_at=None,
    )

    updated: Final = upsert_exclusion(cleared, repeated)

    assert updated == (repeated,)


def test_billing_route_scope_does_not_exclude_sibling_route() -> None:
    exclusion: Final = activate_exclusion(
        scope=EligibilityScope.BILLING_ROUTE,
        source=EligibilitySource.RESTRICTION,
        account_id="channel-a",
        model="model-a",
        deployment_id="deployment-a",
        billing_route_id="route-a",
        reason_code="balance_signal_unscoped",
        starts_at=100,
        retry_at=200,
    )

    assert candidate_exclusion((exclusion,), "channel-a", "model-a", "deployment-a", "route-a", 150) == exclusion
    assert candidate_exclusion((exclusion,), "channel-a", "model-a", "deployment-a", "route-b", 150) is None


def test_reconfigure_discards_evidence_for_removed_deployment() -> None:
    exclusion: Final = activate_exclusion(
        scope=EligibilityScope.DEPLOYMENT,
        source=EligibilitySource.HEALTH,
        account_id="channel-a",
        model="model-a",
        deployment_id="deployment-a",
        billing_route_id=None,
        reason_code="model_not_found",
        starts_at=100,
        retry_at=None,
    )
    account: Final = AccountConfig(
        id="channel-a",
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=1,
        deployments=(DeploymentConfig(public_model="model-b", litellm_model_id="deployment-b"),),
    )

    assert retain_configured_exclusions((exclusion,), (account,)) == ()


def test_redis_codec_round_trips_scope_and_retry_time() -> None:
    subject: Final = EligibilitySubject(
        scope=EligibilityScope.BILLING_ROUTE,
        account_id="channel-a",
        model="model-a",
        deployment_id="deployment-a",
        billing_route_id="route-a",
    )

    decoded: Final = decode_exclusions(subject, {"restriction|rate_limited": "100|200"})

    assert eligibility_key(subject) == "pool:eligibility:billing_route:channel-a:route-a"
    assert decoded[0].source == "restriction"
    assert decoded[0].reason_code == "rate_limited"
    assert decoded[0].retry_at == 200
