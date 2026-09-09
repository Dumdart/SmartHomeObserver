from datetime import datetime
from typing import Protocol
from uuid import UUID

from topicgate.core.models.observation_event import HistoryScan, ObservationEvent


class ObservationHistoryWriter(Protocol):
    def append(self, events: tuple[ObservationEvent, ...]) -> None: ...


class ObservationHistoryReader(Protocol):
    def scan(
        self, broker_id: UUID, *, watermark: int | None = None,
        position: tuple[datetime, UUID] | None = None,
        after: datetime | None = None, before: datetime | None = None,
        limit: int = 500,
    ) -> HistoryScan: ...
