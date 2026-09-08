from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class HistoryRetentionPolicy:
    max_age_seconds: int | None = 7 * 24 * 60 * 60
    max_events_per_broker: int = 100_000
    max_events_per_topic: int | None = None
    max_payload_bytes: int = 256 * 1024 * 1024
    prune_batch_size: int = 500
    prune_interval_seconds: int = 60

    def __post_init__(self) -> None:
        for name in ("max_age_seconds", "max_events_per_broker", "max_events_per_topic",
                     "max_payload_bytes", "prune_batch_size", "prune_interval_seconds"):
            value = getattr(self, name)
            if value is None and name in ("max_age_seconds", "max_events_per_topic"):
                continue
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        if self.prune_batch_size > 500:
            raise ValueError("prune_batch_size cannot exceed 500.")


@dataclass(frozen=True)
class HistoryUsage:
    broker_id: UUID | None
    event_count: int
    payload_bytes: int
    oldest_received_at: datetime | None
    last_pruned_at: datetime | None
    generation: int
    evictions: dict[str, int] = field(default_factory=dict)
    enforcement_pending: bool = False


@dataclass(frozen=True)
class HistoryPruneResult:
    deleted: int
    by_reason: dict[str, int]
    pending: bool
