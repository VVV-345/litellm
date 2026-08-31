"""定义渠道与模型路由管理审计事件、安全明细和关联事实。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.models import FrozenModel, ModelName
from account_pool.sync.models import DeleteMode

_SAFE_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"
_REQUEST_ID_PATTERN: Final = r"^[A-Za-z0-9._:-]+$"


class AuditOutcome(StrEnum):
    ACCEPTED = "accepted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SafeAuditOutcome(FrozenModel):
    status: AuditOutcome
    failure_code: str | None = Field(default=None, pattern=_SAFE_CODE_PATTERN)

    @model_validator(mode="after")
    def validate_failure_code(self) -> Self:
        if self.status == AuditOutcome.FAILED and self.failure_code is None:
            raise ValueError("failed audit outcomes require a failure code")
        if self.status != AuditOutcome.FAILED and self.failure_code is not None:
            raise ValueError("only failed audit outcomes may carry a failure code")
        return self


class ChannelCreateDetails(FrozenModel):
    kind: Literal["channel_create"] = "channel_create"
    outcome: SafeAuditOutcome


class ChannelUpdateDetails(FrozenModel):
    kind: Literal["channel_update"] = "channel_update"
    outcome: SafeAuditOutcome


class ChannelImportDetails(FrozenModel):
    kind: Literal["channel_import"] = "channel_import"
    outcome: SafeAuditOutcome


class ChannelDetachDetails(FrozenModel):
    kind: Literal["channel_detach"] = "channel_detach"
    outcome: SafeAuditOutcome


class ChannelDeleteDetails(FrozenModel):
    kind: Literal["channel_delete"] = "channel_delete"
    outcome: SafeAuditOutcome
    delete_mode: DeleteMode


class ChannelDeleteExternalDeploymentDetails(FrozenModel):
    kind: Literal["channel_delete_external_deployment"] = "channel_delete_external_deployment"
    outcome: SafeAuditOutcome
    binding_id: UUID


class ChannelReconcileDetails(FrozenModel):
    kind: Literal["channel_reconcile"] = "channel_reconcile"
    outcome: SafeAuditOutcome


class ParserTaskStartDetails(FrozenModel):
    kind: Literal["parser_task_start"] = "parser_task_start"
    outcome: SafeAuditOutcome
    task_id: UUID | None = None
    parser_run_id: UUID | None = None


class ParserSnapshotImportDetails(FrozenModel):
    kind: Literal["parser_snapshot_import"] = "parser_snapshot_import"
    outcome: SafeAuditOutcome
    import_id: UUID
    source_parser_run_id: UUID | None = None
    changed_field_count: int | None = Field(default=None, ge=0)


class ParserOverrideSetDetails(FrozenModel):
    kind: Literal["parser_override_set"] = "parser_override_set"
    outcome: SafeAuditOutcome
    override_id: UUID
    field_path: str | None = Field(default=None, min_length=1, max_length=512, pattern=r"^/")


class ParserOverrideRevokeDetails(FrozenModel):
    kind: Literal["parser_override_revoke"] = "parser_override_revoke"
    outcome: SafeAuditOutcome
    override_id: UUID
    field_path: str | None = Field(default=None, min_length=1, max_length=512, pattern=r"^/")


class RoutingPolicyUpdateDetails(FrozenModel):
    kind: Literal["routing_policy_update"] = "routing_policy_update"
    outcome: SafeAuditOutcome
    expected_version: int = Field(ge=0)
    resulting_version: int | None = Field(default=None, ge=0)


class RoutingCandidateUpdateDetails(FrozenModel):
    kind: Literal["routing_candidate_update"] = "routing_candidate_update"
    outcome: SafeAuditOutcome
    binding_id: UUID
    expected_version: int = Field(ge=0)
    resulting_version: int | None = Field(default=None, ge=0)


class RoutingOrderUpdateDetails(FrozenModel):
    kind: Literal["routing_order_update"] = "routing_order_update"
    outcome: SafeAuditOutcome
    binding_count: int = Field(ge=1)
    expected_version: int = Field(ge=0)
    resulting_version: int | None = Field(default=None, ge=0)


class RoutingCandidateDeleteDetails(FrozenModel):
    kind: Literal["routing_candidate_delete"] = "routing_candidate_delete"
    outcome: SafeAuditOutcome
    binding_id: UUID
    expected_version: int = Field(ge=0)
    resulting_version: int | None = Field(default=None, ge=0)


ManagementAuditDetails = Annotated[
    ChannelCreateDetails
    | ChannelUpdateDetails
    | ChannelImportDetails
    | ChannelDetachDetails
    | ChannelDeleteDetails
    | ChannelDeleteExternalDeploymentDetails
    | ChannelReconcileDetails
    | ParserTaskStartDetails
    | ParserSnapshotImportDetails
    | ParserOverrideSetDetails
    | ParserOverrideRevokeDetails
    | RoutingPolicyUpdateDetails
    | RoutingCandidateUpdateDetails
    | RoutingOrderUpdateDetails
    | RoutingCandidateDeleteDetails,
    Field(discriminator="kind"),
]


class ManagementEventType(StrEnum):
    CHANNEL_CREATE = "channel_create"
    CHANNEL_UPDATE = "channel_update"
    CHANNEL_IMPORT = "channel_import"
    CHANNEL_DETACH = "channel_detach"
    CHANNEL_DELETE = "channel_delete"
    CHANNEL_DELETE_EXTERNAL_DEPLOYMENT = "channel_delete_external_deployment"
    CHANNEL_RECONCILE = "channel_reconcile"
    PARSER_TASK_START = "parser_task_start"
    PARSER_SNAPSHOT_IMPORT = "parser_snapshot_import"
    PARSER_OVERRIDE_SET = "parser_override_set"
    PARSER_OVERRIDE_REVOKE = "parser_override_revoke"
    ROUTING_POLICY_UPDATE = "routing_policy_update"
    ROUTING_CANDIDATE_UPDATE = "routing_candidate_update"
    ROUTING_ORDER_UPDATE = "routing_order_update"
    ROUTING_CANDIDATE_DELETE = "routing_candidate_delete"


class PoolEvent(FrozenModel):
    event_id: UUID
    event_type: ManagementEventType
    occurred_at: AwareDatetime
    channel_id: UUID | None = None
    model_id: ModelName | None = None
    deployment_id: str | None = Field(default=None, min_length=1, max_length=255)
    request_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=_REQUEST_ID_PATTERN)
    lease_id: str | None = Field(default=None, min_length=1, max_length=255)
    reason_code: str | None = Field(default=None, pattern=_SAFE_CODE_PATTERN)
    actor_type: Literal["user", "system"]
    actor_id: str = Field(min_length=1, max_length=255)
    safe_details: ManagementAuditDetails

    @model_validator(mode="after")
    def validate_management_event(self) -> Self:
        if self.event_type.value != self.safe_details.kind:
            raise ValueError("event type must match safe details")
        is_routing_event: Final = self.event_type in {
            ManagementEventType.ROUTING_POLICY_UPDATE,
            ManagementEventType.ROUTING_CANDIDATE_UPDATE,
            ManagementEventType.ROUTING_ORDER_UPDATE,
            ManagementEventType.ROUTING_CANDIDATE_DELETE,
        }
        if is_routing_event and (self.model_id is None or self.channel_id is not None):
            raise ValueError("routing management events require only a model ID")
        if not is_routing_event and (self.channel_id is None or self.model_id is not None):
            raise ValueError("channel management events require only a channel ID")
        if self.request_id is None:
            raise ValueError("management events require a request ID")
        return self


class ManagementAuditFact(FrozenModel):
    event_id: UUID
    operation_id: UUID | None = None
    actor_role: Literal["proxy_admin", "system"]
    actor_action: ActorAction
    actor_envelope_id: UUID
    outcome: AuditOutcome


class ManagementAuditRecord(FrozenModel):
    event: PoolEvent
    audit: ManagementAuditFact

    @model_validator(mode="after")
    def validate_linked_facts(self) -> Self:
        if self.event.event_id != self.audit.event_id:
            raise ValueError("common event and audit fact must share an event ID")
        if _EVENT_TYPE_BY_ACTION[self.audit.actor_action] != self.event.event_type:
            raise ValueError("audit action must match the common event type")
        if self.event.safe_details.outcome.status != self.audit.outcome:
            raise ValueError("audit outcome must match safe details")
        return self


_EVENT_TYPE_BY_ACTION: Final = {
    ActorAction.CHANNEL_CREATE: ManagementEventType.CHANNEL_CREATE,
    ActorAction.CHANNEL_UPDATE: ManagementEventType.CHANNEL_UPDATE,
    ActorAction.CHANNEL_IMPORT: ManagementEventType.CHANNEL_IMPORT,
    ActorAction.CHANNEL_DETACH: ManagementEventType.CHANNEL_DETACH,
    ActorAction.CHANNEL_DELETE: ManagementEventType.CHANNEL_DELETE,
    ActorAction.CHANNEL_DELETE_EXTERNAL_DEPLOYMENT: ManagementEventType.CHANNEL_DELETE_EXTERNAL_DEPLOYMENT,
    ActorAction.CHANNEL_RECONCILE: ManagementEventType.CHANNEL_RECONCILE,
    ActorAction.PARSER_START: ManagementEventType.PARSER_TASK_START,
    ActorAction.SNAPSHOT_IMPORT: ManagementEventType.PARSER_SNAPSHOT_IMPORT,
    ActorAction.OVERRIDE_SET: ManagementEventType.PARSER_OVERRIDE_SET,
    ActorAction.OVERRIDE_REVOKE: ManagementEventType.PARSER_OVERRIDE_REVOKE,
    ActorAction.ROUTING_POLICY_UPDATE: ManagementEventType.ROUTING_POLICY_UPDATE,
    ActorAction.ROUTING_CANDIDATE_UPDATE: ManagementEventType.ROUTING_CANDIDATE_UPDATE,
    ActorAction.ROUTING_ORDER_UPDATE: ManagementEventType.ROUTING_ORDER_UPDATE,
    ActorAction.ROUTING_CANDIDATE_DELETE: ManagementEventType.ROUTING_CANDIDATE_DELETE,
}


def build_management_audit_record(
    *,
    event_id: UUID,
    occurred_at: AwareDatetime,
    actor: ActorContext,
    operation_id: UUID | None = None,
    channel_id: UUID | None = None,
    model_id: ModelName | None = None,
    details: ManagementAuditDetails,
) -> ManagementAuditRecord:
    event_type: Final = _EVENT_TYPE_BY_ACTION.get(actor.action)
    if event_type is None or event_type.value != details.kind:
        raise ValueError("actor action must match audit details")
    return ManagementAuditRecord(
        event=PoolEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            channel_id=channel_id,
            model_id=model_id,
            request_id=actor.request_id,
            actor_type=actor.actor_type,
            actor_id=actor.user_id,
            safe_details=details,
        ),
        audit=ManagementAuditFact(
            event_id=event_id,
            operation_id=operation_id,
            actor_role=actor.role,
            actor_action=actor.action,
            actor_envelope_id=actor.envelope_id,
            outcome=details.outcome.status,
        ),
    )
