from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

import pytest
from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.models import AdministrativeState, CatalogSnapshot
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

    assert project_pool_config(shuffled) == source


def test_projection_rejects_orphan_binding() -> None:
    _, snapshot = imported_snapshot()
    orphaned: Final = CatalogSnapshot(
        channels=snapshot.channels,
        bindings=(snapshot.bindings[0].model_copy(update={"channel_id": uuid4()}),),
        policies=snapshot.policies,
    )

    with pytest.raises(ValueError, match="orphan binding"):
        project_pool_config(orphaned)


def test_projection_rejects_channel_without_legacy_account_id() -> None:
    _, snapshot = imported_snapshot()
    missing_legacy_id: Final = CatalogSnapshot(
        channels=(snapshot.channels[0].model_copy(update={"legacy_account_id": None}),),
        bindings=tuple(binding for binding in snapshot.bindings if binding.channel_id == snapshot.channels[0].channel_id),
        policies=(),
    )

    with pytest.raises(ValueError, match="legacy_account_id"):
        project_pool_config(missing_legacy_id)


def test_projection_maps_paused_channel_to_disabled_legacy_account() -> None:
    _, snapshot = imported_snapshot()
    paused: Final = CatalogSnapshot(
        channels=(snapshot.channels[0].model_copy(update={"administrative_state": AdministrativeState.PAUSED}),),
        bindings=tuple(binding for binding in snapshot.bindings if binding.channel_id == snapshot.channels[0].channel_id),
        policies=(),
    )

    assert project_pool_config(paused).accounts[0].enabled is False


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
