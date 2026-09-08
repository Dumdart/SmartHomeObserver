from collections.abc import Callable
from datetime import datetime
from threading import Lock
from uuid import UUID, uuid4

from topicgate.app.services.history_recording_service import HistoryRecordingService
from topicgate.core.interfaces.current_topic_reader import CurrentTopicReader
from topicgate.core.models.mqtt_message import MqttMessage
from topicgate.core.models.observation_event import ObservationEvent
from topicgate.core.models.observation_retention_policy import ObservationRetentionPolicy
from topicgate.core.models.topic_message import TopicMessage
from topicgate.processors.observation_retention_processor import ObservationRetentionProcessor


class ObservationRecorder:
    def __init__(
        self, current: CurrentTopicReader,
        record_current: Callable[[TopicMessage], None],
        history: HistoryRecordingService | None = None,
    ) -> None:
        self._current = current
        self._record_current = record_current
        self._history = history
        self._lock = Lock()

    def record(
        self, broker_id: UUID, message: MqttMessage,
        policy: ObservationRetentionPolicy, clock: Callable[[], datetime],
    ) -> tuple[TopicMessage, MqttMessage]:
        message, truncated = ObservationRetentionProcessor.truncate_mqtt_message(message, policy)
        with self._lock:
            previous = self._current.get_current_topic(broker_id, message.topic)
            entry = TopicMessage(
                broker_id=broker_id, topic=message.topic, payload=message.payload,
                qos=message.qos, retain=message.retain, received_at=clock(),
                payload_size=message.payload_size,
                message_count=1 if previous is None else previous.message.message_count + 1,
                observation_id=uuid4(), is_truncated=truncated,
            )
            self._record_current(entry)
            if self._history is not None:
                self._history.record(ObservationEvent(
                    observation_id=entry.observation_id, broker_id=broker_id,
                    topic=entry.topic, received_at=entry.received_at, payload=entry.payload,
                    payload_size=entry.payload_size, qos=entry.qos, retain=entry.retain,
                    is_truncated=entry.is_truncated,
                ))
            return entry, message
