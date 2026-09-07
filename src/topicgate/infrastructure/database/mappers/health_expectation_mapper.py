import base64
from typing import Any
from uuid import UUID

from topicgate.core.models.health.condition import Condition
from topicgate.core.models.health.condition import EqualCondition
from topicgate.core.models.health.condition import InRangeCondition
from topicgate.core.models.health.condition import OutSideCondition
from topicgate.core.models.health.expectation_target import BrokerTarget
from topicgate.core.models.health.expectation_target import ExpectationTarget
from topicgate.core.models.health.expectation_target import TopicTarget
from topicgate.core.models.health.health_enums import ActionKind
from topicgate.core.models.health.health_enums import HealthSeverity
from topicgate.core.models.health.health_expectation import HealthExpectation
from topicgate.infrastructure.database.models.health_expectation_row import (
    HealthExpectationRow,
)


class HealthExpectationMapper:
    """Convert health expectations and their JSON fields to and from rows."""

    @staticmethod
    def to_row(expectation: HealthExpectation) -> HealthExpectationRow:
        return HealthExpectationRow(
            expectation_id=expectation.expectation_id,
            revision=expectation.revision,
            enabled=expectation.enabled,
            severity=expectation.severity.value,
            target=HealthExpectationMapper._target_to_dict(expectation.target),
            condition=HealthExpectationMapper._condition_to_dict(
                expectation.condition
            ),
            actions=sorted(action.value for action in expectation.actions),
            name=expectation.name,
            description=expectation.description,
        )

    @staticmethod
    def to_model(row: HealthExpectationRow) -> HealthExpectation:
        return HealthExpectation(
            expectation_id=row.expectation_id,
            revision=row.revision,
            enabled=row.enabled,
            severity=HealthSeverity(row.severity),
            target=HealthExpectationMapper._dict_to_target(row.target),
            condition=HealthExpectationMapper._dict_to_condition(row.condition),
            actions=frozenset(ActionKind(action) for action in row.actions),
            name=getattr(row, "name", "") or "",
            description=getattr(row, "description", "") or "",
        )

    @staticmethod
    def _target_to_dict(target: ExpectationTarget) -> dict[str, str]:
        if isinstance(target, BrokerTarget):
            return {"kind": "broker", "broker_id": str(target.broker_id)}
        if isinstance(target, TopicTarget):
            return {
                "kind": "topic",
                "broker_id": str(target.broker_id),
                "topic": target.topic,
            }
        raise ValueError(f"Unsupported expectation target: {type(target).__name__}")

    @staticmethod
    def _dict_to_target(value: dict[str, Any]) -> ExpectationTarget:
        kind = value.get("kind")
        broker_id = HealthExpectationMapper._required_uuid(value, "broker_id")
        if kind == "broker":
            return BrokerTarget(broker_id=broker_id)
        if kind == "topic":
            topic = value.get("topic")
            if not isinstance(topic, str):
                raise ValueError("Expectation topic must be a string.")
            return TopicTarget(broker_id=broker_id, topic=topic)
        raise ValueError(f"Unsupported expectation target kind: {kind!r}")

    @staticmethod
    def _condition_to_dict(condition: Condition) -> dict[str, Any]:
        if isinstance(condition, EqualCondition):
            return HealthExpectationMapper._encode_condition_values(
                "equal",
                "expected_value",
                (condition.expected_value,),
                scalar=True,
            )
        if isinstance(condition, InRangeCondition):
            return HealthExpectationMapper._encode_condition_values(
                "in_range",
                "expected_values",
                condition.expected_values,
            )
        if isinstance(condition, OutSideCondition):
            return HealthExpectationMapper._encode_condition_values(
                "outside",
                "expected_values",
                condition.expected_values,
            )
        raise ValueError(
            f"Unsupported expectation condition: {type(condition).__name__}"
        )

    @staticmethod
    def _dict_to_condition(value: dict[str, Any]) -> Condition:
        if not isinstance(value, dict):
            raise ValueError("Expectation condition must be an object.")

        kind = value.get("kind")
        if kind == "equal":
            expected_values = HealthExpectationMapper._decode_condition_values(
                value,
                "expected_value",
                scalar=True,
            )
            return EqualCondition(expected_value=expected_values[0])
        if kind == "in_range":
            expected_values = HealthExpectationMapper._decode_condition_values(
                value,
                "expected_values",
            )
            return InRangeCondition(expected_values=expected_values)
        if kind == "outside":
            expected_values = HealthExpectationMapper._decode_condition_values(
                value,
                "expected_values",
            )
            return OutSideCondition(expected_values=expected_values)
        raise ValueError(
            "Unsupported expectation condition kind: "
            f"{kind!r}"
        )

    @staticmethod
    def _encode_condition_values(
        kind: str,
        field_name: str,
        values: tuple[bytes | str, ...],
        *,
        scalar: bool = False,
    ) -> dict[str, Any]:
        if not values:
            raise ValueError("Expectation condition values must not be empty.")
        if any(type(value) is not type(values[0]) for value in values):
            raise ValueError(
                "Expectation condition values must have the same type."
            )

        encoded_values: list[str]
        if isinstance(values[0], bytes):
            encoded_values = [
                base64.b64encode(value).decode("ascii")
                for value in values
            ]
            result: dict[str, Any] = {
                "kind": kind,
                field_name: encoded_values[0] if scalar else encoded_values,
                "value_type": "bytes",
            }
            return result
        if isinstance(values[0], str):
            return {
                "kind": kind,
                field_name: values[0] if scalar else list(values),
            }
        raise ValueError(
            "Expectation condition values must be bytes or strings."
        )

    @staticmethod
    def _decode_condition_values(
        value: dict[str, Any],
        field_name: str,
        *,
        scalar: bool = False,
    ) -> tuple[bytes | str, ...]:
        raw_values = value.get(field_name)
        if scalar:
            if not isinstance(raw_values, str):
                raise ValueError(
                    f"Expectation {field_name} must be a string."
                )
            raw_items = (raw_values,)
        else:
            if not isinstance(raw_values, list) or not raw_values:
                raise ValueError(
                    f"Expectation {field_name} must be a non-empty list."
                )
            raw_items = tuple(raw_values)

        value_type = value.get("value_type")
        if value_type == "bytes":
            if not all(isinstance(item, str) for item in raw_items):
                raise ValueError(
                    f"Encoded bytes in {field_name} must be strings."
                )
            try:
                return tuple(
                    base64.b64decode(item, validate=True)
                    for item in raw_items
                )
            except ValueError as error:
                raise ValueError(
                    f"Encoded bytes in {field_name} are invalid."
                ) from error
        if value_type is not None:
            raise ValueError(
                f"Unsupported expectation condition value type: {value_type!r}"
            )
        if not all(isinstance(item, str) for item in raw_items):
            raise ValueError(
                f"Expectation {field_name} values must be strings."
            )
        return tuple(raw_items)

    @staticmethod
    def _required_uuid(value: dict[str, Any], field_name: str) -> UUID:
        raw_value = value.get(field_name)
        if not isinstance(raw_value, (str, UUID)):
            raise ValueError(f"Expectation {field_name} must be a UUID.")
        try:
            return raw_value if isinstance(raw_value, UUID) else UUID(raw_value)
        except ValueError as error:
            raise ValueError(f"Expectation {field_name} must be a UUID.") from error
