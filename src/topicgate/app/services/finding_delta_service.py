"""Stateless checkpoint and delta protocol for expectation findings."""

from hashlib import sha256
from uuid import UUID

from topicgate.app.models.expectation_health_report import (
    ExpectationHealthFinding,
    FindingCheckpoint,
    FindingCheckpointEntry,
    FindingDelta,
    FindingDeltaEvent,
    FindingDeltaKind,
    FindingProtocolVersion,
)
from topicgate.core.models.health import HealthSeverity, HealthStatus, normalize_rule_id
from topicgate.core.mqtt_topics import validate_topic_name


FINDING_PROTOCOL_VERSION: FindingProtocolVersion = 1
MAX_FINDING_PROTOCOL_ENTRIES = 200
_FINGERPRINT_PREFIX = "v1:sha256:"
_EVENT_KIND_ORDER = {
    "new": 0,
    "recovered": 1,
    "unknown": 2,
    "severity_change": 3,
    "continuing": 4,
}


def finding_fingerprint(
    *,
    profile_id: UUID,
    rule_id: str,
    broker_id: UUID,
    matched_topic: str | None,
) -> str:
    """Return the stable v1 fingerprint for one finding identity."""
    normalized_rule_id = normalize_rule_id(rule_id)
    canonical = b"".join(
        _length_prefixed(value)
        for value in (
            "v1",
            str(profile_id),
            normalized_rule_id,
            str(broker_id),
            matched_topic,
        )
    )
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical).hexdigest()}"


def build_finding_checkpoint(
    broker_id: UUID,
    findings: tuple[ExpectationHealthFinding, ...],
) -> FindingCheckpoint:
    entries = _entries_from_findings(findings)
    _validate_entries(entries, broker_id=broker_id)
    omitted_count = max(0, len(entries) - MAX_FINDING_PROTOCOL_ENTRIES)
    checkpoint = FindingCheckpoint(
        version=FINDING_PROTOCOL_VERSION,
        broker_id=broker_id,
        entries=entries[:MAX_FINDING_PROTOCOL_ENTRIES],
        complete=omitted_count == 0,
        omitted_count=omitted_count,
    )
    _validate_checkpoint(checkpoint, expected_broker_id=broker_id)
    return checkpoint


def build_finding_delta(
    previous: FindingCheckpoint,
    *,
    broker_id: UUID,
    findings: tuple[ExpectationHealthFinding, ...],
) -> FindingDelta:
    _validate_checkpoint(previous, expected_broker_id=broker_id)
    current_entries = _entries_from_findings(findings)
    _validate_entries(current_entries, broker_id=broker_id)
    return _compare_entries(
        previous.entries,
        current_entries,
        previous_complete=previous.complete,
        current_complete=len(current_entries) <= MAX_FINDING_PROTOCOL_ENTRIES,
    )


def compare_finding_checkpoints(
    previous: FindingCheckpoint,
    current: FindingCheckpoint,
) -> FindingDelta:
    _validate_checkpoint(previous, expected_broker_id=current.broker_id)
    _validate_checkpoint(current, expected_broker_id=current.broker_id)
    return _compare_entries(
        previous.entries,
        current.entries,
        previous_complete=previous.complete,
        current_complete=current.complete,
    )


def _compare_entries(
    previous_entries: tuple[FindingCheckpointEntry, ...],
    current_entries: tuple[FindingCheckpointEntry, ...],
    *,
    previous_complete: bool,
    current_complete: bool,
) -> FindingDelta:
    previous_by_fingerprint = {
        entry.fingerprint: entry for entry in previous_entries
    }
    current_by_fingerprint = {
        entry.fingerprint: entry for entry in current_entries
    }
    events: list[FindingDeltaEvent] = []

    for fingerprint, entry in current_by_fingerprint.items():
        old = previous_by_fingerprint.get(fingerprint)
        if entry.status == HealthStatus.PROBLEM:
            events.append(
                _event(
                    "continuing"
                    if old is not None and old.status == HealthStatus.PROBLEM
                    else "new",
                    old,
                    entry,
                )
            )
        elif entry.status == HealthStatus.UNKNOWN:
            events.append(_event("unknown", old, entry))

        if old is not None and old.severity != entry.severity:
            events.append(_event("severity_change", old, entry))

    if previous_complete and current_complete:
        for fingerprint, old in previous_by_fingerprint.items():
            if old.status != HealthStatus.PROBLEM:
                continue
            entry = current_by_fingerprint.get(fingerprint)
            if entry is None or entry.status == HealthStatus.HEALTHY:
                events.append(_event("recovered", old, entry))

    ordered = tuple(
        sorted(
            events,
            key=lambda event: (
                _EVENT_KIND_ORDER[event.kind],
                event.fingerprint,
            ),
        )
    )
    omitted_count = max(0, len(ordered) - MAX_FINDING_PROTOCOL_ENTRIES)
    returned = ordered[:MAX_FINDING_PROTOCOL_ENTRIES]
    return FindingDelta(
        version=FINDING_PROTOCOL_VERSION,
        events=returned,
        complete=previous_complete and current_complete and omitted_count == 0,
        returned_count=len(returned),
        omitted_count=omitted_count,
    )


