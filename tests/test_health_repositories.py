from datetime import datetime, timezone
from uuid import uuid4

from topicgate.core.models.health import ActionKind
from topicgate.core.models.health import EqualCondition
from topicgate.core.models.health import ExpectationFailure
from topicgate.core.models.health import ExpectationState
from topicgate.core.models.health import HealthExpectation
from topicgate.core.models.health import HealthSeverity
from topicgate.core.models.health import HealthStatus
from topicgate.core.models.health import TopicTarget
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


def test_health_repositories_scope_topics_and_hydrate_after_restart(tmp_path) -> None:
    path = tmp_path / "health.db"
    url = f"sqlite:///{path}"
    database = DatabaseContext(url)
    expectations = HealthExpectationRepository(database)
    broker_id = uuid4()
    other_broker_id = uuid4()
    first = _expectation(broker_id)
    second = _expectation(broker_id)
    other = _expectation(other_broker_id)
    for item in (first, second, other):
        expectations.create(item)
    updated_first = HealthExpectation(
        expectation_id=first.expectation_id,
        revision=2,
        enabled=False,
        severity=first.severity,
        target=first.target,
        condition=first.condition,
        actions=first.actions,
    )
    expectations.upsert(updated_first)
    failure_id = uuid4()
    timestamp = datetime.now(timezone.utc)
    failures = ExpectationFailureRepository(database)
    states = ExpectationStateRepository(database)
    failures.create(
        ExpectationFailure(
            failure_id,
            first.expectation_id,
            timestamp,
            timestamp,
            occurrence_count=1,
        )
    )
    states.create(
        ExpectationState(
            first.expectation_id,
            HealthStatus.PROBLEM,
            active_failure_id=failure_id,
        )
    )
    database.dispose()

    reopened = DatabaseContext(url)
    reopened_expectations = HealthExpectationRepository(reopened)
    matches = reopened_expectations.list_for_topic(
        broker_id,
        "devices/status",
    )
    assert {item.expectation_id for item in matches} == {
        first.expectation_id,
        second.expectation_id,
    }
    assert reopened_expectations.get(first.expectation_id) == updated_first
    assert reopened_expectations.list_for_topic(
        uuid4(),
        "devices/status",
    ) == ()
    assert ExpectationStateRepository(reopened).get(first.expectation_id) is not None
    assert ExpectationFailureRepository(reopened).get(failure_id) is not None

    ExpectationFailureRepository(reopened).delete(failure_id)
    assert ExpectationFailureRepository(reopened).get(failure_id) is None
    assert (
        ExpectationStateRepository(reopened).get(first.expectation_id).active_failure_id
        is None
    )

    reopened_expectations.delete(first.expectation_id)
    assert ExpectationStateRepository(reopened).get(first.expectation_id) is None
    reopened.dispose()


def _expectation(broker_id) -> HealthExpectation:
    return HealthExpectation(
        expectation_id=uuid4(),
        revision=1,
        enabled=True,
        severity=HealthSeverity.CRITICAL,
        target=TopicTarget(broker_id, "devices/status"),
        condition=EqualCondition(b"online"),
        actions=frozenset({ActionKind.LOG}),
    )
