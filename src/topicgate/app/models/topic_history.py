from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from topicgate.core.models.history_recording import HistoryRecordingStatus
from topicgate.core.models.history_retention import HistoryRetentionPolicy, HistoryUsage


@dataclass(frozen=True)
class HistoryEventResult:
    observation_id: UUID
    topic: str
    received_at: datetime
    payload_text: str | None
    payload_base64: str
    payload_size: int
    stored_payload_bytes: int
    rendered_payload_bytes: int
    is_truncated: bool
    rendering_truncated: bool
    qos: int
    retain: bool
    provenance: str


@dataclass(frozen=True)
class TopicHistoryResult:
    broker_id: UUID
    topic_filter: str
    after: datetime | None
    before: datetime | None
    events: tuple[HistoryEventResult, ...]
    next_cursor: str | None
    snapshot_watermark: int
    retention: HistoryRetentionPolicy
    usage: HistoryUsage
    recording: HistoryRecordingStatus
    limitations: tuple[str, ...]
    rendered_payload_field_bytes: int
    source: str = "TopicGate-observed history; not authoritative broker history"