def _entries_from_findings(
    findings: tuple[ExpectationHealthFinding, ...],
) -> tuple[FindingCheckpointEntry, ...]:
    return tuple(
        sorted(
            (
                FindingCheckpointEntry(
                    profile_id=finding.profile_id,
                    rule_id=finding.rule_id,
                    matched_topic=finding.matched_topic,
                    fingerprint=finding.fingerprint,
                    status=finding.status,
                    severity=finding.severity,
                )
                for finding in findings
            ),
            key=lambda entry: entry.fingerprint,
        )
    )


def validate_finding_checkpoint(
    checkpoint: FindingCheckpoint,
    *,
    expected_broker_id: UUID,
) -> None:
    _validate_checkpoint(checkpoint, expected_broker_id=expected_broker_id)


def _validate_checkpoint(
    checkpoint: FindingCheckpoint,
    *,
    expected_broker_id: UUID,
) -> None:
    if not isinstance(checkpoint, FindingCheckpoint):
        raise ValueError("Finding checkpoint must be a FindingCheckpoint object.")
    if (
        type(checkpoint.version) is not int
        or checkpoint.version != FINDING_PROTOCOL_VERSION
    ):
        raise ValueError(
            f"Unsupported finding checkpoint version: {checkpoint.version}."
        )
    if not isinstance(checkpoint.broker_id, UUID):
        raise ValueError("Finding checkpoint broker ID must be a UUID.")
    if checkpoint.broker_id != expected_broker_id:
        raise ValueError(
            "Finding checkpoint broker does not match the requested broker."
        )
    if len(checkpoint.entries) > MAX_FINDING_PROTOCOL_ENTRIES:
        raise ValueError(
            f"Finding checkpoint cannot contain more than "
            f"{MAX_FINDING_PROTOCOL_ENTRIES} entries."
        )
    if type(checkpoint.complete) is not bool:
        raise ValueError("Finding checkpoint complete must be a boolean.")
    if type(checkpoint.omitted_count) is not int or checkpoint.omitted_count < 0:
        raise ValueError("Finding checkpoint omitted_count must be non-negative.")
    if checkpoint.complete and checkpoint.omitted_count:
        raise ValueError("A complete finding checkpoint cannot have omitted entries.")

    _validate_entries(checkpoint.entries, broker_id=checkpoint.broker_id)


def _validate_entries(
    entries: tuple[FindingCheckpointEntry, ...],
    *,
    broker_id: UUID,
) -> None:
    seen: set[str] = set()
    fingerprints: list[str] = []
    for entry in entries:
        if not isinstance(entry, FindingCheckpointEntry):
            raise ValueError("Finding checkpoint entries must be checkpoint entries.")
        if not isinstance(entry.profile_id, UUID):
            raise ValueError("Finding checkpoint profile IDs must be UUIDs.")
        try:
            normalized_rule_id = normalize_rule_id(entry.rule_id)
        except ValueError as error:
            raise ValueError(
                "Finding checkpoint contains an invalid rule ID."
            ) from error
        if entry.rule_id != normalized_rule_id:
            raise ValueError("Finding checkpoint rule IDs must be normalized.")
        if not isinstance(entry.status, HealthStatus):
            raise ValueError("Finding checkpoint contains an invalid status.")
        if not isinstance(entry.severity, HealthSeverity):
            raise ValueError("Finding checkpoint contains an invalid severity.")
        if entry.matched_topic is not None:
            if not isinstance(entry.matched_topic, str):
                raise ValueError("Finding checkpoint matched topics must be strings.")
            try:
                validate_topic_name(entry.matched_topic)
            except ValueError as error:
                raise ValueError(
                    "Finding checkpoint contains an invalid matched topic."
                ) from error
        expected = finding_fingerprint(
            profile_id=entry.profile_id,
            rule_id=entry.rule_id,
            broker_id=broker_id,
            matched_topic=entry.matched_topic,
        )
        if entry.fingerprint != expected:
            raise ValueError(
                "Finding checkpoint entry fingerprint does not match its identity."
            )
        if entry.fingerprint in seen:
            raise ValueError("Finding checkpoint contains duplicate entries.")
        seen.add(entry.fingerprint)
        fingerprints.append(entry.fingerprint)
    if fingerprints != sorted(fingerprints):
        raise ValueError("Finding checkpoint entries must be fingerprint-sorted.")


def _event(
    kind: FindingDeltaKind,
    previous: FindingCheckpointEntry | None,
    current: FindingCheckpointEntry | None,
) -> FindingDeltaEvent:
    identity = current or previous
    if identity is None:
        raise ValueError("A finding delta event requires an identity.")
    return FindingDeltaEvent(
        kind=kind,
        profile_id=identity.profile_id,
        rule_id=identity.rule_id,
        matched_topic=identity.matched_topic,
        fingerprint=identity.fingerprint,
        previous_status=None if previous is None else previous.status,
        current_status=None if current is None else current.status,
        previous_severity=None if previous is None else previous.severity,
        current_severity=None if current is None else current.severity,
    )


def _length_prefixed(value: str | None) -> bytes:
    if value is None:
        return (2**32 - 1).to_bytes(4, "big")
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded
