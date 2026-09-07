from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from topicgate.core.models.health.health_enums import HealthStatus


@dataclass(frozen=True)
class ExpectationEvaluation:
    expectation_id: UUID
    expectation_revision: int
    status: HealthStatus
    evaluated_at: datetime
    failure_code: str | None
    evidence_summary: str | None
    evidence_complete: bool
