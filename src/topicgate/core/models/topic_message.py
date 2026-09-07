from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

@dataclass(frozen=True)
class TopicMessage:
    broker_id: UUID
    topic: str
    payload: bytes
    qos: int
    retain: bool
    received_at: datetime
    payload_size: int
    message_count: int
    observation_id: UUID
    is_truncated: bool = False
