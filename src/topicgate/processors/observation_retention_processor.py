from dataclasses import replace

from topicgate.core.models.mqtt_message import MqttMessage
from topicgate.core.models.observation_retention_policy import (
    ObservationRetentionPolicy,
)
from topicgate.core.models.topic_message import TopicMessage


class ObservationRetentionProcessor:
    """Apply policy transformations without persistence concerns."""

    @staticmethod
    def truncate_mqtt_message(
        message: MqttMessage,
        policy: ObservationRetentionPolicy,
    ) -> tuple[MqttMessage, bool]:
        limit = policy.max_payload_bytes_per_topic
        if len(message.payload) <= limit:
            return message, False
        return replace(
            message,
            payload=message.payload[:limit],
            payload_size=message.payload_size,
        ), True

    @staticmethod
    def truncate_topic_message(
        message: TopicMessage,
        policy: ObservationRetentionPolicy,
    ) -> TopicMessage:
        limit = policy.max_payload_bytes_per_topic
        if len(message.payload) <= limit:
            return message
        return replace(message, payload=message.payload[:limit])
