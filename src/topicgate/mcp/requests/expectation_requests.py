"""Explicit wire encodings for the existing health domain conditions."""

import base64
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from topicgate.core.models.health import (
    ActionKind,
    BrokerTarget,
    HealthExpectation,
    HealthSeverity,
    TopicTarget,
    Condition,
    ExpectationTarget,
)
from topicgate.core.models.health.condition import (
    EqualCondition,
    FreshnessCondition,
    InRangeCondition,
    NumericRangeCondition,
    OutSideCondition,
    TopicAbsentCondition,
    TopicExistsCondition,
)
from topicgate.core.mqtt_topics import validate_topic_name


class RequestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )


class BrokerTargetRequest(RequestModel):
    kind: Literal["broker"]


class TopicTargetRequest(RequestModel):
    kind: Literal["topic"]
    topic: str = Field(min_length=1, max_length=65535)

    @field_validator("topic")
    @classmethod
    def valid_topic(cls, value: str) -> str:
        validate_topic_name(value)
        return value


TargetRequest = Annotated[
    BrokerTargetRequest | TopicTargetRequest, Field(discriminator="kind")
]


class PayloadRequest(RequestModel):
    encoding: Literal["utf8", "base64", "text"]
    value: str = Field(max_length=8192)

    @model_validator(mode="after")
    def valid_encoding(self) -> Self:
        self.decode()
        return self

    def decode(self) -> bytes | str:
        if self.encoding == "base64":
            return base64.b64decode(self.value, validate=True)
        if self.encoding == "utf8":
            return self.value.encode("utf-8")
        return self.value


class EqualRequest(RequestModel):
    kind: Literal["equal"]
    expected: PayloadRequest


class MembershipRequest(RequestModel):
    kind: Literal["in", "outside"]
    expected: tuple[PayloadRequest, ...] = Field(min_length=1, max_length=100)


class NumericRequest(RequestModel):
    kind: Literal["numeric_range"]
    minimum: Decimal
    maximum: Decimal

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        NumericRangeCondition(self.minimum, self.maximum)
        return self


class PresenceRequest(RequestModel):
    kind: Literal["exists", "absent"]


class FreshnessRequest(RequestModel):
    kind: Literal["freshness"]
    max_age_seconds: float = Field(gt=0)


ConditionRequest = Annotated[
    EqualRequest
    | MembershipRequest
    | NumericRequest
    | PresenceRequest
    | FreshnessRequest,
    Field(discriminator="kind"),
]


def target_model(target: TargetRequest, broker_id: UUID) -> ExpectationTarget:
    if isinstance(target, TopicTargetRequest):
        return TopicTarget(broker_id, target.topic)
    return BrokerTarget(broker_id)


def condition_model(
    condition: ConditionRequest, target: ExpectationTarget
) -> Condition:
    if isinstance(condition, (EqualRequest, MembershipRequest)):
        values = (
            (condition.expected,)
            if isinstance(condition, EqualRequest)
            else condition.expected
        )
        decoded = tuple(item.decode() for item in values)
        wanted_type = str if isinstance(target, BrokerTarget) else bytes
        if any(type(value) is not wanted_type for value in decoded):
            raise ValueError("Use text for broker status; utf8/base64 for MQTT bytes.")
        if isinstance(condition, EqualRequest):
            return EqualCondition(decoded[0])
        return (InRangeCondition if condition.kind == "in" else OutSideCondition)(
            decoded
        )
    if isinstance(condition, NumericRequest):
        return NumericRangeCondition(condition.minimum, condition.maximum)
    if isinstance(condition, FreshnessRequest):
        return FreshnessCondition(condition.max_age_seconds)
    return (
        TopicExistsCondition() if condition.kind == "exists" else TopicAbsentCondition()
    )


class CreateExpectationRequest(RequestModel):
    target: TargetRequest
    condition: ConditionRequest
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    enabled: bool = True
    severity: HealthSeverity = HealthSeverity.CRITICAL
    actions: frozenset[ActionKind] = frozenset({ActionKind.STORE_FAILURE})

    @field_validator("name", "description")
    @classmethod
    def metadata(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Expectation metadata must not be blank.")
        return value


class UpdateExpectationRequest(RequestModel):
    target: TargetRequest | None = None
    condition: ConditionRequest | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    enabled: bool | None = None
    severity: HealthSeverity | None = None
    actions: frozenset[ActionKind] | None = None

    @model_validator(mode="after")
    def explicit_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Specify at least one changed field.")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Changed fields cannot be null.")
        return self


def definition(item: HealthExpectation) -> dict:
    condition = item.condition

    def payload(value):
        if isinstance(value, bytes):
            return {
                "encoding": "base64",
                "value": base64.b64encode(value).decode("ascii"),
            }
        return {"encoding": "text", "value": value}

    if isinstance(condition, EqualCondition):
        wire = {"kind": "equal", "expected": payload(condition.expected_value)}
    elif isinstance(condition, (InRangeCondition, OutSideCondition)):
        wire = {
            "kind": "in" if isinstance(condition, InRangeCondition) else "outside",
            "expected": [payload(value) for value in condition.expected_values],
        }
    elif isinstance(condition, NumericRangeCondition):
        wire = {
            "kind": "numeric_range",
            "minimum": str(condition.minimum),
            "maximum": str(condition.maximum),
        }
    elif isinstance(condition, FreshnessCondition):
        wire = {"kind": "freshness", "max_age_seconds": condition.max_age_seconds}
    elif isinstance(condition, (TopicExistsCondition, TopicAbsentCondition)):
        wire = {
            "kind": "exists"
            if isinstance(condition, TopicExistsCondition)
            else "absent"
        }
    else:
        raise ValueError("Unsupported persisted condition.")
    return {
        "expectation_id": item.expectation_id,
        "broker_id": item.target.broker_id,
        "revision": item.revision,
        "enabled": item.enabled,
        "name": item.name,
        "description": item.description,
        "severity": item.severity,
        "actions": sorted(item.actions),
        "condition": wire,
        "target": (
            {"kind": "topic", "topic": item.target.topic}
            if isinstance(item.target, TopicTarget)
            else {"kind": "broker"}
        ),
    }
