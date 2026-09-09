from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select, update

from topicgate.core.models.observation_event import HistoryScan, ObservationEvent
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.mappers.observation_event_mapper import ObservationEventMapper
from topicgate.infrastructure.database.models.observation_event_row import (
    ObservationEventRow, ObservationHistoryClockRow,
)


class ObservationHistoryRepository:
    def __init__(self, db: DatabaseContext) -> None:
        self._db = db

    def append(self, events: tuple[ObservationEvent, ...]) -> None:
        if not events:
            return
        with self._db.transaction() as session:
            # Check sequence allocation and insertion commit atomically across writers.
            last = session.scalar(
                update(ObservationHistoryClockRow)
                .where(ObservationHistoryClockRow.id == 1)
                .values(last_sequence=ObservationHistoryClockRow.last_sequence + len(events))
                .returning(ObservationHistoryClockRow.last_sequence)
            )
            for index, event in enumerate(events, start=last - len(events) + 1):
                session.add(ObservationEventMapper.to_row(event, index))

    def scan(
        self, broker_id: UUID, *, watermark: int | None = None,
        position: tuple[datetime, UUID] | None = None,
        after: datetime | None = None, before: datetime | None = None,
        limit: int = 500,
    ) -> HistoryScan:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("History scan limit must be between 1 and 1000.")
        row = ObservationEventRow
        with self._db.session() as session:
            if watermark is None:
                watermark = session.scalar(select(ObservationHistoryClockRow.last_sequence))
            statement = select(row).where(row.broker_id == broker_id, row.sequence <= watermark)
            if after is not None:
                statement = statement.where(row.received_at > _utc(after))
            if before is not None:
                statement = statement.where(row.received_at < _utc(before))
            if position is not None:
                timestamp, event_id = position
                statement = statement.where(or_(
                    row.received_at > _utc(timestamp),
                    and_(row.received_at == _utc(timestamp), row.observation_id > event_id),
                ))
            rows = session.scalars(statement.order_by(
                row.received_at, row.observation_id,
            ).limit(limit + 1)).all()
            return HistoryScan(
                tuple(ObservationEventMapper.to_dto(item) for item in rows[:limit]),
                watermark, len(rows) > limit,
            )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("History timestamps must include a timezone.")
    return value.astimezone(timezone.utc)
