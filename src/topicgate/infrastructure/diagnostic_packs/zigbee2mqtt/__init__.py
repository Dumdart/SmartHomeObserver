from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal
from importlib.resources import files
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from topicgate.core.interfaces.diagnostic_pack import PackReference
from topicgate.core.models.health import Condition
from topicgate.core.models.health import ConditionResult
from topicgate.core.models.health import EqualCondition
from topicgate.core.models.health import FreshnessCondition
from topicgate.core.models.health import HealthExpectation
from topicgate.core.models.health import HealthSeverity
from topicgate.core.models.health import HealthStatus
from topicgate.core.models.health import NumericRangeCondition
from topicgate.core.models.health import TopicExistsCondition
from topicgate.core.models.health import TopicTarget
from topicgate.core.models.health.condition import PayloadValue

PACK_RESOURCE = "v1/pack.json"


@dataclass(frozen=True)
class _PackCheck:
    check_id: str
    name: str
    description: str
    topic: str
    json_field: str | None
    condition: Condition


@dataclass(frozen=True)
class Zigbee2MqttJsonCondition(Condition):
    """Adapt one Zigbee2MQTT JSON field to a standard health condition."""

    field: str
    condition: Condition

    def handle_condition(self, actual: PayloadValue) -> ConditionResult:
        try:
            document = json.loads(_payload_text(actual))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _unknown_payload("Payload is not valid Zigbee2MQTT JSON.")

        if not isinstance(document, dict) or self.field not in document:
            return _unknown_payload(
                f"Zigbee2MQTT payload does not contain field {self.field!r}."
            )

        value = document[self.field]
        if isinstance(value, str):
            adapted = value.encode("utf-8")
        elif (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        ):
            adapted = str(value).encode("ascii")
        else:
            return _unknown_payload(
                f"Zigbee2MQTT field {self.field!r} is not a supported scalar."
            )
        return self.condition.handle_condition(adapted)


class Zigbee2MqttDiagnosticPack:
    def __init__(
        self,
        reference: PackReference,
        checks: tuple[_PackCheck, ...],
    ) -> None:
        self._reference = reference
        self._checks = checks

    @property
    def reference(self) -> PackReference:
        return self._reference

    def build_expectations(
        self,
        broker_id: UUID,
    ) -> tuple[HealthExpectation, ...]:
        return tuple(
            HealthExpectation(
                expectation_id=uuid5(
                    NAMESPACE_URL,
                    ":".join(
                        (
                            "topicgate",
                            self.reference.pack_id,
                            self.reference.version,
                            check.check_id,
                            str(broker_id),
                        )
                    ),
                ),
                revision=1,
                enabled=True,
                severity=HealthSeverity.CRITICAL,
                target=TopicTarget(broker_id, check.topic),
                condition=(
                    Zigbee2MqttJsonCondition(check.json_field, check.condition)
                    if check.json_field is not None
                    else check.condition
                ),
                actions=frozenset(),
                name=check.name,
                description=check.description,
            )
            for check in self._checks
        )


def load_zigbee2mqtt_pack() -> Zigbee2MqttDiagnosticPack:
    resource = files(__package__).joinpath(PACK_RESOURCE)
    data = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Zigbee2MQTT diagnostic pack must be a JSON object.")

    reference = PackReference(
        pack_id=_required_string(data, "pack_id"),
        version=_required_string(data, "version"),
    )
    raw_checks = data.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise ValueError("Zigbee2MQTT diagnostic pack must contain checks.")
    return Zigbee2MqttDiagnosticPack(
        reference,
        tuple(_parse_check(item) for item in raw_checks),
    )


def _parse_check(value: Any) -> _PackCheck:
    if not isinstance(value, dict):
        raise ValueError("Diagnostic pack checks must be JSON objects.")
    json_field = value.get("json_field")
    if json_field is not None and not isinstance(json_field, str):
        raise ValueError("Diagnostic check json_field must be a string.")
    return _PackCheck(
        check_id=_required_string(value, "id"),
        name=_required_string(value, "name"),
        description=_required_string(value, "description"),
        topic=_required_string(value, "topic"),
        json_field=json_field,
        condition=_parse_condition(value.get("condition")),
    )


def _parse_condition(value: Any) -> Condition:
    if not isinstance(value, dict):
        raise ValueError("Diagnostic check condition must be a JSON object.")
    kind = value.get("kind")
    if kind == "equal":
        return EqualCondition(_required_scalar(value, "expected"))
    if kind == "numeric_range":
        return NumericRangeCondition(
            Decimal(_required_number(value, "minimum")),
            Decimal(_required_number(value, "maximum")),
        )
    if kind == "freshness":
        return FreshnessCondition(float(_required_number(value, "max_age_seconds")))
    if kind == "topic_exists":
        return TopicExistsCondition()
    raise ValueError(f"Unsupported diagnostic condition kind: {kind!r}.")


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"Diagnostic pack field {key!r} must be a string.")
    return item


def _required_scalar(value: dict[str, Any], key: str) -> bytes:
    item = value.get(key)
    if not isinstance(item, (str, int, float)) or isinstance(item, bool):
        raise ValueError(f"Diagnostic pack field {key!r} must be a scalar.")
    return str(item).encode("utf-8")


def _required_number(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, (int, float)) or isinstance(item, bool):
        raise ValueError(f"Diagnostic pack field {key!r} must be a number.")
    return str(item)


def _payload_text(payload: PayloadValue) -> str:
    return payload.decode("utf-8") if isinstance(payload, bytes) else payload


def _unknown_payload(summary: str) -> ConditionResult:
    return ConditionResult(
        status=HealthStatus.UNKNOWN,
        failure_code="UNSUPPORTED_PACK_PAYLOAD",
        evidence_summary=summary,
        evidence_complete=False,
    )
