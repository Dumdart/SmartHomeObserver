import json
import math
from dataclasses import dataclass

from topicgate.core.models.health import Condition
from topicgate.core.models.health import ConditionResult
from topicgate.core.models.health import HealthStatus
from topicgate.core.models.health.condition import PayloadValue


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


def _payload_text(payload: PayloadValue) -> str:
    return payload.decode("utf-8") if isinstance(payload, bytes) else payload


def _unknown_payload(summary: str) -> ConditionResult:
    return ConditionResult(
        status=HealthStatus.UNKNOWN,
        failure_code="UNSUPPORTED_PACK_PAYLOAD",
        evidence_summary=summary,
        evidence_complete=False,
    )
