from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from topicgate.core.models.health.expectation_evaluation import (
    ExpectationEvaluation,
)
from topicgate.core.models.health.health_enums import HealthStatus


class ObservationFindingCode(StrEnum):
    BROKER_DISCONNECTED = "broker_disconnected"
    OBSERVATION_NOT_STARTED = "observation_not_started"
    DROPPED_MESSAGES = "dropped_messages"
    RECORDING_FAILURES = "recording_failures"
    SUBSCRIPTION_UNAVAILABLE = "subscription_unavailable"
    SUBSCRIPTION_REJECTED = "subscription_rejected"


@dataclass(frozen=True)
class ObservationHealthFinding:
    code: ObservationFindingCode
    status: HealthStatus
    evidence_summary: str


@dataclass(frozen=True)
class ObservationHealth:
    status: HealthStatus
    findings: tuple[ObservationHealthFinding, ...]
    evidence_complete: bool


@dataclass(frozen=True)
class DiagnosticReport:
    broker_id: UUID
    evaluated_at: datetime
    observation_health: ObservationHealth
    topic_findings: tuple[ExpectationEvaluation, ...]
    aggregate_status: HealthStatus
    evidence_complete: bool

    @property
    def topic_expectation_findings(self) -> tuple[ExpectationEvaluation, ...]:
        return self.topic_findings
