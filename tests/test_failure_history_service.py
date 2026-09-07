from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from topicgate.app.services.failure_history_service import FailureHistoryService
from topicgate.core.models.health import (
    ExpectationFailure,
    HealthExpectation,
    HealthSeverity,
    TopicTarget,
)


NOW = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)


def _failure(expectation_id, minutes, *, recovered=False, evidence="evidence"):
    timestamp = NOW + timedelta(minutes=minutes)
    return ExpectationFailure(
        uuid4(),
        expectation_id,
        timestamp,
        timestamp,
        recovered_at=timestamp if recovered else None,
        evidence_summary=evidence,
    )


def _service(failures, expectations=None):
    failure_repository = MagicMock()
    failure_repository.get_all_states.return_value = failures
    expectation_repository = MagicMock()
    expectation_repository.get.side_effect = (expectations or {}).get
    return FailureHistoryService(failure_repository, expectation_repository)


def test_active_and_closed_episodes_are_sorted_newest_first():
    expectation_id = uuid4()
    active = _failure(expectation_id, 1)
    closed = _failure(expectation_id, 2, recovered=True)
    service = _service([active, closed])

    assert service.get_active_episodes() == (active,)
    assert service.get_closed_episodes() == (closed,)


def test_filter_resolves_broker_topic_and_time_range():
    broker_id = uuid4()
    matching = HealthExpectation(
        uuid4(),
        1,
        True,
        HealthSeverity.CRITICAL,
        TopicTarget(broker_id, "home/status"),
        MagicMock(),
        frozenset(),
    )
    other = HealthExpectation(
        uuid4(),
        1,
        True,
        HealthSeverity.CRITICAL,
        TopicTarget(uuid4(), "home/status"),
        MagicMock(),
        frozenset(),
    )
    match = _failure(matching.expectation_id, 1)
    non_match = _failure(other.expectation_id, 2)
    service = _service(
        [non_match, match],
        {matching.expectation_id: matching, other.expectation_id: other},
    )

    assert service.filter(
        broker_id,
        "home/status",
        (NOW, NOW + timedelta(minutes=1)),
    ) == (match,)


def test_filter_uses_snapshotted_identity_after_expectation_deletion():
    broker_id = uuid4()
    failure = _failure(uuid4(), 1)
    failure.snapshot_broker_id = broker_id
    failure.snapshot_topic = "home/status"
    service = _service([failure])

    assert service.filter(broker_id, "home/status") == (failure,)


def test_filter_uses_snapshotted_identity_after_expectation_edit():
    original_broker_id = uuid4()
    expectation = HealthExpectation(
        uuid4(),
        2,
        True,
        HealthSeverity.CRITICAL,
        TopicTarget(uuid4(), "new/status"),
        MagicMock(),
        frozenset(),
    )
    failure = _failure(expectation.expectation_id, 1)
    failure.snapshot_broker_id = original_broker_id
    failure.snapshot_topic = "old/status"
    service = _service([failure], {expectation.expectation_id: expectation})

    assert service.filter(original_broker_id, "old/status") == (failure,)


def test_cursor_pagination_returns_next_cursor():
    failures = [_failure(uuid4(), minute) for minute in range(3)]
    service = _service(failures)

    first = service.get_cursor_pagination(limit=2)
    second = service.get_cursor_pagination(first.next_cursor, limit=2)

    assert first.items == (failures[2], failures[1])
    assert first.next_cursor == 2
    assert second.items == (failures[0],)
    assert second.next_cursor is None


def test_evidence_is_bounded_and_typo_alias_is_supported():
    failure = _failure(uuid4(), 0, evidence="012345")
    service = _service([failure])

    assert service.get_bounded_evidence(failure, 3) == "012"
    assert service.get_boudned_evidence(failure, 3) == "012"


def test_delete_delegates_to_failure_repository():
    failure_repository = MagicMock()
    service = FailureHistoryService(failure_repository)
    failure_id = uuid4()

    service.delete(failure_id)

    failure_repository.delete.assert_called_once_with(failure_id)


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"cursor": -1}, {"limit": -1}])
def test_pagination_rejects_invalid_arguments(kwargs):
    service = _service([])
    with pytest.raises(ValueError):
        service.get_cursor_pagination(**kwargs)
