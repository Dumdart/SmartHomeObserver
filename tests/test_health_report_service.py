from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from topicgate.app.services.health_report_service import HealthReportService
from topicgate.core.models.health import (
    BrokerTarget,
    ExpectationFailure,
    ExpectationState,
    HealthExpectation,
    HealthSeverity,
    HealthStatus,
    TopicTarget,
)


def _expectation(target) -> HealthExpectation:
    return HealthExpectation(
        uuid4(), 1, True, HealthSeverity.CRITICAL, target, MagicMock(), frozenset()
    )


def _failure(expectation_id, *, recovered_at=None, evidence_summary="evidence"):
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return ExpectationFailure(
        uuid4(), expectation_id, timestamp, timestamp, recovered_at=recovered_at,
        evidence_summary=evidence_summary,
    )


def _service(expectations, states, failures) -> HealthReportService:
    expectation_repository = MagicMock()
    expectation_repository.get.side_effect = expectations.get
    state_repository = MagicMock()
    state_repository.get_all_states.return_value = states
    failure_repository = MagicMock()
    failure_repository.get_all_states.return_value = failures
    return HealthReportService(
        expectation_repository, state_repository, failure_repository
    )


def test_report_contains_current_state_summary_and_active_failures() -> None:
    evaluated = datetime(2026, 1, 3, tzinfo=timezone.utc)
    healthy = datetime(2026, 1, 4, tzinfo=timezone.utc)
    item = _expectation(TopicTarget(uuid4(), "devices/status"))
    active = _failure(item.expectation_id)
    recovered = _failure(item.expectation_id, recovered_at=healthy)
    service = _service(
        {item.expectation_id: item},
        [ExpectationState(uuid4(), HealthStatus.HEALTHY, evaluated, healthy)],
        [active, recovered],
    )

    assert service.get_last_evaluated() == evaluated
    assert service.get_last_healthy() == healthy
    assert service.get_active_failures() == [active]


@pytest.mark.parametrize(
    ("target_factory", "target_identity"),
    [
        (lambda broker_id: TopicTarget(broker_id, "devices/status"), "devices/status"),
        (lambda broker_id: BrokerTarget(broker_id), "broker"),
    ],
)
def test_report_resolves_broker_and_target_identity(
    target_factory, target_identity
) -> None:
    broker_id = uuid4()
    item = _expectation(target_factory(broker_id))
    failure = _failure(item.expectation_id)
    service = _service({item.expectation_id: item}, [], [failure])

    assert service.broker_identity(failure) == broker_id
    assert service.target_identity(failure) == target_identity


def test_report_exposes_evidence_limitations() -> None:
    item = _expectation(BrokerTarget(uuid4()))
    unavailable = _failure(item.expectation_id, evidence_summary=None)
    available = _failure(item.expectation_id)
    service = _service({item.expectation_id: item}, [], [unavailable, available])

    assert service.get_evidence_limitations(unavailable) == ("evidence_unavailable",)
    assert service.get_evidence_limitations(available) == ()


def test_report_prefers_failure_identity_snapshot_after_expectation_edit() -> None:
    original_broker_id = uuid4()
    item = _expectation(TopicTarget(uuid4(), "new/status"))
    failure = _failure(item.expectation_id)
    failure.snapshot_broker_id = original_broker_id
    failure.snapshot_topic = "old/status"
    service = _service({item.expectation_id: item}, [], [failure])

    assert service.broker_identity(failure) == original_broker_id
    assert service.target_identity(failure) == "old/status"
