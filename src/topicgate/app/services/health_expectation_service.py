import logging
import math
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from topicgate.core.interfaces.health_repositories import (
    ExpectationFailureStore,
    ExpectationStateStore,
    HealthExpectationReader,
    TransactionManager,
)
from topicgate.core.interfaces.observer_repo_metadata import ObserverRepoMetadata
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.health import (
    BrokerTarget,
    ConditionEvaluationContext,
    ConditionResult,
    DiagnosticReport,
    ExpectationEvaluation,
    ExpectationFailure,
    ExpectationState,
    HealthExpectation,
    HealthStatus,
    HealthTransition,
    ObservationFindingCode,
    ObservationHealth,
    ObservationHealthFinding,
    TopicTarget,
)
from topicgate.core.models.health.condition import FreshnessCondition
from topicgate.core.models.health.condition import NumericRangeCondition
from topicgate.core.models.health.condition import TopicAbsentCondition
from topicgate.core.models.health.condition import TopicExistsCondition
from topicgate.core.models.health.health_action_context import HealthActionContext
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.topic_message import TopicMessage
from topicgate.core.mqtt_topics import mqtt_filter_matches
from topicgate.processors.action_dispatcher import ActionDispatcher
from topicgate.processors.transition_tracker import TransitionTracker

logger = logging.getLogger(__name__)

SubscriptionsReader = Callable[[UUID], tuple[Subscription, ...]]
BrokerMetadataReader = Callable[[UUID], ObserverRepoMetadata]
CurrentTopicsReader = Callable[[UUID], tuple[CurrentTopic, ...]]

DEFAULT_STALE_AFTER_SECONDS = 300.0
TOPIC_STATE_CONDITIONS = (
    TopicExistsCondition,
    TopicAbsentCondition,
    FreshnessCondition,
)
TOPIC_ONLY_CONDITIONS = (NumericRangeCondition, *TOPIC_STATE_CONDITIONS)


