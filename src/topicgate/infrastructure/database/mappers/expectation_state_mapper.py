from topicgate.core.models.health.expectation_state import ExpectationState
from topicgate.core.models.health.health_enums import HealthStatus
from topicgate.infrastructure.database.models.expectation_state_row import (
    ExpectationStateRow,
)


class ExpectationStateMapper:
    """Convert persisted expectation state to and from domain models."""

    @staticmethod
    def to_row(state: ExpectationState) -> ExpectationStateRow:
        return ExpectationStateRow(
            expectation_id=state.expectation_id,
            current_status=state.current_status.value,
            expectation_revision=state.expectation_revision,
            last_evaluated_at=state.last_evaluated_at,
            last_healthy_at=state.last_healthy_at,
            active_failure_id=state.active_failure_id,
        )

    @staticmethod
    def to_model(row: ExpectationStateRow) -> ExpectationState:
        return ExpectationState(
            expectation_id=row.expectation_id,
            current_status=HealthStatus(row.current_status),
            expectation_revision=getattr(row, "expectation_revision", 0) or 0,
            last_evaluated_at=row.last_evaluated_at,
            last_healthy_at=row.last_healthy_at,
            active_failure_id=row.active_failure_id,
        )
