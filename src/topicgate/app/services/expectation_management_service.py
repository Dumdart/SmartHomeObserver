from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from topicgate.core.interfaces.health_repositories import (
    ExpectationFailureStore,
    ExpectationStateStore,
    HealthExpectationReader,
    TransactionManager,
)
from topicgate.core.models.health import (
    ActionKind,
    Condition,
    ExpectationTarget,
    HealthExpectation,
    HealthSeverity,
    HealthStatus,
    TopicTarget,
)
from topicgate.core.models.subscription import Subscription
from topicgate.core.mqtt_topics import mqtt_filter_matches
from topicgate.core.models.health.condition import FreshnessCondition
from topicgate.core.models.health.condition import NumericRangeCondition
from topicgate.core.models.health.condition import TopicAbsentCondition
from topicgate.core.models.health.condition import TopicExistsCondition


SubscriptionsReader = Callable[[UUID], tuple[Subscription, ...]]
TOPIC_ONLY_CONDITIONS = (
    NumericRangeCondition,
    TopicExistsCondition,
    TopicAbsentCondition,
    FreshnessCondition,
)


class ExpectationManagementService:
    """Manage expectations while keeping their incident history consistent."""

    def __init__(
        self,
        health_expectation_repository: HealthExpectationReader,
        expectation_state_repository: ExpectationStateStore | None = None,
        expectation_failure_repository: ExpectationFailureStore | None = None,
        transaction_manager: TransactionManager | None = None,
        subscriptions_reader: SubscriptionsReader | None = None,
    ) -> None:
        self._expectation_repo = health_expectation_repository
        self._state_repo = expectation_state_repository
        self._failure_repo = expectation_failure_repository
        self._transaction_manager = transaction_manager
        self._subscriptions_reader = subscriptions_reader

    def list_expectations(self, broker_id: UUID) -> tuple[HealthExpectation, ...]:
        return self._expectation_repo.list_for_broker(broker_id)

    def get_expectation(
        self, broker_id: UUID, expectation_id: UUID
    ) -> HealthExpectation:
        expectation = self._expectation_repo.get(expectation_id)
        if expectation is None:
            raise KeyError(f"Unknown health expectation: {expectation_id}")
        self._check_broker_scope(expectation, broker_id)
        return expectation

    def create_expectation(
        self,
        expectation: HealthExpectation,
        *,
        broker_id: UUID | None = None,
    ) -> HealthExpectation:
        self._check_broker_scope(expectation, broker_id)
        if expectation.revision < 1:
            raise ValueError("Expectation revision must be positive.")
        self.validate_condition_target(expectation)
        self.validate_topic_observability(expectation)
        return self._expectation_repo.create(expectation)

    def edit_expectation(
        self,
        expectation_id: UUID,
        is_enabled: bool | None = None,
        new_severity: HealthSeverity | None = None,
        new_target: ExpectationTarget | None = None,
        new_condition: Condition | None = None,
        new_actions: frozenset[ActionKind] | None = None,
        *,
        broker_id: UUID | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> HealthExpectation:
        current = self._expectation_repo.get(expectation_id)
        if current is None:
            raise KeyError(f"Unknown health expectation: {expectation_id}")
        self._check_broker_scope(current, broker_id)

        target = current.target if new_target is None else new_target
        if target != current.target:
            self.validate_topic_observability(replace(current, target=target))

        behavior_changed = (
            target != current.target
            or (new_condition is not None and new_condition != current.condition)
        )
        updated = replace(
            current,
            enabled=current.enabled if is_enabled is None else is_enabled,
            severity=current.severity if new_severity is None else new_severity,
            target=target,
            condition=(
                current.condition if new_condition is None else new_condition
            ),
            actions=current.actions if new_actions is None else new_actions,
            name=current.name if name is None else _metadata_text(name, "name"),
            description=(
                current.description
                if description is None
                else _metadata_text(description, "description")
            ),
            revision=current.revision + 1 if behavior_changed else current.revision,
        )
        self.validate_condition_target(updated)

        if behavior_changed:
            self._supersede_active_revision(current, updated.revision)
        return self._expectation_repo.update(updated)

    def enable_expectation(
        self, expectation_id: UUID, *, broker_id: UUID | None = None
    ) -> HealthExpectation:
        return self.edit_expectation(
            expectation_id, broker_id=broker_id, is_enabled=True
        )

    def disable_expectation(
        self, expectation_id: UUID, *, broker_id: UUID | None = None
    ) -> HealthExpectation:
        return self.edit_expectation(
            expectation_id, broker_id=broker_id, is_enabled=False
        )

    def delete_expectation(
        self,
        expectation_id: UUID,
        *,
        broker_id: UUID | None = None,
    ) -> None:
        expectation = self._expectation_repo.get(expectation_id)
        if expectation is None:
            raise KeyError(f"Unknown health expectation: {expectation_id}")
        self._check_broker_scope(expectation, broker_id)
        self._close_active_failure(expectation)
        self._expectation_repo.delete(expectation_id, retain_history=True)

    def is_topic_observable(self, target: ExpectationTarget) -> bool:
        if not isinstance(target, TopicTarget):
            return True
        if self._subscriptions_reader is None:
            return True
        subscriptions = self._subscriptions_reader(target.broker_id)
        return any(
            mqtt_filter_matches(subscription.topic_filter, target.topic)
            for subscription in subscriptions
        )

    def validate_topic_observability(self, expectation: HealthExpectation) -> None:
        if isinstance(expectation.target, TopicTarget) and not self.is_topic_observable(
            expectation.target
        ):
            raise ValueError(
                f"Topic {expectation.target.topic!r} is not covered by a "
                "subscription for this broker profile."
            )

    @staticmethod
    def validate_condition_target(expectation: HealthExpectation) -> None:
        if isinstance(expectation.condition, TOPIC_ONLY_CONDITIONS) and not isinstance(
            expectation.target,
            TopicTarget,
        ):
            raise ValueError(
                "Numeric range, existence, and freshness conditions require a "
                "topic target."
            )

    def _supersede_active_revision(
        self,
        expectation: HealthExpectation,
        new_revision: int,
    ) -> None:
        if (
            self._state_repo is None
            or self._failure_repo is None
            or self._transaction_manager is None
        ):
            return
        now = datetime.now(timezone.utc)
        with self._transaction_manager.transaction() as transaction:
            state = self._state_repo.get(
                expectation.expectation_id,
                transaction=transaction,
            )
            if state is None:
                return
            self._close_failure_for_state(state.active_failure_id, now, transaction)
            self._state_repo.upsert(
                replace(
                    state,
                    expectation_revision=new_revision,
                    current_status=HealthStatus.UNKNOWN,
                    active_failure_id=None,
                ),
                transaction=transaction,
            )

    def _close_active_failure(self, expectation: HealthExpectation) -> None:
        if (
            self._state_repo is None
            or self._failure_repo is None
            or self._transaction_manager is None
        ):
            return
        now = datetime.now(timezone.utc)
        with self._transaction_manager.transaction() as transaction:
            state = self._state_repo.get(
                expectation.expectation_id,
                transaction=transaction,
            )
            if state is not None:
                self._close_failure_for_state(state.active_failure_id, now, transaction)

    def _close_failure_for_state(
        self,
        failure_id: UUID | None,
        closed_at: datetime,
        transaction: object,
    ) -> None:
        if failure_id is None or self._failure_repo is None:
            return
        failure = self._failure_repo.get(failure_id, transaction=transaction)
        if failure is None or failure.recovered_at is not None:
            return
        self._failure_repo.upsert(
            replace(failure, recovered_at=closed_at),
            transaction=transaction,
        )

    @staticmethod
    def _check_broker_scope(
        expectation: HealthExpectation,
        broker_id: UUID | None,
    ) -> None:
        target_broker_id = getattr(expectation.target, "broker_id", None)
        if broker_id is not None and target_broker_id != broker_id:
            raise ValueError("The expectation does not belong to this broker profile.")


def _metadata_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Expectation {field_name} is required.")
    return normalized
