from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from topicgate.app.services.health_expectation_service import (
    HealthExpectationService,
)
from topicgate.core.models.health import ActionKind
from topicgate.core.models.health import Condition
from topicgate.core.models.health import ConditionResult
from topicgate.core.models.health import EqualCondition
from topicgate.core.models.health import ExpectationEvaluation
from topicgate.core.models.health import ExpectationState
from topicgate.core.models.health import HealthExpectation
from topicgate.core.models.health import HealthSeverity
from topicgate.core.models.health import HealthStatus
from topicgate.core.models.health import HealthTransition
from topicgate.core.models.health import TopicTarget
from topicgate.core.models.topic_message import TopicMessage
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.models import ExpectationFailureRow
from topicgate.infrastructure.repository.expectation_failure_repository import (
    ExpectationFailureRepository,
)
from topicgate.infrastructure.repository.expectation_state_repository import (
    ExpectationStateRepository,
)
from topicgate.infrastructure.repository.health_expectation_repository import (
    HealthExpectationRepository,
)
from topicgate.processors.action_dispatcher import ActionDispatcher
from topicgate.processors.health_action_registry import HealthActionRegistry
from topicgate.processors.transition_tracker import TransitionTracker


NOW = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)


class FailingCondition(Condition):
    def handle_condition(self, actual: bytes | str) -> ConditionResult:
        raise ValueError("invalid observation")


class RecordingCondition(Condition):
    def __init__(self) -> None:
        self.values: list[bytes | str] = []

    def handle_condition(self, actual: bytes | str) -> ConditionResult:
        self.values.append(actual)
        return ConditionResult(status=HealthStatus.HEALTHY)


class StaticExpectationReader:
    def __init__(self, expectations) -> None:
        self._expectations = expectations

    def list_for_topic(self, broker_id, topic):
        return self._expectations


def expectation(broker_id, *, enabled=True, actions=frozenset()):
    return HealthExpectation(
        expectation_id=uuid4(),
        revision=2,
        enabled=enabled,
        severity=HealthSeverity.CRITICAL,
        target=TopicTarget(broker_id, "devices/status"),
        condition=EqualCondition(b"online"),
        actions=actions,
    )


def message(
    broker_id,
    payload=b"offline",
    *,
    received_at=NOW,
    is_truncated=False,
):
    return TopicMessage(
        broker_id=broker_id,
        topic="devices/status",
        payload=payload,
        qos=0,
        retain=False,
        received_at=received_at,
        payload_size=len(payload),
        message_count=1,
        observation_id=uuid4(),
        is_truncated=is_truncated,
    )


def evaluation(
    item: HealthExpectation,
    status: HealthStatus,
    *,
    evaluated_at: datetime = NOW,
) -> ExpectationEvaluation:
    return ExpectationEvaluation(
        expectation_id=item.expectation_id,
        expectation_revision=item.revision,
        status=status,
        evaluated_at=evaluated_at,
        failure_code=None,
        evidence_summary=None,
        evidence_complete=True,
    )


def service(database, actions=None):
    expectations = HealthExpectationRepository(database)
    states = ExpectationStateRepository(database)
    failures = ExpectationFailureRepository(database)
    dispatcher = ActionDispatcher(HealthActionRegistry(actions or {}))
    return (
        HealthExpectationService(
            expectations,
            states,
            failures,
            database,
            TransitionTracker(),
            dispatcher,
        ),
        expectations,
        states,
        failures,
    )


@pytest.mark.parametrize(
    ("previous_status", "has_active_failure", "status", "transition", "retains_failure"),
    [
        (None, False, HealthStatus.UNKNOWN, None, False),
        (None, False, HealthStatus.HEALTHY, None, False),
        (None, False, HealthStatus.PROBLEM, HealthTransition.NEW_FAILURE, True),
        (HealthStatus.UNKNOWN, False, HealthStatus.UNKNOWN, None, False),
        (HealthStatus.UNKNOWN, False, HealthStatus.HEALTHY, None, False),
        (HealthStatus.UNKNOWN, False, HealthStatus.PROBLEM, HealthTransition.NEW_FAILURE, True),
        (HealthStatus.HEALTHY, False, HealthStatus.UNKNOWN, None, False),
        (HealthStatus.HEALTHY, False, HealthStatus.HEALTHY, None, False),
        (HealthStatus.HEALTHY, False, HealthStatus.PROBLEM, HealthTransition.NEW_FAILURE, True),
        (HealthStatus.PROBLEM, True, HealthStatus.UNKNOWN, None, True),
        (HealthStatus.PROBLEM, True, HealthStatus.HEALTHY, HealthTransition.RECOVERY, False),
        (HealthStatus.PROBLEM, True, HealthStatus.PROBLEM, HealthTransition.ONGOING_FAILURE, True),
        (HealthStatus.UNKNOWN, True, HealthStatus.UNKNOWN, None, True),
        (HealthStatus.UNKNOWN, True, HealthStatus.HEALTHY, HealthTransition.RECOVERY, False),
        (HealthStatus.UNKNOWN, True, HealthStatus.PROBLEM, HealthTransition.ONGOING_FAILURE, True),
    ],
)
def test_transition_tracker_matrix(
    previous_status,
    has_active_failure,
    status,
    transition,
    retains_failure,
) -> None:
    broker_id = uuid4()
    item = expectation(broker_id)
    failure_id = uuid4()
    previous = (
        None
        if previous_status is None
        else ExpectationState(
            item.expectation_id,
            previous_status,
            last_healthy_at=NOW - timedelta(minutes=1),
            active_failure_id=(
                failure_id if has_active_failure else None
            ),
        )
    )

    state, actual_transition = TransitionTracker().apply(
        previous,
        evaluation(item, status),
    )

    assert actual_transition is transition
    assert state.current_status is status
    assert state.last_evaluated_at == NOW
    assert (state.active_failure_id is not None) is retains_failure
    if has_active_failure and retains_failure:
        assert state.active_failure_id == failure_id


