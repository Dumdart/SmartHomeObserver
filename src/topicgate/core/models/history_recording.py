from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class HistoryRecordingStatus:
    broker_id: UUID
    enabled: bool = False
    admitted: int = 0
    committed: int = 0
    pending: int = 0
    dropped: int = 0
    failed: int = 0
    previous_unclean: bool = False
    checkpoint_failed: bool = False
    started_at: datetime | None = None

    @property
    def incomplete(self) -> bool:
        return bool(not self.enabled or self.pending or self.dropped or self.failed
                    or self.previous_unclean or self.checkpoint_failed)
