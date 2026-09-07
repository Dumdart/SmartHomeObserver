from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from topicgate.core.models.health import ExpectationEvaluation
from topicgate.core.models.health import ExpectationFailure
from topicgate.core.models.health import HealthSeverity
from topicgate.core.models.health.health_transition import HealthTransition


@dataclass(frozen=True)
class HealthActionContext:
    evaluation: ExpectationEvaluation
    transition: HealthTransition
    severity: HealthSeverity
    failure: ExpectationFailure | None

    @property
    def expectation_id(self) -> UUID:
        return self.evaluation.expectation_id

    @property
    def expectation_revision(self) -> int:
        return self.evaluation.expectation_revision

    @property
    def evaluated_at(self) -> datetime:
        return self.evaluation.evaluated_at
