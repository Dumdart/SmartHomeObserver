import asyncio
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from topicgate.app.services.broker_health_monitor import BrokerHealthMonitor
from topicgate.app.services.health_expectation_service import (
    HealthExpectationService,
)
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.health import BrokerTarget
from topicgate.core.models.health import EqualCondition
from topicgate.core.models.health import FreshnessCondition
from topicgate.core.models.health import HealthExpectation
from topicgate.core.models.health import HealthSeverity
from topicgate.core.models.health import HealthStatus
from topicgate.core.models.health import ObservationFindingCode
from topicgate.core.models.health import NumericRangeCondition
from topicgate.core.models.health import TopicAbsentCondition
from topicgate.core.models.health import TopicExistsCondition
from topicgate.core.models.health import TopicTarget
from topicgate.core.models.observation_status import ObservationStatus
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


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _expectation(broker_id) -> HealthExpectation:
    return HealthExpectation(
        expectation_id=uuid4(),
        revision=1,
        enabled=True,
        severity=HealthSeverity.CRITICAL,
        target=TopicTarget(broker_id, "devices/status"),
        condition=EqualCondition(b"online"),
        actions=frozenset(),
    )


def _metadata(**overrides):
    values = {
        "connection_status": ConnectionStatus.CONNECTED,
        "observation_started_at": NOW - timedelta(minutes=10),
        "dropped_message_count": 0,
        "recording_failure_count": 0,
        "subscription_failure_count": 0,
        "subscription_rejected_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _service(database, item, metadata, current_topics=(), subscriptions=None):
    expectations = HealthExpectationRepository(database)
    expectations.create(item)
    return HealthExpectationService(
        expectations,
        ExpectationStateRepository(database),
        ExpectationFailureRepository(database),
        database,
        TransitionTracker(),
        ActionDispatcher(HealthActionRegistry({})),
        subscriptions_reader=lambda _broker_id: (
            (Subscription("devices/#"),)
            if subscriptions is None
            else subscriptions
        ),
        broker_metadata_reader=lambda _broker_id: metadata,
        current_topics_reader=lambda _broker_id: current_topics,
    )


def test_broker_evaluation_reports_a_topic_that_was_never_observed(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'missing.db'}")
    broker_id = uuid4()
    item = _expectation(broker_id)
    evaluator = _service(database, item, _metadata())

    report = evaluator.evaluate_broker(broker_id, evaluated_at=NOW)

    assert report.observation_health.status is HealthStatus.HEALTHY
    assert report.observation_health.findings == ()
    assert report.topic_findings[0].failure_code == "TOPIC_NEVER_OBSERVED"
    assert report.topic_findings[0].status is HealthStatus.UNKNOWN
    assert report.aggregate_status is HealthStatus.UNKNOWN
    assert report.evidence_complete is False
    database.dispose()


def test_broker_evaluation_does_not_treat_a_stale_value_as_condition_evidence(
    tmp_path,
) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'stale.db'}")
    broker_id = uuid4()
    item = _expectation(broker_id)
    stale_message = TopicMessage(
        broker_id=broker_id,
        topic="devices/status",
        payload=b"offline",
        qos=0,
        retain=False,
        received_at=NOW - timedelta(seconds=61),
        payload_size=7,
        message_count=1,
        observation_id=uuid4(),
    )
    evaluator = _service(
        database,
        item,
        _metadata(),
        (CurrentTopic(stale_message, ObservationStatus.LIVE),),
    )

    report = evaluator.evaluate_broker(
        broker_id,
        stale_after_seconds=60,
        evaluated_at=NOW,
    )

    assert report.topic_findings[0].failure_code == "TOPIC_STALE"
    assert report.topic_findings[0].status is HealthStatus.UNKNOWN
    assert report.topic_findings[0].evidence_complete is False
    database.dispose()


def test_missing_topic_is_evaluated_by_topic_state_conditions(tmp_path) -> None:
    cases = (
        (TopicExistsCondition(), HealthStatus.PROBLEM, "TOPIC_EXPECTED_PRESENT"),
        (TopicAbsentCondition(), HealthStatus.HEALTHY, None),
        (FreshnessCondition(60), HealthStatus.PROBLEM, "FRESHNESS_CONDITION_FAILED"),
    )

    for index, (condition, status, failure_code) in enumerate(cases):
        database = DatabaseContext(f"sqlite:///{tmp_path / f'missing-{index}.db'}")
        broker_id = uuid4()
        item = replace(_expectation(broker_id), condition=condition)
        evaluator = _service(database, item, _metadata())

        finding = evaluator.evaluate_broker(
            broker_id,
            evaluated_at=NOW,
        ).topic_findings[0]

        assert finding.status is status
        assert finding.failure_code == failure_code
        database.dispose()


def test_cached_topic_counts_for_existence_and_uses_recorded_freshness(
    tmp_path,
) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'cached-freshness.db'}")
    broker_id = uuid4()
    item = replace(_expectation(broker_id), condition=FreshnessCondition(60))
    message = TopicMessage(
        broker_id=broker_id,
        topic="devices/status",
        payload=b"",
        qos=0,
        retain=False,
        received_at=NOW - timedelta(seconds=30),
        payload_size=0,
        message_count=1,
        observation_id=uuid4(),
    )
    evaluator = _service(
        database,
        item,
        _metadata(),
        (CurrentTopic(message, ObservationStatus.CACHED),),
    )

    finding = evaluator.evaluate_broker(
        broker_id,
        stale_after_seconds=10,
        evaluated_at=NOW,
    ).topic_findings[0]

    assert finding.status is HealthStatus.HEALTHY
    assert "30.0 seconds ago" in finding.evidence_summary
    database.dispose()


