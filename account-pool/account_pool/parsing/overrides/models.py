"""定义可审计人工覆盖事件及不依赖数组顺序的稳定目标。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, TypeAdapter, model_validator

from account_pool.models import FrozenModel
from account_pool.parsing.safety import has_safe_parser_content

_JSON_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)


class OverrideAction(StrEnum):
    SET = "set"
    REVOKE = "revoke"


class RootField(StrEnum):
    SUBSCRIPTION = "subscription"
    METERED = "metered"
    BILLING_ROUTES = "billing_routes"
    CAPABILITIES = "capabilities"
    UNRESOLVED_FIELDS = "unresolved_fields"
    EVIDENCE = "evidence"
    WARNINGS = "warnings"


class SubscriptionField(StrEnum):
    PLAN_ID = "plan_id"
    PLAN_NAME = "plan_name"
    STATUS = "status"
    STARTS_AT = "starts_at"
    EXPIRES_AT = "expires_at"
    MODELS = "models"
    BALANCE = "balance"
    CURRENCY = "currency"
    CHANNEL_CONCURRENCY = "channel_concurrency"
    MODEL_CONCURRENCY = "model_concurrency"
    LIMITS = "limits"


class RootFieldTarget(FrozenModel):
    kind: Literal["root_field"] = "root_field"
    field: RootField

    def field_path(self) -> str:
        return f"/{self.field}"


class SubscriptionFieldTarget(FrozenModel):
    kind: Literal["subscription_field"] = "subscription_field"
    field: SubscriptionField

    def field_path(self) -> str:
        return f"/subscription/{self.field}"


class SubscriptionModelTarget(FrozenModel):
    kind: Literal["subscription_model"] = "subscription_model"
    provider_model_id: str = Field(min_length=1)

    def field_path(self) -> str:
        return f"/subscription/models/{_pointer_segment(self.provider_model_id)}"


class MeteredGroupTarget(FrozenModel):
    kind: Literal["metered_group"] = "metered_group"
    group_id: str = Field(min_length=1)

    def field_path(self) -> str:
        return f"/metered/groups/{_pointer_segment(self.group_id)}"


class MeteredPriceTarget(FrozenModel):
    kind: Literal["metered_price"] = "metered_price"
    group_id: str = Field(min_length=1)
    provider_model_id: str = Field(min_length=1)

    def field_path(self) -> str:
        return (
            f"/metered/groups/{_pointer_segment(self.group_id)}"
            f"/models/{_pointer_segment(self.provider_model_id)}"
        )


class BillingRouteTarget(FrozenModel):
    kind: Literal["billing_route"] = "billing_route"
    route_id: UUID

    def field_path(self) -> str:
        return f"/billing_routes/{self.route_id}"


OverrideTarget = Annotated[
    RootFieldTarget
    | SubscriptionFieldTarget
    | SubscriptionModelTarget
    | MeteredGroupTarget
    | MeteredPriceTarget
    | BillingRouteTarget,
    Field(discriminator="kind"),
]


class FieldOverrideEvent(FrozenModel):
    override_id: UUID
    channel_id: UUID
    source_parser_run_id: UUID
    target: OverrideTarget
    action: OverrideAction
    value: JsonValue | None
    had_previous_override: bool = False
    previous_value: JsonValue | None = None
    supersedes_override_id: UUID | None = None
    actor_id: str = Field(min_length=1)
    actor_role: str | None = Field(default=None, min_length=1)
    request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    reason: str = Field(min_length=1)
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.action == OverrideAction.REVOKE and self.value is not None:
            raise ValueError("a revoke event cannot contain a replacement value")
        if self.had_previous_override and self.supersedes_override_id is None:
            raise ValueError("a previous override requires a superseded event ID")
        if self.action == OverrideAction.REVOKE and not self.had_previous_override:
            raise ValueError("a revoke event requires an active previous override")
        if (self.actor_role is None) != (self.request_id is None):
            raise ValueError("actor role and request ID must be stored together")
        serialized_values = (
            _JSON_VALUE.dump_json(self.value).decode("utf-8"),
            _JSON_VALUE.dump_json(self.previous_value).decode("utf-8"),
        )
        if any(not has_safe_parser_content(value) for value in serialized_values) or not has_safe_parser_content(
            self.reason
        ):
            raise ValueError("override contains content that cannot be persisted")
        return self

    def field_path(self) -> str:
        return self.target.field_path()


def _pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
