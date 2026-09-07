from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from topicgate.app.services.expectation_management_service import (
    ExpectationManagementService,
)
from topicgate.app.services.health_expectation_service import HealthExpectationService
from topicgate.app.services.health_report_service import HealthReportService
from topicgate.core.models.health import (
    ActionKind,
    BrokerTarget,
    EqualCondition,
    ExpectationState,
    HealthExpectation,
    HealthSeverity,
    HealthStatus,
    TopicTarget,
    TopicExistsCondition,
)
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.topic_message import TopicMessage
from topicgate.infrastructure.database.database_context import DatabaseContext
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


NOW = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)


def _expectation(broker_id, *, condition=b"online"):
    return HealthExpectation(
        uuid4(),
        1,
        True,
        HealthSeverity.CRITICAL,
        TopicTarget(broker_id, "devices/status"),
        EqualCondition(condition),
        frozenset({ActionKind.STORE_FAILURE}),
    )


def _message(broker_id, payload=b"offline"):
    return TopicMessage(
        broker_id,
        "devices/status",
        payload,
        0,
        False,
        NOW,
        len(payload),
        1,
        uuid4(),
    )


def _components(tmp_path, subscriptions):
    database = DatabaseContext(f"sqlite:///{tmp_path / 'expectations.db'}")
    expectations = HealthExpectationRepository(database)
    states = ExpectationStateRepository(database)
    failures = ExpectationFailureRepository(database)
    reader = lambda _broker_id: subscriptions
    management = ExpectationManagementService(
        expectations,
        states,
        failures,
        database,
        reader,
    )
    pipeline = HealthExpectationService(
        expectations,
        states,
        failures,
        database,
        TransitionTracker(),
        ActionDispatcher(HealthActionRegistry({ActionKind.STORE_FAILURE: object()})),
        reader,
    )
    return database, expectations, states, failures, management, pipeline


def test_topic_only_condition_rejects_broker_target(tmp_path) -> None:
    components = _components(tmp_path, (Subscription("devices/#"),))
    database, _, _, _, management, _ = components
    broker_id = uuid4()
    item = replace(
        _expectation(broker_id),
        target=BrokerTarget(broker_id),
        condition=TopicExistsCondition(),
    )
    try:
        with pytest.raises(ValueError, match="require a topic target"):
            management.create_expectation(item, broker_id=broker_id)
    finally:
        database.dispose()


def test_metadata_and_enablement_changes_preserve_active_incident(tmp_path) -> None:
    broker_id = uuid4()
    components = _components(tmp_path, (Subscription("devices/#"),))
    database, _, states, failures, management, pipeline = components
    item = _expectation(broker_id)
    try:
        management.create_expectation(item, broker_id=broker_id)
        pipeline.evaluate_observation(_message(broker_id))
        active_id = states.get(item.expectation_id).active_failure_id

        edited = management.edit_expectation(
            item.expectation_id,
            broker_id=broker_id,
            name=" Device status ",
            description="Status must be online",
        )
        assert (edited.revision, edited.name, edited.description) == (
            1,
            "Device status",
            "Status must be online",
        )
        assert states.get(item.expectation_id).active_failure_id == active_id

        management.disable_expectation(item.expectation_id, broker_id=broker_id)
        assert states.get(item.expectation_id).active_failure_id == active_id
        management.enable_expectation(item.expectation_id, broker_id=broker_id)
        assert states.get(item.expectation_id).active_failure_id == active_id
    finally:
        database.dispose()


def test_condition_change_closes_old_revision_and_starts_fresh(tmp_path) -> None:
    broker_id = uuid4()
    database, _, states, failures, management, pipeline = _components(
        tmp_path, (Subscription("devices/#"),)
    )
    item = _expectation(broker_id)
    try:
        management.create_expectation(item, broker_id=broker_id)
        pipeline.evaluate_observation(_message(broker_id))
        old_failure_id = states.get(item.expectation_id).active_failure_id

        updated = management.edit_expectation(
            item.expectation_id,
            broker_id=broker_id,
            new_condition=EqualCondition(b"offline"),
        )
        old_failure = failures.get(old_failure_id)
        reset = states.get(item.expectation_id)
        assert updated.revision == 2
        assert old_failure.recovered_at is not None
        assert reset.current_status is HealthStatus.UNKNOWN
        assert reset.expectation_revision == 2
        assert reset.active_failure_id is None

        pipeline.evaluate_observation(_message(broker_id))
        assert states.get(item.expectation_id).current_status is HealthStatus.HEALTHY
        assert states.get(item.expectation_id).active_failure_id is None
    finally:
        database.dispose()


def test_transition_tracker_does_not_carry_failure_across_revisions() -> None:
    broker_id = uuid4()
    item = _expectation(broker_id)
    previous = ExpectationState(
        item.expectation_id,
        HealthStatus.PROBLEM,
        active_failure_id=uuid4(),
        expectation_revision=1,
    )
    evaluation = HealthExpectationService._condition_result_to_evaluation(
        replace(
            EqualCondition(b"online").handle_condition(b"offline"),
            status=HealthStatus.PROBLEM,
        ),
        replace(item, revision=2),
        NOW,
    )

    state, transition = TransitionTracker().apply(previous, evaluation)

    assert transition.value == "new_failure"
    assert state.expectation_revision == 2
    assert state.active_failure_id != previous.active_failure_id


def test_delete_retains_closed_failure_snapshot(tmp_path) -> None:
    broker_id = uuid4()
    database, expectations, states, failures, management, pipeline = _components(
        tmp_path, (Subscription("devices/#"),)
    )
    item = _expectation(broker_id)
    try:
        management.create_expectation(item, broker_id=broker_id)
        pipeline.evaluate_observation(_message(broker_id))
        failure_id = states.get(item.expectation_id).active_failure_id

        management.delete_expectation(item.expectation_id, broker_id=broker_id)

        assert expectations.get(item.expectation_id) is None
        retained = failures.get(failure_id)
        assert retained is not None
        assert retained.recovered_at is not None
        assert retained.snapshot_broker_id == broker_id
        assert retained.snapshot_topic == "devices/status"
    finally:
        database.dispose()


def test_topic_must_be_observable_when_created(tmp_path) -> None:
    broker_id = uuid4()
    database, _, _, _, management, _ = _components(
        tmp_path, (Subscription("other/#"),)
    )
    try:
        with pytest.raises(ValueError, match="not covered"):
            management.create_expectation(
                _expectation(broker_id), broker_id=broker_id
            )
    finally:
        database.dispose()


def test_unobservable_rule_is_reported_unknown_without_deleting_it(tmp_path) -> None:
    broker_id = uuid4()
    database, expectations, states, failures, management, pipeline = _components(
        tmp_path, (Subscription("devices/#"),)
    )
    item = _expectation(broker_id)
    try:
        management.create_expectation(item, broker_id=broker_id)
        pipeline.evaluate_observation(_message(broker_id))
        active_id = states.get(item.expectation_id).active_failure_id

        report = HealthReportService(
            expectations,
            states,
            failures,
            lambda _broker_id: (),
        )
        state = report.get_expectation_states(broker_id)[0]
        assert state.current_status is HealthStatus.UNKNOWN
        assert state.active_failure_id == active_id
        assert expectations.get(item.expectation_id) is not None
    finally:
        database.dispose()
