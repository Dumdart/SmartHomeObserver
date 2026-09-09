import base64
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from topicgate.app.models.topic_history import HistoryEventResult, TopicHistoryResult
from topicgate.app.services.history_retention_service import HistoryRetentionService
from topicgate.core.interfaces.observation_history import ObservationHistoryReader
from topicgate.core.models.history_recording import HistoryRecordingStatus
from topicgate.core.models.observation_event import ObservationEvent
from topicgate.core.models.subscription import Subscription
from topicgate.core.mqtt_topics import mqtt_filter_matches
from topicgate.core.payload_limits import MAX_RENDERED_PAYLOAD_BYTES


MAX_HISTORY_RECORDS = 500
MAX_HISTORY_PAYLOAD_FIELD_BYTES = 256 * 1024
MAX_HISTORY_SCAN_EVENTS = 1000
MAX_HISTORY_CURSOR_BYTES = 2048


class TopicHistoryService:
    def __init__(
        self, reader: ObservationHistoryReader, retention: HistoryRetentionService,
        recording_status: Callable[[UUID], HistoryRecordingStatus],
    ) -> None:
        self._reader = reader
        self._retention = retention
        self._recording_status = recording_status

    def query(
        self, broker_id: UUID, topic_filter: str, *, after: datetime | None = None,
        before: datetime | None = None, cursor: str | None = None, limit: int = 100,
    ) -> TopicHistoryResult:
        Subscription(topic_filter)
        after, before = _utc(after), _utc(before)
        if after is not None and before is not None and after >= before:
            raise ValueError("History after must be earlier than before.")
        if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_RECORDS:
            raise ValueError(f"History limit must be between 1 and {MAX_HISTORY_RECORDS}.")
        scope = hashlib.sha256(json.dumps([
            str(broker_id), topic_filter, _iso(after), _iso(before),
        ], ensure_ascii=True).encode()).hexdigest()
        position, watermark, generation = None, None, None
        if cursor is not None:
            position, watermark, generation = _decode_cursor(cursor, scope)
        initial_usage = self._retention.usage(broker_id)
        if generation is None:
            generation = initial_usage.generation
        page = self._reader.scan(
            broker_id, watermark=watermark, position=position, after=after, before=before,
            limit=MAX_HISTORY_SCAN_EVENTS,
        )
        events: list[HistoryEventResult] = []
        field_bytes = 0
        consumed = 0
        for event in page.events:
            if mqtt_filter_matches(topic_filter, event.topic):
                rendered, size = _render(event)
                if len(events) >= limit or field_bytes + size > MAX_HISTORY_PAYLOAD_FIELD_BYTES:
                    break
                events.append(rendered)
                field_bytes += size
            position = (event.received_at, event.observation_id)
            consumed += 1
        has_more = page.has_more or consumed < len(page.events)
        next_cursor = _encode_cursor(scope, position, page.watermark, generation) if has_more else None
        usage = self._retention.usage(broker_id)
        recording = self._recording_status(broker_id)
        limitations = ["Only TopicGate receipts during enabled recording periods are available."]
        if not recording.enabled:
            limitations.append("History recording is disabled for this broker.")
        if recording.pending:
            limitations.append("Pending history writes are excluded from this committed snapshot.")
        if recording.dropped or recording.failed:
            limitations.append("History recording has dropped or failed events; history is incomplete.")
        if recording.previous_unclean:
            limitations.append("A previous recording session did not close cleanly; crash losses are unknown.")
        if recording.checkpoint_failed:
            limitations.append("Recording counters could not be fully checkpointed.")
        if usage.generation != generation:
            limitations.append("Retention changed during pagination; snapshot events may have been removed.")
        if usage.evictions:
            limitations.append("Retention has removed previously observed events.")
        if self._retention.last_error:
            limitations.append(self._retention.last_error)
        if usage.enforcement_pending:
            limitations.append("History retention enforcement is pending.")
        if any(e.is_truncated or e.rendering_truncated for e in events):
            limitations.append("One or more payloads were truncated at storage or rendering.")
        if has_more:
            limitations.append("This is a bounded page; follow next_cursor, including after an empty page.")
        return TopicHistoryResult(
            broker_id, topic_filter, after, before, tuple(events), next_cursor,
            page.watermark, self._retention.get_policy(), usage, recording,
            tuple(limitations), field_bytes,
        )


def _render(event: ObservationEvent) -> tuple[HistoryEventResult, int]:
    payload = event.payload[:MAX_RENDERED_PAYLOAD_BYTES]
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    encoded = base64.b64encode(payload).decode("ascii")
    # Check escaped JSON and base64 expansion both count against the wire budget.
    size = len(json.dumps({"payload_text": text, "payload_base64": encoded}, ensure_ascii=True).encode())
    return HistoryEventResult(
        event.observation_id, event.topic, event.received_at, text, encoded,
        event.payload_size, len(event.payload), len(payload), event.is_truncated,
        len(payload) < len(event.payload), event.qos, event.retain, event.provenance,
    ), size


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("History timestamps must include a timezone.")
    return value.astimezone(timezone.utc)


def _encode_cursor(scope: str, position: tuple[datetime, UUID], watermark: int, generation: int) -> str:
    value = {"v": 1, "q": scope, "p": [position[0].isoformat(), str(position[1])],
             "w": watermark, "g": generation}
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def _decode_cursor(cursor: str, scope: str) -> tuple[tuple[datetime, UUID], int, int]:
    try:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= MAX_HISTORY_CURSOR_BYTES:
            raise ValueError()
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(value, dict) or set(value) != {"v", "q", "p", "w", "g"}:
            raise ValueError()
        if type(value["v"]) is not int or value["v"] != 1 or value["q"] != scope:
            raise ValueError()
        if any(type(value[key]) is not int or not 0 <= value[key] <= 2**63 - 1 for key in ("w", "g")):
            raise ValueError()
        if not isinstance(value["p"], list) or len(value["p"]) != 2:
            raise ValueError()
        timestamp = _utc(datetime.fromisoformat(value["p"][0]))
        event_id = UUID(value["p"][1])
        return (timestamp, event_id), value["w"], value["g"]
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as error:
        raise ValueError("Invalid, unsupported, or differently scoped history cursor.") from error