class HealthExpectationService:
    def __init__(
        self,
        health_expectation_repo: HealthExpectationReader,
        expectation_state_repo: ExpectationStateStore,
        expectation_failure_repo: ExpectationFailureStore,
        transaction_manager: TransactionManager,
        transition_tracker: TransitionTracker,
        action_dispatcher: ActionDispatcher,
        subscriptions_reader: SubscriptionsReader | None = None,
        broker_metadata_reader: BrokerMetadataReader | None = None,
        current_topics_reader: CurrentTopicsReader | None = None,
    ) -> None:
        self._expectation_repo = health_expectation_repo
        self._state_repo = expectation_state_repo
        self._failure_repo = expectation_failure_repo
        self._transaction_manager = transaction_manager
        self._transition_tracker = transition_tracker
        self._action_dispatcher = action_dispatcher
        self._subscriptions_reader = subscriptions_reader
        self._broker_metadata_reader = broker_metadata_reader
        self._current_topics_reader = current_topics_reader

    def evaluate_observation(
        self,
        topic_msg: TopicMessage,
    ) -> tuple[ExpectationEvaluation, ...]:
        try:
            expectations = self._expectation_repo.list_for_topic(
                topic_msg.broker_id,
                topic_msg.topic,
            )
        except Exception:
            logger.exception(
                "Unable to load health expectations for broker %s topic %s.",
                topic_msg.broker_id,
                topic_msg.topic,
            )
            return ()

        evaluations: list[ExpectationEvaluation] = []
        for expectation in expectations:
            if not expectation.enabled:
                continue
            try:
                evaluations.append(
                    self._evaluate(
                        expectation,
                        topic_msg,
                        observable=self._is_observable(expectation, topic_msg.topic),
                    )
                )
            except Exception:
                logger.exception(
                    "Health expectation %s evaluation failed.",
                    expectation.expectation_id,
                )
        return tuple(evaluations)

    def evaluate_broker(
        self,
        broker_id: UUID,
        *,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        evaluated_at: datetime | None = None,
        deadline_check: Callable[[], None] | None = None,
    ) -> DiagnosticReport:
        """Evaluate broker lifecycle health independently of message delivery."""
        expectations = tuple(
            expectation
            for expectation in self._expectation_repo.list_for_broker(broker_id)
            if expectation.enabled
        )
        return self._evaluate_broker(
            broker_id,
            expectations,
            persist=True,
            stale_after_seconds=stale_after_seconds,
            evaluated_at=evaluated_at,
            deadline_check=deadline_check,
        )

    def preview_broker(
        self,
        broker_id: UUID,
        expectations: tuple[HealthExpectation, ...],
        *,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        evaluated_at: datetime | None = None,
        deadline_check: Callable[[], None] | None = None,
    ) -> DiagnosticReport:
        """Evaluate supplied draft expectations without changing live health state."""
        return self._evaluate_broker(
            broker_id,
            tuple(item for item in expectations if item.enabled),
            persist=False,
            stale_after_seconds=stale_after_seconds,
            evaluated_at=evaluated_at,
            deadline_check=deadline_check,
        )

    def _evaluate_broker(
        self,
        broker_id: UUID,
        expectations: tuple[HealthExpectation, ...],
        *,
        persist: bool,
        stale_after_seconds: float,
        evaluated_at: datetime | None,
        deadline_check: Callable[[], None] | None,
    ) -> DiagnosticReport:
        stale_after_seconds = _validate_stale_after(stale_after_seconds)
        evaluated_at = _as_utc(evaluated_at or datetime.now(timezone.utc))
        if deadline_check is not None:
            deadline_check()
        metadata = self._require_broker_metadata_reader()(broker_id)
        current_topics = {
            current.message.topic: current
            for current in self._require_current_topics_reader()(broker_id)
        }
        subscriptions = self._read_subscriptions(broker_id)
        observation_health = self._observation_health(metadata)

        topic_findings: list[ExpectationEvaluation] = []
        for expectation in expectations:
            if deadline_check is not None:
                deadline_check()
            try:
                result = self._scheduled_condition_result(
                    expectation,
                    metadata,
                    current_topics,
                    subscriptions,
                    evaluated_at,
                    stale_after_seconds,
                )
                # Check fresh topic conditions without recording a second occurrence;
                # message delivery already owns those state transitions.
                persist_result = persist and (
                    isinstance(expectation.target, BrokerTarget)
                    or isinstance(expectation.condition, TOPIC_STATE_CONDITIONS)
                    or result.failure_code
                    in {
                        "SUBSCRIPTION_UNAVAILABLE",
                        "TOPIC_NEVER_OBSERVED",
                        "TOPIC_STALE",
                        "UNSUPPORTED_EXPECTATION_TARGET",
                    }
                )
                topic_findings.append(
                    self._evaluate_condition_result(
                        expectation,
                        result,
                        evaluated_at,
                        count_occurrence=False,
                    ) if persist_result else self._condition_result_to_evaluation(
                        result, expectation, evaluated_at
                    )
                )
            except Exception:
                logger.exception(
                    "Health expectation %s broker evaluation failed.",
                    expectation.expectation_id,
                )

        if deadline_check is not None:
            deadline_check()
        statuses = [observation_health.status]
        statuses.extend(finding.status for finding in topic_findings)
        return DiagnosticReport(
            broker_id=broker_id,
            evaluated_at=evaluated_at,
            observation_health=observation_health,
            topic_findings=tuple(topic_findings),
            aggregate_status=_aggregate_status(statuses),
            evidence_complete=(
                observation_health.evidence_complete
                and len(topic_findings) == len(expectations)
                and all(finding.evidence_complete for finding in topic_findings)
            ),
        )

    def _evaluate(
        self,
        expectation: HealthExpectation,
        topic_msg: TopicMessage,
        *,
        observable: bool = True,
    ) -> ExpectationEvaluation:
        if not observable:
            result = ConditionResult(
                status=HealthStatus.UNKNOWN,
                evidence_complete=False,
                evidence_summary="Topic is not covered by an active subscription.",
            )
        elif (
            topic_msg.is_truncated
            and not isinstance(expectation.condition, TOPIC_STATE_CONDITIONS)
        ):
            result = ConditionResult(
                status=HealthStatus.UNKNOWN,
                evidence_complete=False,
                evidence_summary="Message payload was truncated.",
            )
        else:
            result = expectation.condition.evaluate(
                ConditionEvaluationContext(
                    payload=topic_msg.payload,
                    received_at=topic_msg.received_at,
                    evaluated_at=topic_msg.received_at,
                )
            )

        return self._evaluate_condition_result(
            expectation,
            result,
            topic_msg.received_at,
        )

    def _evaluate_condition_result(
        self,
        expectation: HealthExpectation,
        result: ConditionResult,
        evaluated_at: datetime,
        *,
        count_occurrence: bool = True,
    ) -> ExpectationEvaluation:
        evaluation = self._condition_result_to_evaluation(
            result,
            expectation,
            evaluated_at,
        )

        with self._transaction_manager.transaction() as transaction:
            previous_state = self._state_repo.get(
                expectation.expectation_id,
                transaction=transaction,
            )

            if (
                previous_state is not None
                and previous_state.expectation_revision
                != evaluation.expectation_revision
            ):
                previous_state = self._reset_changed_revision(
                    previous_state,
                    evaluation.evaluated_at,
                    transaction,
                )

            state, transition = self._transition_tracker.apply(
                previous_state,
                evaluation,
            )
            failure = self._update_failure(
                expectation,
                evaluation,
                previous_state,
                state,
                transition,
                transaction=transaction,
                count_occurrence=count_occurrence,
            )
            if failure is not None:
                self._failure_repo.upsert(failure, transaction=transaction)
            self._state_repo.upsert(state, transaction=transaction)

        if transition not in {
            HealthTransition.NEW_FAILURE,
            HealthTransition.RECOVERY,
        }:
            return evaluation
        self._action_dispatcher.dispatch(
            action_kinds=expectation.actions,
            context=HealthActionContext(
                evaluation=evaluation,
                transition=transition,
                severity=expectation.severity,
                failure=failure,
            ),
        )
        return evaluation

    def _scheduled_condition_result(
        self,
        expectation: HealthExpectation,
        metadata: ObserverRepoMetadata,
        current_topics: dict[str, CurrentTopic],
        subscriptions: tuple[Subscription, ...] | None,
        evaluated_at: datetime,
        stale_after_seconds: float,
    ) -> ConditionResult:
        if isinstance(expectation.target, BrokerTarget):
            if isinstance(expectation.condition, TOPIC_ONLY_CONDITIONS):
                return ConditionResult(
                    status=HealthStatus.UNKNOWN,
                    failure_code="UNSUPPORTED_CONDITION_TARGET",
                    evidence_summary=(
                        "Numeric range, existence, and freshness conditions "
                        "require a topic target."
                    ),
                    evidence_complete=False,
                )
            status = _status_value(metadata.connection_status)
            return expectation.condition.evaluate(
                ConditionEvaluationContext(
                    payload=status,
                    received_at=None,
                    evaluated_at=evaluated_at,
                )
            )

        if not isinstance(expectation.target, TopicTarget):
            return ConditionResult(
                status=HealthStatus.UNKNOWN,
                failure_code="UNSUPPORTED_EXPECTATION_TARGET",
                evidence_summary=(
                    f"Unsupported expectation target: "
                    f"{type(expectation.target).__name__}."
                ),
                evidence_complete=False,
            )
        if subscriptions is None or not any(
            mqtt_filter_matches(item.topic_filter, expectation.target.topic)
            for item in subscriptions
        ):
            return ConditionResult(
                status=HealthStatus.UNKNOWN,
                failure_code="SUBSCRIPTION_UNAVAILABLE",
                evidence_summary="Topic is not covered by an active subscription.",
                evidence_complete=False,
            )

        current = current_topics.get(expectation.target.topic)
        if current is None:
            if isinstance(expectation.condition, TOPIC_STATE_CONDITIONS):
                return expectation.condition.evaluate(
                    ConditionEvaluationContext(
                        payload=None,
                        received_at=None,
                        evaluated_at=evaluated_at,
                    )
                )
            return ConditionResult(
                status=HealthStatus.UNKNOWN,
                failure_code="TOPIC_NEVER_OBSERVED",
                evidence_summary="Topic has never been observed.",
                evidence_complete=False,
            )
        context = ConditionEvaluationContext(
            payload=current.message.payload,
            received_at=current.message.received_at,
            evaluated_at=evaluated_at,
        )
        if isinstance(expectation.condition, TOPIC_STATE_CONDITIONS):
            return expectation.condition.evaluate(context)
        age_seconds = max(
            0.0,
            (evaluated_at - _as_utc(current.message.received_at)).total_seconds(),
        )
        if age_seconds > stale_after_seconds:
            return ConditionResult(
                status=HealthStatus.UNKNOWN,
                failure_code="TOPIC_STALE",
                evidence_summary=(
                    f"Topic was last observed {age_seconds:.1f} seconds ago; "
                    f"the threshold is {stale_after_seconds:.1f} seconds."
                ),
                evidence_complete=False,
            )
        if current.message.is_truncated:
            return ConditionResult(
                status=HealthStatus.UNKNOWN,
                evidence_summary="Message payload was truncated.",
                evidence_complete=False,
            )
        return expectation.condition.evaluate(context)

    def _observation_health(
        self,
        metadata: ObserverRepoMetadata,
    ) -> ObservationHealth:
        findings: list[ObservationHealthFinding] = []
        if _status_value(metadata.connection_status) != ConnectionStatus.CONNECTED:
            findings.append(
                ObservationHealthFinding(
                    ObservationFindingCode.BROKER_DISCONNECTED,
                    HealthStatus.PROBLEM,
                    "Broker is not connected.",
                )
            )
        if metadata.observation_started_at is None:
            findings.append(
                ObservationHealthFinding(
                    ObservationFindingCode.OBSERVATION_NOT_STARTED,
                    HealthStatus.UNKNOWN,
                    "Observation has not started.",
                )
            )
        if metadata.dropped_message_count:
            findings.append(
                ObservationHealthFinding(
                    ObservationFindingCode.DROPPED_MESSAGES,
                    HealthStatus.PROBLEM,
                    f"{metadata.dropped_message_count} message(s) were dropped.",
                )
            )
        recording_failures = getattr(metadata, "recording_failure_count", 0)
        if recording_failures:
            findings.append(
                ObservationHealthFinding(
                    ObservationFindingCode.RECORDING_FAILURES,
                    HealthStatus.PROBLEM,
                    f"{recording_failures} message recording failure(s) occurred.",
                )
            )
        subscription_failures = getattr(metadata, "subscription_failure_count", 0)
        if subscription_failures:
            findings.append(
                ObservationHealthFinding(
                    ObservationFindingCode.SUBSCRIPTION_UNAVAILABLE,
                    HealthStatus.PROBLEM,
                    f"{subscription_failures} subscription operation(s) failed.",
                )
            )
        rejected = getattr(metadata, "subscription_rejected_count", 0)
        if rejected:
            findings.append(
                ObservationHealthFinding(
                    ObservationFindingCode.SUBSCRIPTION_REJECTED,
                    HealthStatus.PROBLEM,
                    f"The broker rejected {rejected} subscription(s).",
                )
            )
        return ObservationHealth(
            status=_aggregate_status(finding.status for finding in findings),
            findings=tuple(findings),
            evidence_complete=not findings,
        )

    def _read_subscriptions(
        self,
        broker_id: UUID,
    ) -> tuple[Subscription, ...] | None:
        if self._subscriptions_reader is None:
            return None
        try:
            return self._subscriptions_reader(broker_id)
        except Exception:
            logger.exception("Unable to read subscriptions for broker %s.", broker_id)
            return None

    def _require_broker_metadata_reader(self) -> BrokerMetadataReader:
        if self._broker_metadata_reader is None:
            raise RuntimeError("Broker health metadata reads are unavailable.")
        return self._broker_metadata_reader

    def _require_current_topics_reader(self) -> CurrentTopicsReader:
        if self._current_topics_reader is None:
            raise RuntimeError("Current topic reads are unavailable.")
        return self._current_topics_reader

    @staticmethod
    def _condition_result_to_evaluation(
        condition_result: ConditionResult,
        expectation: HealthExpectation,
        evaluated_at: datetime,
    ) -> ExpectationEvaluation:
        return ExpectationEvaluation(
            expectation_id=expectation.expectation_id,
            expectation_revision=expectation.revision,
            status=condition_result.status,
            evaluated_at=evaluated_at,
            failure_code=condition_result.failure_code,
            evidence_summary=condition_result.evidence_summary,
            evidence_complete=condition_result.evidence_complete,
        )

    def _update_failure(
        self,
        expectation: HealthExpectation,
        evaluation: ExpectationEvaluation,
        previous_state: ExpectationState | None,
        state: ExpectationState,
        transition: HealthTransition | None,
        *,
        transaction: object,
        count_occurrence: bool = True,
    ) -> ExpectationFailure | None:
        if transition is HealthTransition.NEW_FAILURE:
            if state.active_failure_id is None:
                raise RuntimeError("New failure transition has no failure ID.")
            return ExpectationFailure(
                failure_id=state.active_failure_id,
                expectation_id=evaluation.expectation_id,
                first_failed_at=evaluation.evaluated_at,
                last_seen_at=evaluation.evaluated_at,
                occurrence_count=1,
                expected_revision=evaluation.expectation_revision,
                last_healthy_at=state.last_healthy_at,
                failure_code=evaluation.failure_code,
                evidence_summary=evaluation.evidence_summary,
                snapshot_broker_id=_target_broker_id(expectation.target),
                snapshot_topic=_target_topic(expectation.target),
            )

        previous_failure_id = (
            None if previous_state is None else previous_state.active_failure_id
        )
        if previous_failure_id is None:
            return None
        failure = self._failure_repo.get(
            previous_failure_id,
            transaction=transaction,
        )
        if failure is None:
            raise RuntimeError(
                f"Active health failure {previous_failure_id} was not found."
            )
        if transition is HealthTransition.ONGOING_FAILURE:
            return replace(
                failure,
                last_seen_at=evaluation.evaluated_at,
                occurrence_count=failure.occurrence_count + int(count_occurrence),
                expected_revision=evaluation.expectation_revision,
                failure_code=evaluation.failure_code,
                evidence_summary=evaluation.evidence_summary,
            )
        if transition is HealthTransition.RECOVERY:
            return replace(
                failure,
                last_seen_at=evaluation.evaluated_at,
                last_healthy_at=evaluation.evaluated_at,
                recovered_at=evaluation.evaluated_at,
            )
        return None

    def _reset_changed_revision(
        self,
        previous_state: ExpectationState,
        changed_at: datetime,
        transaction: object,
    ) -> ExpectationState:
        """Close the old incident before evaluating the new rule revision."""
        if previous_state.active_failure_id is not None:
            failure = self._failure_repo.get(
                previous_state.active_failure_id,
                transaction=transaction,
            )
            if failure is not None and failure.recovered_at is None:
                self._failure_repo.upsert(
                    replace(failure, recovered_at=changed_at),
                    transaction=transaction,
                )
        return replace(
            previous_state,
            current_status=HealthStatus.UNKNOWN,
            active_failure_id=None,
        )

    def _is_observable(self, expectation: HealthExpectation, topic: str) -> bool:
        if not isinstance(expectation.target, TopicTarget):
            return isinstance(expectation.target, BrokerTarget)
        if self._subscriptions_reader is None:
            return True
        if expectation.target.topic != topic:
            return False
        return any(
            mqtt_filter_matches(subscription.topic_filter, topic)
            for subscription in self._subscriptions_reader(
                expectation.target.broker_id
            )
        )


def _target_broker_id(target) -> UUID | None:
    return getattr(target, "broker_id", None)


def _target_topic(target) -> str | None:
    return getattr(target, "topic", None)


def _aggregate_status(statuses) -> HealthStatus:
    values = tuple(statuses)
    if HealthStatus.PROBLEM in values:
        return HealthStatus.PROBLEM
    if HealthStatus.UNKNOWN in values:
        return HealthStatus.UNKNOWN
    return HealthStatus.HEALTHY


def _status_value(status: object) -> str:
    return str(getattr(status, "value", status))


def _validate_stale_after(value: float) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("stale_after_seconds must be a finite non-negative value.")
    return seconds


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
