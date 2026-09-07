from datetime import datetime, timezone
from uuid import uuid4

from topicgate.app.models.expectation_health_report import (
    ExpectationHealthFinding,
    ExpectationHealthReport,
)
from topicgate.core.models.health import (
    EqualCondition,
    HealthExpectation,
    HealthSeverity,
    HealthStatus,
    TopicTarget,
)
from topicgate.presentation.health_presentation import (
    broker_health_summary,
    topic_health_summary,
)

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def _expectation(*, enabled: bool = True) -> HealthExpectation:
    return HealthExpectation(
        uuid4(),
        1,
        enabled,
        HealthSeverity.CRITICAL,
        TopicTarget(uuid4(), "devices/status"),
        EqualCondition(b"online"),
        frozenset(),
        "Device online",
    )


def _finding(expectation, status: HealthStatus) -> ExpectationHealthFinding:
    return ExpectationHealthFinding(
        expectation.expectation_id,
        expectation.revision,
        expectation.name,
        "",
        "topic",
        "devices/status",
        status,
        None,
        "actual=offline; expected=online",
        True,
        False,
    )


def _report(expectation, *, status=HealthStatus.HEALTHY, omitted=0, complete=True):
    return ExpectationHealthReport(
        expectation.target.broker_id,
        NOW,
        status,
        complete,
        HealthStatus.HEALTHY,
        (),
        (_finding(expectation, status),),
        0,
        1,
        omitted,
    )


def test_broker_summary_distinguishes_unavailable_not_evaluated_and_no_rules() -> None:
    expectation = _expectation(enabled=False)

    assert broker_health_summary(None, (), available=False).label == "Unavailable"
    assert broker_health_summary(None, (), available=True).label == "Not evaluated"
    summary = broker_health_summary(
        _report(expectation),
        (expectation,),
        available=True,
        now=NOW,
    )

    assert summary.label == "No enabled expectations"
    assert "Broker checks" in summary.explanation


def test_broker_summary_labels_visible_counts_and_incomplete_results() -> None:
    expectation = _expectation()
    report = _report(
        expectation,
        status=HealthStatus.PROBLEM,
        omitted=3,
        complete=False,
    )

    summary = broker_health_summary(
        report,
        (expectation,),
        available=True,
        now=NOW,
    )

    assert summary.label == "Failed"
    assert summary.counts == "Failed 1 · +3 not shown"
    assert "limited" in summary.explanation


def test_topic_badge_never_treats_omitted_results_as_healthy() -> None:
    first = _expectation()
    second = _expectation()
    second = HealthExpectation(
        second.expectation_id,
        second.revision,
        second.enabled,
        second.severity,
        TopicTarget(first.target.broker_id, "devices/status"),
        second.condition,
        second.actions,
        second.name,
    )
    report = _report(first, omitted=1)

    summary = topic_health_summary(
        "devices/status",
        report,
        (first, second),
        available=True,
    )

    assert summary.label == "Unknown"
    assert "omitted" in summary.detail


def test_topic_badge_hides_for_filters_and_reports_actual_evidence() -> None:
    expectation = _expectation()

    assert (
        topic_health_summary("devices/#", None, (expectation,), available=True).label
        == ""
    )
    summary = topic_health_summary(
        "devices/status",
        _report(expectation, status=HealthStatus.PROBLEM),
        (expectation,),
        available=True,
    )

    assert summary.label.startswith("Failed")
    assert "actual=offline" in summary.evidence