def test_unknown_observation_retains_and_resumes_unresolved_failure() -> None:
    broker_id = uuid4()
    item = expectation(broker_id)
    failure_id = uuid4()
    unknown_state = ExpectationState(
        item.expectation_id,
        HealthStatus.UNKNOWN,
        active_failure_id=failure_id,
    )

    state, transition = TransitionTracker().apply(
        unknown_state,
        evaluation(item, HealthStatus.PROBLEM),
    )

    assert transition is HealthTransition.ONGOING_FAILURE
    assert state.active_failure_id == failure_id


def test_truncated_observation_is_unknown_without_invoking_condition_or_actions(
    tmp_path,
) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'truncated.db'}")
    condition = RecordingCondition()
    broker_id = uuid4()
    item = replace(expectation(broker_id), condition=condition)
    handler = MagicMock()
    states = MagicMock()
    states.get.return_value = None
    failures = MagicMock()
    pipeline = HealthExpectationService(
        StaticExpectationReader((item,)),
        states,
        failures,
        database,
        TransitionTracker(),
        ActionDispatcher(HealthActionRegistry({ActionKind.LOG: handler})),
    )

    evaluations = pipeline.evaluate_observation(
        message(broker_id, b"healthy-but-incomplete", is_truncated=True)
    )

    assert len(evaluations) == 1
    assert evaluations[0].status is HealthStatus.UNKNOWN
    assert evaluations[0].failure_code is None
    assert evaluations[0].evidence_complete is False
    assert evaluations[0].evidence_summary == "Message payload was truncated."
    assert condition.values == []
    stored_state = states.upsert.call_args.args[0]
    assert stored_state.current_status is HealthStatus.UNKNOWN
    handler.execute.assert_not_called()
    database.dispose()


def test_truncated_observation_does_not_recover_an_existing_failure(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'truncated-recovery.db'}")
    handler = MagicMock()
    pipeline, expectations, states, failures = service(
        database,
        {ActionKind.LOG: handler},
    )
    broker_id = uuid4()
    item = expectation(broker_id, actions=frozenset({ActionKind.LOG}))
    expectations.create(item)

    pipeline.evaluate_observation(message(broker_id, b"offline"))
    failed_state = states.get(item.expectation_id)
    assert failed_state is not None
    failure_id = failed_state.active_failure_id
    assert failure_id is not None

    evaluations = pipeline.evaluate_observation(
        message(broker_id, b"online", is_truncated=True)
    )

    state = states.get(item.expectation_id)
    failure = failures.get(failure_id)
    assert evaluations[0].status is HealthStatus.UNKNOWN
    assert state.current_status is HealthStatus.UNKNOWN
    assert state.active_failure_id == failure_id
    assert failure.recovered_at is None
    assert failure.occurrence_count == 1
    assert handler.execute.call_count == 1
    database.dispose()


def test_pipeline_creates_repeats_and_recovers_one_failure_episode(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'pipeline.db'}")
    handler = MagicMock()
    pipeline, expectations, states, failures = service(
        database,
        {ActionKind.LOG: handler},
    )
    broker_id = uuid4()
    item = expectation(broker_id, actions=frozenset({ActionKind.LOG}))
    expectations.create(item)

    evaluations = pipeline.evaluate_observation(message(broker_id))
    failed_state = states.get(item.expectation_id)
    assert failed_state is not None
    assert failed_state.active_failure_id is not None
    failure_id = failed_state.active_failure_id
    failure = failures.get(failure_id)
    assert failure is not None
    assert failure.occurrence_count == 1
    assert failure.failure_code == "EQUAL_CONDITION_FAILED"
    assert failure.evidence_summary == (
        "Expected value: b'online', Actual value: b'offline'"
    )
    assert len(evaluations) == 1
    finding = evaluations[0]
    assert finding.expectation_id == item.expectation_id
    assert finding.expectation_revision == item.revision
    assert finding.status is HealthStatus.PROBLEM
    assert finding.evaluated_at == NOW
    assert finding.failure_code == "EQUAL_CONDITION_FAILED"
    assert finding.evidence_complete is True
    assert handler.execute.call_count == 1
    first_context = handler.execute.call_args.args[0]
    assert first_context.evaluation is finding
    assert first_context.transition is HealthTransition.NEW_FAILURE
    assert first_context.severity is HealthSeverity.CRITICAL

    pipeline.evaluate_observation(
        message(broker_id, received_at=NOW + timedelta(minutes=1))
    )
    assert failures.get(failure_id).occurrence_count == 2
    assert handler.execute.call_count == 1

    pipeline.evaluate_observation(
        message(
            broker_id,
            b"online",
            received_at=NOW + timedelta(minutes=2),
        )
    )
    recovered_state = states.get(item.expectation_id)
    recovered = failures.get(failure_id)
    assert recovered_state.current_status is HealthStatus.HEALTHY
    assert recovered_state.active_failure_id is None
    assert recovered.recovered_at is not None
    assert handler.execute.call_count == 2
    assert handler.execute.call_args.args[0].transition is HealthTransition.RECOVERY
    database.dispose()


