from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from topicgate.app.models.expectation_health_report import (
    ExpectationHealthFinding,
    FindingCheckpoint,
    FindingCheckpointEntry,
)
from topicgate.app.services.finding_delta_service import (
    build_finding_checkpoint,
    build_finding_delta,
    compare_finding_checkpoints,
    finding_fingerprint,
    validate_finding_checkpoint,
)
from topicgate.core.models.health import HealthSeverity, HealthStatus


BROKER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROFILE_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _entry(
    status: HealthStatus,
    *,
    rule_id: str = "device-online",
    severity: HealthSeverity = HealthSeverity.CRITICAL,
    broker_id: UUID = BROKER_ID,
    profile_id: UUID = PROFILE_ID,
    matched_topic: str | None = "Devices/Status",
) -> FindingCheckpointEntry:
    return FindingCheckpointEntry(
        profile_id=profile_id,
        rule_id=rule_id,
        matched_topic=matched_topic,
        fingerprint=finding_fingerprint(
            profile_id=profile_id,
            rule_id=rule_id,
            broker_id=broker_id,
            matched_topic=matched_topic,
        ),
        status=status,
        severity=severity,
    )


def _checkpoint(
    *entries: FindingCheckpointEntry,
    broker_id: UUID = BROKER_ID,
    complete: bool = True,
    omitted_count: int = 0,
) -> FindingCheckpoint:
    return FindingCheckpoint(1, broker_id, entries, complete, omitted_count)


def _finding(
    index: int,
    status: HealthStatus,
    *,
    severity: HealthSeverity = HealthSeverity.CRITICAL,
) -> ExpectationHealthFinding:
    rule_id = f"rule-{index:03d}"
    topic = f"devices/{index}"
    return ExpectationHealthFinding(
        expectation_id=uuid4(),
        profile_id=PROFILE_ID,
        rule_id=rule_id,
        severity=severity,
        matched_topic=topic,
        fingerprint=finding_fingerprint(
            profile_id=PROFILE_ID,
            rule_id=rule_id,
            broker_id=BROKER_ID,
            matched_topic=topic,
        ),
        expectation_revision=1,
        name=f"Rule {index}",
        description="",
        target_kind="topic",
        target=topic,
        status=status,
        failure_code=None,
        evidence_summary=None,
        evidence_complete=True,
        evidence_truncated=False,
    )


def test_fingerprint_is_normalized_length_safe_and_case_sensitive() -> None:
    baseline = finding_fingerprint(
        profile_id=PROFILE_ID,
        rule_id="Device.Online",
        broker_id=BROKER_ID,
        matched_topic="Devices/Status",
    )

    assert baseline == finding_fingerprint(
        profile_id=PROFILE_ID,
        rule_id="device.online",
        broker_id=BROKER_ID,
        matched_topic="Devices/Status",
    )
    assert baseline.startswith("v1:sha256:")
    assert len(baseline.removeprefix("v1:sha256:")) == 64
    assert baseline != finding_fingerprint(
        profile_id=uuid4(),
        rule_id="device.online",
        broker_id=BROKER_ID,
        matched_topic="Devices/Status",
    )
    assert baseline != finding_fingerprint(
        profile_id=PROFILE_ID,
        rule_id="device.offline",
        broker_id=BROKER_ID,
        matched_topic="Devices/Status",
    )
    assert baseline != finding_fingerprint(
        profile_id=PROFILE_ID,
        rule_id="device.online",
        broker_id=uuid4(),
        matched_topic="Devices/Status",
    )
    assert baseline != finding_fingerprint(
        profile_id=PROFILE_ID,
        rule_id="device.online",
        broker_id=BROKER_ID,
        matched_topic="devices/status",
    )
    assert baseline != finding_fingerprint(
        profile_id=PROFILE_ID,
        rule_id="device.online",
        broker_id=BROKER_ID,
        matched_topic=None,
    )


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (None, HealthStatus.HEALTHY, []),
        (None, HealthStatus.PROBLEM, ["new"]),
        (None, HealthStatus.UNKNOWN, ["unknown"]),
        (HealthStatus.HEALTHY, HealthStatus.HEALTHY, []),
        (HealthStatus.HEALTHY, HealthStatus.PROBLEM, ["new"]),
        (HealthStatus.HEALTHY, HealthStatus.UNKNOWN, ["unknown"]),
        (HealthStatus.PROBLEM, HealthStatus.HEALTHY, ["recovered"]),
        (HealthStatus.PROBLEM, HealthStatus.PROBLEM, ["continuing"]),
        (HealthStatus.PROBLEM, HealthStatus.UNKNOWN, ["unknown"]),
        (HealthStatus.UNKNOWN, HealthStatus.HEALTHY, []),
        (HealthStatus.UNKNOWN, HealthStatus.PROBLEM, ["new"]),
        (HealthStatus.UNKNOWN, HealthStatus.UNKNOWN, ["unknown"]),
    ],
)
def test_checkpoint_transition_matrix(previous, current, expected) -> None:
    old = _checkpoint() if previous is None else _checkpoint(_entry(previous))
    new = _checkpoint(_entry(current))

    delta = compare_finding_checkpoints(old, new)

    assert [event.kind for event in delta.events] == expected


