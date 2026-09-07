from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from topicgate.core.interfaces.health_repositories import (
    ExpectationFailureStore,
    HealthExpectationReader,
)
from topicgate.core.models.health import ExpectationFailure


DEFAULT_PAGE_SIZE = 50
DEFAULT_EVIDENCE_LIMIT = 500


@dataclass(frozen=True)
class FailureHistoryPage:
    """A stable page of failure episodes and the cursor for the next page."""

    items: tuple[ExpectationFailure, ...]
    next_cursor: int | None


class FailureHistoryService:
    """Query persisted health-failure episodes for application consumers."""

    def __init__(
        self,
        expectation_failure_repository: ExpectationFailureStore,
        health_expectation_repository: HealthExpectationReader | None = None,
    ) -> None:
        self._failure_repository = expectation_failure_repository
        self._expectation_repository = health_expectation_repository

    def get_bounded_evidence(
        self,
        failure: ExpectationFailure,
        max_length: int = DEFAULT_EVIDENCE_LIMIT,
    ) -> str | None:
        """Return an evidence summary capped at ``max_length`` characters."""
        if max_length < 0:
            raise ValueError("max_length must be non-negative")
        if failure.evidence_summary is None:
            return None
        return failure.evidence_summary[:max_length]

    def get_boudned_evidence(
        self,
        failure: ExpectationFailure,
        max_length: int = DEFAULT_EVIDENCE_LIMIT,
    ) -> str | None:
        """Compatibility alias for the original misspelled stub method."""
        return self.get_bounded_evidence(failure, max_length)

    def get_cursor_pagination(
        self,
        cursor: int | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        failures: tuple[ExpectationFailure, ...] | None = None,
    ) -> FailureHistoryPage:
        """Return a deterministic page using the item offset as its cursor."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        start = 0 if cursor is None else cursor
        if start < 0:
            raise ValueError("cursor must be non-negative")
        records = self._ordered(
            self._failure_repository.get_all_states()
            if failures is None
            else failures
        )
        items = records[start : start + limit]
        next_cursor = start + limit if start + limit < len(records) else None
        return FailureHistoryPage(tuple(items), next_cursor)

    def filter(
        self,
        broker: UUID | None = None,
        topic: str | None = None,
        time: datetime | tuple[datetime | None, datetime | None] | None = None,
    ) -> tuple[ExpectationFailure, ...]:
        """Filter episodes by broker, topic, and an inclusive time range."""
        start, end = _time_bounds(time)
        result: list[ExpectationFailure] = []
        for failure in self._failure_repository.get_all_states():
            if start is not None and failure.last_seen_at < start:
                continue
            if end is not None and failure.first_failed_at > end:
                continue
            if broker is None and topic is None:
                result.append(failure)
                continue
            if self._expectation_repository is None:
                raise ValueError(
                    "expectation_repository is required for broker/topic filters"
                )
            expectation = self._expectation_repository.get(failure.expectation_id)
            target = None if expectation is None else expectation.target
            target_broker = (
                failure.snapshot_broker_id
                if failure.snapshot_broker_id is not None
                else getattr(target, "broker_id", None)
            )
            target_topic = (
                failure.snapshot_topic
                if failure.snapshot_broker_id is not None
                else getattr(target, "topic", None)
            )
            if broker is not None and target_broker != broker:
                continue
            if topic is not None and target_topic != topic:
                continue
            result.append(failure)
        return tuple(self._ordered(result))

    def get_closed_episodes(self) -> tuple[ExpectationFailure, ...]:
        return tuple(
            self._ordered(
                failure
                for failure in self._failure_repository.get_all_states()
                if failure.recovered_at is not None
            )
        )

    def get_active_episodes(self) -> tuple[ExpectationFailure, ...]:
        return tuple(
            self._ordered(
                failure
                for failure in self._failure_repository.get_all_states()
                if failure.recovered_at is None
            )
        )

    def delete(self, failure_id: UUID) -> None:
        """Permanently delete one failure episode from history."""
        self._failure_repository.delete(failure_id)

    @staticmethod
    def _ordered(failures) -> list[ExpectationFailure]:
        return sorted(
            failures,
            key=lambda failure: (failure.first_failed_at, failure.failure_id.hex),
            reverse=True,
        )


def _time_bounds(
    value: datetime | tuple[datetime | None, datetime | None] | None,
) -> tuple[datetime | None, datetime | None]:
    if value is None:
        return None, None
    if isinstance(value, datetime):
        return value, None
    if len(value) != 2:
        raise ValueError("time must be a datetime or a (start, end) tuple")
    start, end = value
    if start is not None and end is not None and start > end:
        raise ValueError("time range start must not be after end")
    return start, end
