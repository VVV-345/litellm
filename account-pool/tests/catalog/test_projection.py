"""验证渠道目录快照向旧版调度配置投影时的排序和完整性检查。"""

from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

import pytest
from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.models import AdministrativeState, CatalogSnapshot, ModelCandidateOverrideRecord
from account_pool.catalog.projection import project_pool_config
from account_pool.models import PoolConfig
from pydantic import ValidationError
from tests.catalog.test_importer import legacy_config


def imported_snapshot() -> tuple[PoolConfig, CatalogSnapshot]:
    source: Final = legacy_config()
    imported: Final = catalog_import_from_pool_config(source, datetime(2026, 8, 19, 2, 0, tzinfo=UTC))
    return source, CatalogSnapshot(
        channels=imported.channels,
        bindings=imported.bindings,
        policies=imported.policies,
    )


def test_projection_round_trips_legacy_config_using_persisted_order() -> None:
    source, snapshot = imported_snapshot()
    shuffled: Final = CatalogSnapshot(
        channels=tuple(reversed(snapshot.channels)),
        bindings=tuple(reversed(snapshot.bindings)),
        policies=tuple(reversed(snapshot.policies)),
    )

    projected: Final = project_pool_config(shuffled)
    without_catalog_ids: Final = projected.model_copy(
        update={
            "accounts": tuple(
                account.model_copy(
                    update={
                        "channel_id": None,
                        "deployments": tuple(
                            deployment.model_copy(update={"binding_id": None}) for deployment in account.deployments
                        ),
                    }
                )
                for account in projected.accounts
            ),
            "policies": tuple(policy.model_copy(update={"version": 0}) for policy in projected.policies),
        }
    )

    assert without_catalog_ids == source
    assert tuple(account.channel_id for account in projected.accounts) == tuple(
        channel.channel_id for channel in sorted(snapshot.channels, key=lambda channel: channel.account_order)
    )
    assert all(
        deployment.binding_id is not None for account in projected.accounts for deployment in account.deployments
    )


def test_projection_rejects_orphan_binding() -> None:
    _, snapshot = imported_snapshot()
    orphaned: Final = CatalogSnapshot(
        channels=snapshot.channels,
        bindings=(snapshot.bindings[0].model_copy(update={"channel_id": uuid4()}),),
        policies=snapshot.policies,
    )

    with pytest.raises(ValueError, match="orphan binding"):
        project_pool_config(orphaned)


def test_projection_uses_channel_uuid_when_legacy_account_id_is_missing() -> None:
    _, snapshot = imported_snapshot()
    new_channel: Final = snapshot.channels[0].model_copy(update={"legacy_account_id": None})
    without_legacy_id: Final = CatalogSnapshot(
        channels=(new_channel,),
        bindings=tuple(binding for binding in snapshot.bindings if binding.channel_id == new_channel.channel_id),
        policies=(),
    )

    assert project_pool_config(without_legacy_id).accounts[0].id == str(new_channel.channel_id)


def test_projection_maps_paused_channel_to_disabled_legacy_account() -> None:
    _, snapshot = imported_snapshot()
    paused: Final = CatalogSnapshot(
        channels=(snapshot.channels[0].model_copy(update={"administrative_state": AdministrativeState.PAUSED}),),
        bindings=tuple(
            binding for binding in snapshot.bindings if binding.channel_id == snapshot.channels[0].channel_id
        ),
        policies=(),
    )

    assert project_pool_config(paused).accounts[0].enabled is False


def test_projection_maps_pending_delete_channel_to_disabled_legacy_account() -> None:
    _, snapshot = imported_snapshot()
    pending: Final = CatalogSnapshot(
        channels=(
            snapshot.channels[0].model_copy(update={"administrative_state": AdministrativeState.PENDING_DELETE}),
        ),
        bindings=tuple(
            binding for binding in snapshot.bindings if binding.channel_id == snapshot.channels[0].channel_id
        ),
        policies=(),
    )

    assert project_pool_config(pending).accounts[0].enabled is False


def test_projection_applies_model_candidate_override_and_policy_version() -> None:
    _, snapshot = imported_snapshot()
    binding: Final = snapshot.bindings[0]
    timestamp: Final = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)
    configured: Final = snapshot.model_copy(
        update={
            "policies": (snapshot.policies[0].model_copy(update={"version": 7}),),
            "candidate_overrides": (
                ModelCandidateOverrideRecord(
                    model=binding.public_model,
                    binding_id=binding.binding_id,
                    manual_order=0,
                    weight=9,
                    paused=True,
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
            ),
        }
    )

    projected: Final = project_pool_config(configured)
    deployment: Final = next(
        deployment
        for account in projected.accounts
        for deployment in account.deployments
        if deployment.binding_id == binding.binding_id
    )

    assert projected.policies[0].version == 7
    assert (deployment.manual_order, deployment.routing_weight, deployment.routing_paused) == (0, 9, True)


def test_projection_leaves_duplicate_deployment_rejection_to_pool_config() -> None:
    _, snapshot = imported_snapshot()
    duplicate: Final = snapshot.bindings[1].model_copy(
        update={
            "binding_id": uuid4(),
            "channel_id": snapshot.channels[1].channel_id,
            "litellm_deployment_id": snapshot.bindings[0].litellm_deployment_id,
        }
    )
    invalid: Final = CatalogSnapshot(
        channels=snapshot.channels,
        bindings=(snapshot.bindings[0], duplicate),
        policies=(),
    )

    with pytest.raises(ValidationError, match="LiteLLM deployment ids must be unique"):
        project_pool_config(invalid)
