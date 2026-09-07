from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from topicgate.core.interfaces.health_repositories import (
    ExpectationFailureStore,
    ExpectationStateStore,
    HealthExpectationReader,
)
from topicgate.core.models.health import (
    BrokerTarget,
    ExpectationFailure,
    ExpectationState,
    HealthExpectation,
    HealthStatus,
    TopicTarget,
)
from topicgate.core.models.subscription import Subscription
from topicgate.core.mqtt_topics import mqtt_filter_matches


SubscriptionsReader = Callable[[UUID], tuple[Subscription, ...]]


class HealthReportService:
    def __init__(
        self,
        health_expectation_repository: HealthExpectationReader,
        expectation_state_repository: ExpectationStateStore,
        expectation_failure_repository: ExpectationFailureStore,
        subscriptions_reader: SubscriptionsReader | None = None,
    ) -> None:
        self._health_expectation_repository = health_expectation_repository
        self._expectation_state_repository = expectation_state_repository
        self._expectation_failure_repository = expectation_failure_repository
        self._subscriptions_reader = subscriptions_reader

    def get_expectation_states(
        self, broker_id: UUID | None = None
    ) -> list[ExpectationState]:
        states = {
            state.expectation_id: state
            for state in self._expectation_state_repository.get_all_states()
        }
        expectations = self._all_expectations()
        if broker_id is not None:
            expectations = tuple(
                expectation
                for expectation in expectations
                if _target_broker_id(expectation.target) == broker_id
            )

        reported: list[ExpectationState] = []
        known_ids = set()
        for expectation in expectations:
            known_ids.add(expectation.expectation_id)
            state = states.get(expectation.expectation_id)
            if state is None:
                state = ExpectationState(
                    expectation_id=expectation.expectation_id,
                    current_status=HealthStatus.UNKNOWN,
                    expectation_revision=expectation.revision,
                )
            elif not self._is_observable(expectation):
                state = replace(state, current_status=HealthStatus.UNKNOWN)
            reported.append(state)

        # Check state rows whose expectation was deleted remain visible to callers that
        # use the read side directly; failure snapshots are handled separately.
        reported.extend(
            state
            for expectation_id, state in states.items()
            if expectation_id not in known_ids
            and (broker_id is None or self._state_belongs_to_broker(state, broker_id))
        )
        return reported

    def get_last_evaluated(self) -> datetime | None:
        return _latest(
            state.last_evaluated_at for state in self.get_expectation_states()
        )

    def get_last_healthy(self) -> datetime | None:
        return _latest(
            state.last_healthy_at for state in self.get_expectation_states()
        )

    def get_active_failures(self) -> list[ExpectationFailure]:
        return [
            failure
            for failure in self._expectation_failure_repository.get_all_states()
            if failure.recovered_at is None
        ]

    def broker_identity(self, failure: ExpectationFailure) -> UUID:
        if failure.snapshot_broker_id is not None:
            return failure.snapshot_broker_id
        expectation = self._get_expectation(failure)
        if expectation is None:
            raise KeyError(f"Unknown health expectation: {failure.expectation_id}")
        target = expectation.target
        if not isinstance(target, (BrokerTarget, TopicTarget)):
            raise TypeError(f"Unsupported expectation target: {type(target).__name__}")
        return target.broker_id

    def target_identity(self, failure: ExpectationFailure) -> str:
        if failure.snapshot_broker_id is not None:
            return failure.snapshot_topic or "broker"
        expectation = self._get_expectation(failure)
        if expectation is None:
            raise KeyError(f"Unknown health expectation: {failure.expectation_id}")
        target = expectation.target
        if isinstance(target, TopicTarget):
            return target.topic
        if isinstance(target, BrokerTarget):
            return "broker"
        raise TypeError(f"Unsupported expectation target: {type(target).__name__}")

    def get_evidence_limitations(
        self, failure: ExpectationFailure
    ) -> tuple[str, ...]:
        if failure.evidence_summary is None:
            return ("evidence_unavailable",)
        return ()

    def _get_expectation(self, failure: ExpectationFailure):
        return self._health_expectation_repository.get(failure.expectation_id)

    def _all_expectations(self) -> tuple[HealthExpectation, ...]:
        list_all = getattr(self._health_expectation_repository, "list_all", None)
        if not callable(list_all):
            return ()
        expectations = list_all()
        return tuple(expectations) if isinstance(expectations, (list, tuple)) else ()

    def _is_observable(self, expectation: HealthExpectation) -> bool:
        if not isinstance(expectation.target, TopicTarget):
            return True
        if self._subscriptions_reader is None:
            return True
        return any(
            mqtt_filter_matches(subscription.topic_filter, expectation.target.topic)
            for subscription in self._subscriptions_reader(expectation.target.broker_id)
        )

    def _state_belongs_to_broker(
        self, state: ExpectationState, broker_id: UUID
    ) -> bool:
        expectation = self._health_expectation_repository.get(state.expectation_id)
        return (
            expectation is not None
            and _target_broker_id(expectation.target) == broker_id
        )


def _target_broker_id(target) -> UUID | None:
    return getattr(target, "broker_id", None)


def _latest(values) -> datetime | None:
    timestamps = [value for value in values if value is not None]
    return max(timestamps) if timestamps else None
