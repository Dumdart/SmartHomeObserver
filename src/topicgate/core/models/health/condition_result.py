from dataclasses import dataclass

from topicgate.core.models.health.health_enums import HealthStatus


@dataclass(frozen=True)
class ConditionResult:
    status: HealthStatus
    failure_code: str | None = None
    evidence_summary: str | None = None
    evidence_complete: bool = True
