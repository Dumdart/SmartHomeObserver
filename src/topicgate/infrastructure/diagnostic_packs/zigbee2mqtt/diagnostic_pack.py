from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from topicgate.core.interfaces.diagnostic_pack import PackReference
from topicgate.core.models.health import Condition
from topicgate.core.models.health import HealthExpectation
from topicgate.core.models.health import HealthSeverity
from topicgate.core.models.health import TopicTarget
from topicgate.infrastructure.diagnostic_packs.zigbee2mqtt.json_condition import (
    Zigbee2MqttJsonCondition,
)


@dataclass(frozen=True)
class PackCheck:
    check_id: str
    name: str
    description: str
    topic: str
    json_field: str | None
    condition: Condition


class Zigbee2MqttDiagnosticPack:
    def __init__(
        self,
        reference: PackReference,
        checks: tuple[PackCheck, ...],
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