def test_restart_with_active_failure_does_not_dispatch_actions_again(tmp_path) -> None:
    database_path = tmp_path / "restart.db"
    url = f"sqlite:///{database_path}"
    first_database = DatabaseContext(url)
    first_handler = MagicMock()
    first_pipeline, first_expectations, first_states, _ = service(
        first_database,
        {ActionKind.LOG: first_handler},
    )
    broker_id = uuid4()
    item = expectation(broker_id, actions=frozenset({ActionKind.LOG}))
    first_expectations.create(item)

    first_pipeline.evaluate_observation(message(broker_id))
    first_failure_id = first_states.get(item.expectation_id).active_failure_id
    assert first_failure_id is not None
    assert first_handler.execute.call_count == 1
    first_database.dispose()

    second_database = DatabaseContext(url)
    try:
        second_handler = MagicMock()
        second_pipeline, _, second_states, second_failures = service(
            second_database,
            {ActionKind.LOG: second_handler},
        )

        second_pipeline.evaluate_observation(message(broker_id))

        state = second_states.get(item.expectation_id)
        assert state.active_failure_id == first_failure_id
        assert second_failures.get(first_failure_id).occurrence_count == 2
        second_handler.execute.assert_not_called()
    finally:
        second_database.dispose()


def test_pipeline_no_expectations_and_disabled_expectations_are_safe_noops(
    tmp_path,
) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'noop.db'}")
    pipeline, expectations, states, _ = service(database)
    broker_id = uuid4()

    pipeline.evaluate_observation(message(broker_id))
    disabled = expectation(broker_id, enabled=False)
    expectations.create(disabled)
    pipeline.evaluate_observation(message(broker_id))

    assert states.get(disabled.expectation_id) is None
    database.dispose()


def test_evaluator_failure_does_not_block_other_expectations(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'evaluation.db'}")
    _, expectations, states, failures = service(database)
    broker_id = uuid4()
    broken_row = expectation(broker_id)
    healthy = expectation(broker_id)
    expectations.create(broken_row)
    expectations.create(healthy)
    reader = StaticExpectationReader(
        (replace(broken_row, condition=FailingCondition()), healthy)
    )
    pipeline = HealthExpectationService(
        reader,
        states,
        failures,
        database,
        TransitionTracker(),
        ActionDispatcher(HealthActionRegistry({})),
    )

    pipeline.evaluate_observation(message(broker_id))

    assert states.get(broken_row.expectation_id) is None
    assert states.get(healthy.expectation_id).current_status is HealthStatus.PROBLEM
    database.dispose()


def test_pipeline_isolates_missing_and_failing_action_handlers(
    tmp_path,
    caplog,
) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'actions.db'}")
    failing = MagicMock()
    failing.execute.side_effect = RuntimeError("handler failed")
    pipeline, expectations, states, _ = service(
        database,
        {ActionKind.LOG: failing},
    )
    broker_id = uuid4()
    item = expectation(
        broker_id,
        actions=frozenset({ActionKind.LOG, ActionKind.STORE_FAILURE}),
    )
    expectations.create(item)

    pipeline.evaluate_observation(message(broker_id))

    assert states.get(item.expectation_id).current_status is HealthStatus.PROBLEM
    assert "No health action handler is registered for store_failure" in caplog.text
    assert "Health action log failed" in caplog.text
    database.dispose()


def test_pipeline_rolls_back_failure_when_state_write_fails(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'rollback.db'}")
    pipeline, expectations, states, failures = service(database)
    broker_id = uuid4()
    item = expectation(broker_id)
    expectations.create(item)
    original_upsert = states.upsert
    states.upsert = MagicMock(side_effect=RuntimeError("state write failed"))

    pipeline.evaluate_observation(message(broker_id))

    states.upsert = original_upsert
    assert states.get(item.expectation_id) is None
    with database.session() as session:
        failure_count = session.scalar(
            select(func.count()).select_from(ExpectationFailureRow)
        )
        assert failure_count == 0
    database.dispose()