def test_topic_state_conditions_ignore_truncated_payloads(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'truncated-exists.db'}")
    broker_id = uuid4()
    item = replace(_expectation(broker_id), condition=TopicExistsCondition())
    evaluator = _service(database, item, _metadata())
    message = TopicMessage(
        broker_id=broker_id,
        topic="devices/status",
        payload=b"partial",
        qos=0,
        retain=False,
        received_at=NOW,
        payload_size=100,
        message_count=1,
        observation_id=uuid4(),
        is_truncated=True,
    )

    finding = evaluator.evaluate_observation(message)[0]

    assert finding.status is HealthStatus.HEALTHY
    assert finding.evidence_complete is True
    database.dispose()


def test_numeric_range_keeps_generic_stale_value_guard(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'stale-number.db'}")
    broker_id = uuid4()
    item = replace(
        _expectation(broker_id),
        condition=NumericRangeCondition(Decimal("1"), Decimal("3")),
    )
    message = TopicMessage(
        broker_id=broker_id,
        topic="devices/status",
        payload=b"2",
        qos=0,
        retain=False,
        received_at=NOW - timedelta(seconds=61),
        payload_size=1,
        message_count=1,
        observation_id=uuid4(),
    )
    evaluator = _service(
        database,
        item,
        _metadata(),
        (CurrentTopic(message, ObservationStatus.LIVE),),
    )

    finding = evaluator.evaluate_broker(
        broker_id,
        stale_after_seconds=60,
        evaluated_at=NOW,
    ).topic_findings[0]

    assert finding.status is HealthStatus.UNKNOWN
    assert finding.failure_code == "TOPIC_STALE"
    database.dispose()


def test_unsubscribed_topic_state_condition_remains_unknown(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'unsubscribed-exists.db'}")
    broker_id = uuid4()
    item = replace(_expectation(broker_id), condition=TopicExistsCondition())
    evaluator = _service(
        database,
        item,
        _metadata(),
        subscriptions=(),
    )

    finding = evaluator.evaluate_broker(
        broker_id,
        evaluated_at=NOW,
    ).topic_findings[0]

    assert finding.status is HealthStatus.UNKNOWN
    assert finding.failure_code == "SUBSCRIPTION_UNAVAILABLE"
    database.dispose()


def test_observation_failures_are_separate_from_topic_findings(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'transport.db'}")
    broker_id = uuid4()
    item = _expectation(broker_id)
    evaluator = _service(
        database,
        item,
        _metadata(
            connection_status=ConnectionStatus.DISCONNECTED,
            observation_started_at=None,
            dropped_message_count=2,
            recording_failure_count=1,
            subscription_rejected_count=1,
        ),
        subscriptions=(),
    )

    report = evaluator.evaluate_broker(broker_id, evaluated_at=NOW)

    codes = {finding.code for finding in report.observation_health.findings}
    assert codes == {
        ObservationFindingCode.BROKER_DISCONNECTED,
        ObservationFindingCode.OBSERVATION_NOT_STARTED,
        ObservationFindingCode.DROPPED_MESSAGES,
        ObservationFindingCode.RECORDING_FAILURES,
        ObservationFindingCode.SUBSCRIPTION_UNAVAILABLE,
        ObservationFindingCode.SUBSCRIPTION_REJECTED,
    }
    assert report.observation_health.status is HealthStatus.PROBLEM
    assert report.topic_findings[0].failure_code == "SUBSCRIPTION_UNAVAILABLE"
    assert report.aggregate_status is HealthStatus.PROBLEM
    assert report.evidence_complete is False
    database.dispose()


def test_broker_target_is_evaluated_without_a_topic_message(tmp_path) -> None:
    database = DatabaseContext(f"sqlite:///{tmp_path / 'broker-target.db'}")
    broker_id = uuid4()
    item = HealthExpectation(
        expectation_id=uuid4(),
        revision=1,
        enabled=True,
        severity=HealthSeverity.CRITICAL,
        target=BrokerTarget(broker_id),
        condition=EqualCondition("connected"),
        actions=frozenset(),
    )
    evaluator = _service(database, item, _metadata())

    report = evaluator.evaluate_broker(broker_id, evaluated_at=NOW)

    assert report.topic_findings[0].status is HealthStatus.HEALTHY
    assert report.aggregate_status is HealthStatus.HEALTHY
    assert report.evidence_complete is True
    database.dispose()


async def test_health_monitor_evaluates_on_its_lifecycle_schedule() -> None:
    broker_id = uuid4()
    report = MagicMock()
    evaluator = MagicMock()
    evaluator.evaluate_broker.return_value = report
    monitor = BrokerHealthMonitor(
        evaluator,
        lambda: (broker_id,),
        interval_seconds=0.01,
    )

    await monitor.start()
    for _ in range(10):
        if evaluator.evaluate_broker.called:
            break
        await asyncio.sleep(0.005)
    await monitor.stop()

    evaluator.evaluate_broker.assert_called_with(
        broker_id,
        stale_after_seconds=300.0,
    )
    assert monitor.latest_reports == {broker_id: report}
