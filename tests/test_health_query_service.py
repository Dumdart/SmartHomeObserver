from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from topicgate.app.services.health_query_service import HealthQueryService
from topicgate.core.models.health import (
    DiagnosticReport,
    ExpectationEvaluation,
    ExpectationFailure,
    HealthExpectation,
    HealthSeverity,
    HealthStatus,
    ObservationHealth,
    TopicTarget,
)


NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)


def _expectation(broker_id, name="Rule"):
    return HealthExpectation(
        uuid4(),
        1,
        True,
        HealthSeverity.CRITICAL,
        TopicTarget(broker_id, "devices/status"),
        MagicMock(),
        frozenset(),
        name,
    )


def _service(expectations, evaluations=(), failures=()):
    evaluator = MagicMock()
    evaluator.evaluate_broker.return_value = DiagnosticReport(
        broker_id=expectations[0].target.broker_id if expectations else uuid4(),
        evaluated_at=NOW,
        observation_health=ObservationHealth(
            HealthStatus.HEALTHY, (), True
        ),
        topic_findings=tuple(evaluations),
        aggregate_status=HealthStatus.HEALTHY,
        evidence_complete=True,
    )
    management = MagicMock()
    management.list_expectations.return_value = tuple(expectations)
    history = MagicMock()
    history.filter.return_value = tuple(failures)
    history.get_cursor_pagination.side_effect = (
        lambda cursor, limit, items: SimpleNamespace(
            items=items[(cursor or 0) : (cursor or 0) + limit],
            next_cursor=(
                (cursor or 0) + limit
                if (cursor or 0) + limit < len(items)
                else None
            ),
        )
    )
    report = MagicMock()
    report.get_active_failures.return_value = []
    return HealthQueryService(evaluator, management, history, report), report


def test_health_report_orders_bounds_and_truncates_evidence() -> None:
    broker_id = uuid4()
    first = _expectation(broker_id, "Healthy")
    second = _expectation(broker_id, "Problem")
    evaluations = (
        ExpectationEvaluation(
            first.expectation_id,
            1,
            HealthStatus.HEALTHY,
            NOW,
            None,
            "healthy",
            True,
        ),
        ExpectationEvaluation(
            second.expectation_id,
            1,
            HealthStatus.PROBLEM,
            NOW,
            "MISMATCH",
            "x" * 501,
            True,
        ),
    )
    service, _ = _service([first, second], evaluations)

    result = service.get_health_report(broker_id, limit=1)

    assert result.expectation_findings[0].name == "Problem"
    assert len(result.expectation_findings[0].evidence_summary) == 500
    assert result.expectation_findings[0].evidence_truncated is True
    assert (result.returned_count, result.omitted_count) == (1, 1)
    assert len(result.checkpoint.entries) == 2
    assert result.checkpoint.omitted_count == 0
    assert result.delta is None


def test_checkpoint_and_delta_do_not_depend_on_display_limit() -> None:
    broker_id = uuid4()
    first = _expectation(broker_id, "First")
    second = _expectation(broker_id, "Second")
    evaluations = tuple(
        ExpectationEvaluation(
            item.expectation_id,
            1,
            HealthStatus.PROBLEM,
            NOW,
            "MISMATCH",
            item.name,
            True,
        )
        for item in (first, second)
    )
    service, _ = _service([first, second], evaluations)

    initial = service.get_health_report(broker_id, limit=1)
    repeated = service.get_health_report(
        broker_id,
        limit=2,
        checkpoint=initial.checkpoint,
    )

    assert initial.checkpoint == repeated.checkpoint
    assert [event.kind for event in repeated.delta.events] == [
        "continuing",
        "continuing",
    ]


def test_finding_fingerprint_excludes_mutable_report_fields() -> None:
    broker_id = uuid4()
    profile_id = uuid4()
    expectation = replace(
        _expectation(broker_id),
        profile_id=profile_id,
        rule_id="Device.Online",
    )
    evaluation = ExpectationEvaluation(
        expectation.expectation_id,
        1,
        HealthStatus.PROBLEM,
        NOW,
        "MISMATCH",
        "old evidence",
        True,
    )
    service, _ = _service([expectation], [evaluation])

    baseline = service.get_health_report(broker_id).expectation_findings[0]
    changed_expectation = replace(
        expectation,
        revision=9,
        name="New wording",
        description="New description",
        severity=HealthSeverity.WARNING,
        rule_id="device.online",
    )
    service._expectation_management.list_expectations.return_value = (
        changed_expectation,
    )
    service._evaluator.evaluate_broker.return_value = replace(
        service._evaluator.evaluate_broker.return_value,
        evaluated_at=NOW.replace(day=5),
        topic_findings=(
            replace(
                evaluation,
                expectation_revision=9,
                status=HealthStatus.UNKNOWN,
                failure_code="OTHER",
                evidence_summary="new evidence",
            ),
        ),
    )

    changed = service.get_health_report(broker_id).expectation_findings[0]

    assert changed.fingerprint == baseline.fingerprint
    assert changed.profile_id == profile_id
    assert changed.rule_id == "device.online"
    assert changed.matched_topic == "devices/status"
    assert changed.severity == HealthSeverity.WARNING


@pytest.mark.parametrize("limit", [0, 201])
def test_health_queries_reject_out_of_range_limits(limit) -> None:
    service, _ = _service([])

    with pytest.raises(ValueError, match="between 1 and 200"):
        service.query_failure_history(broker_id=uuid4(), limit=limit)


def test_failure_history_maps_bounded_identity_and_cursor() -> None:
    broker_id = uuid4()
    failure = ExpectationFailure(
        uuid4(),
        uuid4(),
        NOW,
        NOW,
        occurrence_count=3,
        evidence_summary="x" * 501,
        snapshot_broker_id=broker_id,
        snapshot_topic="devices/status",
    )
    service, report = _service([], failures=[failure])
    report.broker_identity.return_value = broker_id
    report.target_identity.return_value = "devices/status"
    report.get_evidence_limitations.return_value = ()

    result = service.query_failure_history(broker_id=broker_id)

    assert result.returned_count == 1
    assert result.items[0].broker_id == broker_id
    assert result.items[0].target == "devices/status"
    assert result.items[0].evidence_truncated is True


def test_delete_failure_history_delegates_to_history_service() -> None:
    service, _ = _service([])
    failure_id = uuid4()

    service.delete_failure_history(failure_id)

    service._failure_history.delete.assert_called_once_with(failure_id)
