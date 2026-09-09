from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ObservationEvent:
    """One MQTT receipt observed by TopicGate, never a broker history claim."""

    observation_id: UUID
    broker_id: UUID
    topic: str
    received_at: datetime
    payload: bytes
    payload_size: int
    qos: int
    retain: bool
    is_truncated: bool = False
    provenance: str = "live_receipt"


@dataclass(frozen=True)
class HistoryScan:
    events: tuple[ObservationEvent, ...]
    watermark: int
    has_more: bool
