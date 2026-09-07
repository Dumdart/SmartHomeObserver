from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from topicgate.core.models.health import (
    HealthStatus,
    ObservationHealthFinding,
)


@dataclass(frozen=True)
class ExpectationHealthFinding:
    expectation_id: UUID
    expectation_revision: int
    name: str
    description: str
    target_kind: str
    target: str
    status: HealthStatus
    failure_code: str | None
    evidence_summary: str | None
    evidence_complete: bool
    evidence_truncated: bool


@dataclass(frozen=True)
class ExpectationHealthReport:
    broker_id: UUID
    evaluated_at: datetime
    aggregate_status: HealthStatus
    evidence_complete: bool
    observation_status: HealthStatus
    observation_findings: tuple[ObservationHealthFinding, ...]
    expectation_findings: tuple[ExpectationHealthFinding, ...]
    active_failure_count: int
    returned_count: int
    omitted_count: int


@dataclass(frozen=True)
class FailureHistoryItem:
    failure_id: UUID
    expectation_id: UUID
    broker_id: UUID
    target: str
    first_failed_at: datetime
    last_seen_at: datetime
    recovered_at: datetime | None
    occurrence_count: int
    expected_revision: int
    last_healthy_at: datetime | None
    failure_code: str | None
    evidence_summary: str | None
    evidence_truncated: bool
    evidence_limitations: tuple[str, ...]


@dataclass(frozen=True)
class FailureHistoryResult:
    items: tuple[FailureHistoryItem, ...]
    next_cursor: int | None
    returned_count: int

