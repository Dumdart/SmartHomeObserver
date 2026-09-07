from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from topicgate.core.models.health.health_enums import HealthStatus


@dataclass
class ExpectationState:
    expectation_id: UUID
    current_status: HealthStatus
    last_evaluated_at: datetime | None = None
    last_healthy_at: datetime | None = None
    active_failure_id: UUID | None = None
    expectation_revision: int = 0