@pytest.mark.parametrize(
    ("previous_status", "expected"),
    [
        (HealthStatus.PROBLEM, ["recovered"]),
        (HealthStatus.HEALTHY, []),
        (HealthStatus.UNKNOWN, []),
    ],
)
def test_missing_current_identity_only_recovers_previous_problem(
    previous_status, expected
) -> None:
    delta = compare_finding_checkpoints(
        _checkpoint(_entry(previous_status)),
        _checkpoint(),
    )

    assert [event.kind for event in delta.events] == expected


def test_unknown_never_emits_recovered_and_severity_change_is_additive() -> None:
    delta = compare_finding_checkpoints(
        _checkpoint(_entry(HealthStatus.PROBLEM)),
        _checkpoint(
            _entry(
                HealthStatus.UNKNOWN,
                severity=HealthSeverity.WARNING,
            )
        ),
    )

    assert [event.kind for event in delta.events] == [
        "unknown",
        "severity_change",
    ]


@pytest.mark.parametrize(
    "previous_complete,current_complete",
    [(False, True), (True, False)],
)
def test_incomplete_checkpoint_suppresses_recovery(
    previous_complete, current_complete
) -> None:
    delta = compare_finding_checkpoints(
        _checkpoint(
            _entry(HealthStatus.PROBLEM),
            complete=previous_complete,
        ),
        _checkpoint(complete=current_complete),
    )

    assert delta.events == ()
    assert delta.complete is False


def test_checkpoints_and_deltas_are_deterministically_bounded() -> None:
    findings = tuple(_finding(index, HealthStatus.PROBLEM) for index in range(201))

    checkpoint = build_finding_checkpoint(BROKER_ID, findings)
    delta = build_finding_delta(_checkpoint(), broker_id=BROKER_ID, findings=findings)

    assert len(checkpoint.entries) == 200
    assert checkpoint.complete is False
    assert checkpoint.omitted_count == 1
    assert [entry.fingerprint for entry in checkpoint.entries] == sorted(
        entry.fingerprint for entry in checkpoint.entries
    )
    assert delta.returned_count == 200
    assert delta.omitted_count == 1
    assert delta.complete is False
    assert all(event.kind == "new" for event in delta.events)
    assert [event.fingerprint for event in delta.events] == sorted(
        event.fingerprint for event in delta.events
    )


def test_delta_bound_prioritizes_actionable_events_before_continuing() -> None:
    findings = tuple(
        _finding(
            index,
            HealthStatus.PROBLEM,
            severity=HealthSeverity.WARNING,
        )
        for index in range(200)
    )
    previous_entries = tuple(
        sorted(
            (
                FindingCheckpointEntry(
                    profile_id=finding.profile_id,
                    rule_id=finding.rule_id,
                    matched_topic=finding.matched_topic,
                    fingerprint=finding.fingerprint,
                    status=HealthStatus.PROBLEM,
                    severity=HealthSeverity.CRITICAL,
                )
                for finding in findings
            ),
            key=lambda entry: entry.fingerprint,
        )
    )

    delta = build_finding_delta(
        _checkpoint(*previous_entries),
        broker_id=BROKER_ID,
        findings=findings,
    )

    assert delta.returned_count == 200
    assert delta.omitted_count == 200
    assert all(event.kind == "severity_change" for event in delta.events)


@pytest.mark.parametrize(
    "checkpoint,match",
    [
        (replace(_checkpoint(), version=2), "Unsupported"),
        (replace(_checkpoint(), broker_id=uuid4()), "broker"),
        (
            _checkpoint(
                _entry(HealthStatus.HEALTHY),
                _entry(HealthStatus.HEALTHY),
            ),
            "duplicate",
        ),
        (
            _checkpoint(
                *(
                    _entry(HealthStatus.HEALTHY, rule_id=f"rule-{index}")
                    for index in range(201)
                )
            ),
            "more than 200",
        ),
        (
            _checkpoint(
                replace(
                    _entry(HealthStatus.HEALTHY),
                    fingerprint="v1:sha256:" + "0" * 64,
                )
            ),
            "fingerprint",
        ),
    ],
)
def test_malformed_checkpoints_fail_clearly(checkpoint, match) -> None:
    with pytest.raises(ValueError, match=match):
        validate_finding_checkpoint(checkpoint, expected_broker_id=BROKER_ID)
