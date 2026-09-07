from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class ExpectationFailure:
    failure_id: UUID
    expectation_id: UUID
    first_failed_at: datetime
    last_seen_at: datetime
    occurrence_count: int = 0
    expected_revision: int = 0
    last_healthy_at: datetime | None = None
    recovered_at: datetime | None = None
    failure_code: str | None = None
    evidence_summary: str | None = None
    snapshot_broker_id: UUID | None = None
    snapshot_topic: str | None = None
