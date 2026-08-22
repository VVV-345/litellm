"""验证管理审计事件的不可变类型、安全明细和跨记录关联。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from account_pool.audit.models import (
    AuditOutcome,
    ChannelCreateDetails,
    ChannelDeleteDetails,
    ChannelDeleteExternalDeploymentDetails,
    ChannelDetachDetails,
    ChannelImportDetails,
    ChannelReconcileDetails,
    ChannelUpdateDetails,
    ManagementAuditDetails,
    ManagementAuditRecord,
    ParserOverrideRevokeDetails,
    ParserOverrideSetDetails,
    ParserSnapshotImportDetails,
    ParserTaskStartDetails,
    PoolEvent,
    RoutingPolicyUpdateDetails,
    SafeAuditOutcome,
    build_management_audit_record,
)
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.sync.models import DeleteMode
from pydantic import ValidationError

_EVENT_ID: Final = UUID("30000000-0000-0000-0000-000000000001")
_OPERATION_ID: Final = UUID("30000000-0000-0000-0000-000000000002")
_CHANNEL_ID: Final = UUID("30000000-0000-0000-0000-000000000003")
_BINDING_ID: Final = UUID("30000000-0000-0000-0000-000000000004")
_ENVELOPE_ID: Final = UUID("30000000-0000-0000-0000-000000000005")
_NOW: Final = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
_ACCEPTED: Final = SafeAuditOutcome(status=AuditOutcome.ACCEPTED)


def _actor(action: ActorAction) -> ActorContext:
    return ActorContext(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-123",
        action=action,
        envelope_id=_ENVELOPE_ID,
    )


@pytest.mark.parametrize(
    ("action", "details"),
    [
        (ActorAction.CHANNEL_CREATE, ChannelCreateDetails(outcome=_ACCEPTED)),
        (ActorAction.CHANNEL_UPDATE, ChannelUpdateDetails(outcome=_ACCEPTED)),
        (ActorAction.CHANNEL_IMPORT, ChannelImportDetails(outcome=_ACCEPTED)),
        (ActorAction.CHANNEL_DETACH, ChannelDetachDetails(outcome=_ACCEPTED)),
        (
            ActorAction.CHANNEL_DELETE,
            ChannelDeleteDetails(outcome=_ACCEPTED, delete_mode=DeleteMode.DETACH_ONLY),
        ),
        (
            ActorAction.CHANNEL_DELETE_EXTERNAL_DEPLOYMENT,
            ChannelDeleteExternalDeploymentDetails(outcome=_ACCEPTED, binding_id=_BINDING_ID),
        ),
        (ActorAction.CHANNEL_RECONCILE, ChannelReconcileDetails(outcome=_ACCEPTED)),
        (
            ActorAction.PARSER_START,
            ParserTaskStartDetails(outcome=_ACCEPTED, task_id=_OPERATION_ID, parser_run_id=_BINDING_ID),
        ),
        (
            ActorAction.SNAPSHOT_IMPORT,
            ParserSnapshotImportDetails(
                outcome=_ACCEPTED,
                import_id=_OPERATION_ID,
                source_parser_run_id=_BINDING_ID,
                changed_field_count=2,
            ),
        ),
        (
            ActorAction.OVERRIDE_SET,
            ParserOverrideSetDetails(
                outcome=_ACCEPTED,
                override_id=_OPERATION_ID,
                field_path="/subscription/balance",
            ),
        ),
        (
            ActorAction.OVERRIDE_REVOKE,
            ParserOverrideRevokeDetails(
                outcome=_ACCEPTED,
                override_id=_OPERATION_ID,
                field_path="/subscription/balance",
            ),
        ),
    ],
)
def test_builder_records_verified_actor_and_operation_correlation(
    action: ActorAction,
    details: ManagementAuditDetails,
) -> None:
    record: Final = build_management_audit_record(
        event_id=_EVENT_ID,
        occurred_at=_NOW,
        actor=_actor(action),
        operation_id=_OPERATION_ID,
        channel_id=_CHANNEL_ID,
        details=details,
    )

    assert record.event.event_id == _EVENT_ID
    assert record.event.occurred_at == _NOW
    assert record.event.channel_id == _CHANNEL_ID
    assert record.event.request_id == "request-123"
    assert record.event.actor_type == "user"
    assert record.event.actor_id == "admin-user"
    assert record.audit.event_id == _EVENT_ID
    assert record.audit.actor_role == "proxy_admin"
    assert record.audit.actor_action == action
    assert record.audit.actor_envelope_id == _ENVELOPE_ID
    assert record.audit.operation_id == _OPERATION_ID
    assert record.audit.outcome == AuditOutcome.ACCEPTED
    assert record.event.safe_details == details


def test_builder_records_model_level_routing_audit_without_channel_id() -> None:
    record: Final = build_management_audit_record(
        event_id=_EVENT_ID,
        occurred_at=_NOW,
        actor=_actor(ActorAction.ROUTING_POLICY_UPDATE),
        model_id="openai/gpt-4o",
        details=RoutingPolicyUpdateDetails(
            outcome=SafeAuditOutcome(status=AuditOutcome.SUCCEEDED),
            expected_version=2,
            resulting_version=3,
        ),
    )

    assert record.event.model_id == "openai/gpt-4o"
    assert record.event.channel_id is None
    assert record.audit.operation_id is None


def test_routing_audit_rejects_channel_target() -> None:
    with pytest.raises(ValidationError, match="model ID"):
        build_management_audit_record(
            event_id=_EVENT_ID,
            occurred_at=_NOW,
            actor=_actor(ActorAction.ROUTING_POLICY_UPDATE),
            channel_id=_CHANNEL_ID,
            details=RoutingPolicyUpdateDetails(
                outcome=SafeAuditOutcome(status=AuditOutcome.SUCCEEDED),
                expected_version=2,
                resulting_version=3,
            ),
        )


def test_models_are_immutable_and_forbid_unvalidated_fields() -> None:
    record: Final = build_management_audit_record(
        event_id=_EVENT_ID,
        occurred_at=_NOW,
        actor=_actor(ActorAction.CHANNEL_CREATE),
        operation_id=_OPERATION_ID,
        channel_id=_CHANNEL_ID,
        details=ChannelCreateDetails(outcome=_ACCEPTED),
    )

    with pytest.raises(ValidationError):
        record.audit.actor_action = ActorAction.CHANNEL_UPDATE
    with pytest.raises(ValidationError):
        PoolEvent.model_validate(
            {
                **record.event.model_dump(mode="json"),
                "safe_details": {
                    "kind": "channel_create",
                    "outcome": {"status": "accepted"},
                    "api_key": "must-not-be-stored",
                },
            }
        )


@pytest.mark.parametrize(
    "unsafe_field",
    [
        {"url": "https://user:secret@example.test"},
        {"authorization": "Bearer must-not-be-stored"},
        {"raw_provider_response": {"token": "must-not-be-stored"}},
        {"details": {"arbitrary": "dictionary"}},
    ],
)
def test_safe_details_reject_credentials_headers_responses_and_arbitrary_dictionaries(
    unsafe_field: dict[str, object],
) -> None:
    payload: Final = {
        "event_id": str(_EVENT_ID),
        "event_type": "channel_create",
        "occurred_at": _NOW.isoformat(),
        "channel_id": str(_CHANNEL_ID),
        "model_id": None,
        "deployment_id": None,
        "request_id": "request-123",
        "lease_id": None,
        "reason_code": None,
        "actor_type": "user",
        "actor_id": "admin-user",
        "safe_details": {
            "kind": "channel_create",
            "outcome": {"status": "accepted"},
            **unsafe_field,
        },
    }

    with pytest.raises(ValidationError):
        PoolEvent.model_validate(payload)


def test_failure_outcome_accepts_only_bounded_machine_code() -> None:
    failed: Final = SafeAuditOutcome(status=AuditOutcome.FAILED, failure_code="database_unavailable")

    assert failed.failure_code == "database_unavailable"
    with pytest.raises(ValidationError):
        SafeAuditOutcome(status=AuditOutcome.FAILED)
    with pytest.raises(ValidationError):
        SafeAuditOutcome(status=AuditOutcome.SUCCEEDED, failure_code="unexpected")
    with pytest.raises(ValidationError):
        SafeAuditOutcome(status=AuditOutcome.FAILED, failure_code="Bearer secret")


def test_builder_rejects_action_detail_mismatch() -> None:
    with pytest.raises(ValueError, match="action"):
        build_management_audit_record(
            event_id=_EVENT_ID,
            occurred_at=_NOW,
            actor=_actor(ActorAction.CHANNEL_UPDATE),
            operation_id=_OPERATION_ID,
            channel_id=_CHANNEL_ID,
            details=ChannelCreateDetails(outcome=_ACCEPTED),
        )


def test_record_rejects_mismatched_linked_event_id() -> None:
    record: Final = build_management_audit_record(
        event_id=_EVENT_ID,
        occurred_at=_NOW,
        actor=_actor(ActorAction.CHANNEL_CREATE),
        operation_id=_OPERATION_ID,
        channel_id=_CHANNEL_ID,
        details=ChannelCreateDetails(outcome=_ACCEPTED),
    )

    with pytest.raises(ValidationError, match="event ID"):
        ManagementAuditRecord(
            event=record.event,
            audit=record.audit.model_copy(
                update={"event_id": UUID("30000000-0000-0000-0000-000000000099")}
            ),
        )
