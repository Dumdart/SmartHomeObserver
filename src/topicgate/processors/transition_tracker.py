from uuid import uuid4

from topicgate.core.models.health import ExpectationEvaluation
from topicgate.core.models.health import ExpectationState
from topicgate.core.models.health import HealthStatus
from topicgate.core.models.health import HealthTransition


class TransitionTracker:
    def apply(
        self,
        previous_state: ExpectationState | None,
        evaluation: ExpectationEvaluation,
    ) -> tuple[ExpectationState, HealthTransition | None]:
        active_failure_id = (
            None if previous_state is None else previous_state.active_failure_id
        )
        last_healthy_at = (
            None if previous_state is None else previous_state.last_healthy_at
        )
        transition: HealthTransition | None = None

        revision_changed = (
            previous_state is not None
            and previous_state.expectation_revision > 0
            and previous_state.expectation_revision
            != evaluation.expectation_revision
        )
        if revision_changed:
            # Check a revision change starts a fresh incident for the new rule.
            active_failure_id = None

        if evaluation.status is HealthStatus.HEALTHY:
            last_healthy_at = evaluation.evaluated_at
            if active_failure_id is not None:
                transition = HealthTransition.RECOVERY
                active_failure_id = None
        elif evaluation.status is HealthStatus.PROBLEM:
            if active_failure_id is not None:
                transition = HealthTransition.ONGOING_FAILURE
            else:
                transition = HealthTransition.NEW_FAILURE
                active_failure_id = uuid4()

        return (
            ExpectationState(
                expectation_id=evaluation.expectation_id,
                current_status=evaluation.status,
                expectation_revision=evaluation.expectation_revision,
                last_evaluated_at=evaluation.evaluated_at,
                last_healthy_at=last_healthy_at,
                active_failure_id=active_failure_id,
            ),
            transition,
        )
