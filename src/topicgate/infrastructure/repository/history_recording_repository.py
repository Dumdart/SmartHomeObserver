from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update

from topicgate.core.models.history_recording import HistoryRecordingStatus
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.models.history_recording_row import (
    HistoryRecordingSessionRow, HistoryRecordingSettingRow,
)


class HistoryRecordingRepository:
    def __init__(self, db: DatabaseContext) -> None:
        self._db = db

    def enabled_brokers(self) -> tuple[UUID, ...]:
        with self._db.session() as session:
            return tuple(session.scalars(select(HistoryRecordingSettingRow.broker_id)
                         .where(HistoryRecordingSettingRow.enabled.is_(True))))

    def set_enabled(self, broker_id: UUID, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("History recording enabled must be a boolean.")
        with self._db.transaction() as session:
            session.merge(HistoryRecordingSettingRow(broker_id=broker_id, enabled=enabled))

    def begin_session(self, broker_id: UUID, session_id: UUID) -> HistoryRecordingStatus:
        prior = self.status(broker_id)
        now = datetime.now(timezone.utc)
        with self._db.transaction() as session:
            session.add(HistoryRecordingSessionRow(
                session_id=session_id, broker_id=broker_id, started_at=now,
            ))
        return HistoryRecordingStatus(
            broker_id, enabled=True, previous_unclean=prior.previous_unclean,
            started_at=now,
        )

    def checkpoint(
        self, session_id: UUID, status: HistoryRecordingStatus, *, closed: bool = False,
    ) -> None:
        with self._db.transaction() as session:
            session.execute(update(HistoryRecordingSessionRow)
                            .where(HistoryRecordingSessionRow.session_id == session_id)
                            .values(admitted=status.admitted, committed=status.committed,
                                    dropped=status.dropped, failed=status.failed, closed=closed))

    def status(self, broker_id: UUID) -> HistoryRecordingStatus:
        row = HistoryRecordingSessionRow
        with self._db.session() as session:
            enabled = session.scalar(select(HistoryRecordingSettingRow.enabled)
                                     .where(HistoryRecordingSettingRow.broker_id == broker_id))
            totals = session.execute(select(
                func.coalesce(func.sum(row.admitted), 0),
                func.coalesce(func.sum(row.committed), 0),
                func.coalesce(func.sum(row.dropped), 0),
                func.coalesce(func.sum(row.failed), 0),
            ).where(row.broker_id == broker_id)).one()
            unclosed = session.scalar(select(func.count()).select_from(row)
                                      .where(row.broker_id == broker_id, row.closed.is_(False)))
        admitted, committed, dropped, failed = totals
        return HistoryRecordingStatus(
            broker_id, bool(enabled), admitted, committed,
            0, dropped, failed,
            previous_unclean=bool(unclosed),
        )
