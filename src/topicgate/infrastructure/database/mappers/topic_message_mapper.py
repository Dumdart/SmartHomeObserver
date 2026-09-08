from datetime import timezone

from topicgate.core.models.topic_message import TopicMessage
from topicgate.infrastructure.database.models.mqtt_message_row import MqttMessageRow


class TopicMessageMapper:
    @staticmethod
    def to_dto(row: MqttMessageRow) -> TopicMessage:
        received_at = row.received_at
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        return TopicMessage(
            broker_id=row.broker_id,
            topic=row.topic,
            payload=row.payload,
            qos=row.qos,
            retain=row.retain,
            received_at=received_at,
            payload_size=row.payload_size,
            message_count=row.message_count,
            observation_id=row.observation_id,
            is_truncated=len(row.payload) < row.payload_size,
        )

    @staticmethod
    def to_row(message: TopicMessage) -> MqttMessageRow:
        return MqttMessageRow(
            broker_id=message.broker_id,
            topic=message.topic,
            payload=message.payload,
            qos=message.qos,
            retain=message.retain,
            received_at=message.received_at,
            payload_size=message.payload_size,
            message_count=message.message_count,
            observation_id=message.observation_id,
        )
