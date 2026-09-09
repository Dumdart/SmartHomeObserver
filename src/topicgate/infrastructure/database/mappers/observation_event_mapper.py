from dataclasses import asdict
from datetime import timezone

from topicgate.core.models.observation_event import ObservationEvent
from topicgate.infrastructure.database.models.observation_event_row import ObservationEventRow


class ObservationEventMapper:
    @staticmethod
    def to_row(event: ObservationEvent, sequence: int) -> ObservationEventRow:
        if event.received_at.tzinfo is None or event.received_at.utcoffset() is None:
            raise ValueError("Observation receipt time must include a timezone.")
        values = asdict(event)
        values["received_at"] = event.received_at.astimezone(timezone.utc)
        return ObservationEventRow(**values, sequence=sequence)

    @staticmethod
    def to_dto(row: ObservationEventRow) -> ObservationEvent:
        received_at = row.received_at
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        return ObservationEvent(
            observation_id=row.observation_id, broker_id=row.broker_id,
            topic=row.topic, received_at=received_at, payload=row.payload,
            payload_size=row.payload_size, qos=row.qos, retain=row.retain,
            is_truncated=row.is_truncated, provenance=row.provenance,
        )
