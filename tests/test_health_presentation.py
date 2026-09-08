from datetime import datetime, timezone
from uuid import uuid4

from topicgate.app.models.expectation_health_report import (
    ExpectationHealthFinding,
    ExpectationHealthReport,
    FindingCheckpoint,
)
from topicgate.app.services.finding_delta_service import finding_fingerprint
from topicgate.core.models.health import (
    EqualCondition,
    HealthExpectation,
    HealthSeverity,
    HealthStatus,
    TopicTarget,
    default_profile_id,
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
    broker_id = expectation.target.broker_id
    profile_id = default_profile_id(broker_id)
    rule_id = f"legacy-{expectation.expectation_id}"
    return ExpectationHealthFinding(
        expectation_id=expectation.expectation_id,
        profile_id=profile_id,
        rule_id=rule_id,
        severity=expectation.severity,
        matched_topic="devices/status",
        fingerprint=finding_fingerprint(
            profile_id=profile_id,
            rule_id=rule_id,
            broker_id=broker_id,
            matched_topic="devices/status",
        ),
        expectation_revision=expectation.revision,
        name=expectation.name,
        description="",
        target_kind="topic",
        target="devices/status",
        status=status,
        failure_code=None,
        evidence_summary="actual=offline; expected=online",
        evidence_complete=True,
        evidence_truncated=False,
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
        FindingCheckpoint(1, expectation.target.broker_id, (), True, 0),
        None,
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
