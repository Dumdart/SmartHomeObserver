import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.interfaces.current_topic_reader import CurrentTopicReader
from topicgate.core.interfaces.health_observation_sink import HealthObservationSink
from topicgate.core.interfaces.topic_message_recorder import TopicMessageRecorder
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.mqtt_message import MqttMessage
from topicgate.core.models.mqtt_observation import (
    MqttObservation,
)
from topicgate.core.models.observation_retention_policy import (
    ObservationRetentionPolicy,
)
from topicgate.core.models.observation_status import ObservationStatus
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.topic_message import TopicMessage
from topicgate.core.mqtt_topics import mqtt_filter_matches, validate_topic_name
from topicgate.core.observer_limits import TOPIC_TREE_REFRESH_INTERVAL_SECONDS
from topicgate.core.payload_limits import MAX_PENDING_MESSAGE_NOTIFICATIONS
from topicgate.infrastructure.mqtt.callbacks.observer_repository_callbacks import (
    ObserverRepositoryCallbacks,
)
from topicgate.infrastructure.mqtt.mqtt_gate import MqttGate
from topicgate.processors.subscription_manager import SubscriptionManager
from topicgate.processors.observation_retention_processor import (
    ObservationRetentionProcessor,
)


logger = logging.getLogger(__name__)


class ObserverMqttRepository:
    """Observe messages matching the supplied absolute MQTT topic filters."""

    topic_update_interval = TOPIC_TREE_REFRESH_INTERVAL_SECONDS

    def __init__(
        self,
        config: MqttConfig,
        topic_filters: list[str] | list[Subscription],
        retention_policy: Callable[[], ObservationRetentionPolicy] | None = None,
        observation_sink: Callable[[MqttObservation], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        *,
        broker_id: UUID,
        message_recorder: TopicMessageRecorder,
        current_topics: CurrentTopicReader,
        health_sink: HealthObservationSink,
    ) -> None:
        self._retention_policy = retention_policy or ObservationRetentionPolicy
        self._observation_sink = observation_sink
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._broker_id = broker_id
        self._message_recorder = message_recorder
        self._current_topics = current_topics
        self.health_sink = health_sink
        self.message_queue: asyncio.Queue[MqttMessage] = asyncio.Queue(
            maxsize=MAX_PENDING_MESSAGE_NOTIFICATIONS
        )
        self.connection_status_queue: asyncio.Queue[ConnectionStatus] = asyncio.Queue()
        self.connection_status = ConnectionStatus.DISCONNECTED
        self.connected_at: datetime | None = None
        self.observation_started_at: datetime | None = None
        self._dropped_message_count = 0
        self._recording_failure_count = 0
        self._subscription_failure_count = 0
        self._subscription_rejected_count = 0
        self._is_running = False
        self._is_stopping = False
        self._lifecycle_lock = asyncio.Lock()
        self._mqtt_gate = MqttGate(
            config, ObserverRepositoryCallbacks(self), topic_filters
        )
        self._subscription_manager = SubscriptionManager(
            self._mqtt_gate, self.handle_message
        )

    async def start(self) -> None:
        async with self._lifecycle_lock:
            await self._start()

    async def _start(self) -> None:
        if self._is_running:
            return

        self._is_running = True
        self._set_connection_status(ConnectionStatus.CONNECTING)
        try:
            await self._mqtt_gate.start()
            self._set_connection_status(ConnectionStatus.CONNECTED)
            try:
                await self._subscription_manager.activate()
            except Exception:
                self._subscription_failure_count += 1
                raise
        except Exception as ex:
            self._is_running = False
            self._is_stopping = True
            try:
                await self._mqtt_gate.stop()
            except Exception:
                pass
            finally:
                self._is_stopping = False
                self._set_connection_status(ConnectionStatus.DISCONNECTED)
            raise ConnectionError(
                f"ObserverRepository could not start the MQTT connection: {ex}"
            ) from ex

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop()

    async def _stop(self) -> None:
        if not self._is_running and not self._mqtt_gate.is_started:
            self._set_connection_status(ConnectionStatus.DISCONNECTED)
            return

        self._is_running = False
        self._is_stopping = True
        try:
            try:
                await self._subscription_manager.deactivate()
            finally:
                await self._mqtt_gate.stop()
        except Exception as ex:
            raise ConnectionError(
                "ObserverRepository could not stop the MQTT connection."
            ) from ex
        finally:
            self._is_stopping = False
            self._set_connection_status(ConnectionStatus.DISCONNECTED)

    async def update_broker(
        self,
        new_config: MqttConfig,
        subscriptions: tuple[Subscription, ...] | None = None,
    ) -> None:
        """Replace the MQTT connection with one configured for a new broker."""
        async with self._lifecycle_lock:
            previous_gate = self._mqtt_gate
            previous_manager = self._subscription_manager
            was_running = self._is_running
            active_subscriptions = list(
                self.subscriptions if subscriptions is None else subscriptions
            )

            if was_running or self._mqtt_gate.is_started:
                await self._stop()

            self._mqtt_gate = MqttGate(
                new_config,
                ObserverRepositoryCallbacks(self),
                active_subscriptions,
            )
            self._subscription_manager = SubscriptionManager(
                self._mqtt_gate, self.handle_message
            )

            try:
                await self._start()
            except Exception:
                # Check that a failed broker change leaves the repository using
                # the previous, known configuration rather than the failed one.
                self._mqtt_gate = previous_gate
                self._subscription_manager = previous_manager
                if was_running:
                    try:
                        await self._start()
                    except Exception:
                        pass
                raise

    def get_value(self, topic: str) -> bytes | None:
        state = self.get_state(topic)
        return state.payload if state is not None else None

    def get_state(self, topic: str) -> MqttObservation | None:
        current = self._current_topics.get_current_topic(self._broker_id, topic)
        return None if current is None else current.to_observation()

    def get_all_topics(self) -> tuple[str, ...]:
        return tuple(
            current.message.topic
            for current in self._current_topics.get_current_topics(self._broker_id)
        )

    def handle_message(self, _client: Any, _userdata: Any, msg: MqttMessage) -> None:
        validate_topic_name(msg.topic)
        msg, is_truncated = ObservationRetentionProcessor.truncate_mqtt_message(
            msg,
            self._retention_policy(),
        )
        previous = self._current_topics.get_current_topic(
            self._broker_id,
            msg.topic,
        )
        entry = TopicMessage(
            broker_id=self._broker_id,
            topic=msg.topic,
            payload=msg.payload,
            qos=msg.qos,
            retain=msg.retain,
            received_at=self._clock(),
            payload_size=msg.payload_size,
            message_count=(
                1 if previous is None else previous.message.message_count + 1
            ),
            observation_id=uuid4(),
            is_truncated=is_truncated
        )
        try:
            self._message_recorder.record_message(entry)
        except Exception:
            self._recording_failure_count += 1
            raise
        try:
            self.health_sink.evaluate_observation(entry)
        except Exception:
            logger.exception(
                "Health evaluation failed for broker %s topic %s.",
                entry.broker_id,
                entry.topic,
            )
        observation = CurrentTopic(entry, ObservationStatus.LIVE).to_observation()
        if self._observation_sink is not None:
            self._observation_sink(observation)
        if self.message_queue.full():
            self.message_queue.get_nowait()
            self._dropped_message_count += 1
        self.message_queue.put_nowait(msg)

    @property
    def dropped_message_count(self) -> int:
        return self._dropped_message_count + self._mqtt_gate.dropped_message_count

    @property
    def recording_failure_count(self) -> int:
        return self._recording_failure_count

    @property
    def subscription_failure_count(self) -> int:
        return self._subscription_failure_count

    @property
    def subscription_rejected_count(self) -> int:
        return self._subscription_rejected_count

    def drain_pending_messages(self) -> tuple[MqttMessage, ...]:
        messages: list[MqttMessage] = []
        while not self.message_queue.empty():
            messages.append(self.message_queue.get_nowait())
        return tuple(messages)

    @property
    def subscriptions(self) -> tuple[Subscription, ...]:
        return self._subscription_manager.subscriptions

    async def add_subscription(self, subscription: Subscription) -> None:
        async with self._lifecycle_lock:
            await self._subscription_manager.add(subscription)

    async def remove_subscription(self, subscription: Subscription) -> None:
        async with self._lifecycle_lock:
            await self._subscription_manager.remove(subscription)
            self._prune_unsubscribed_topics()

    async def update_subscription(
        self,
        original_filter: str,
        subscription: Subscription,
    ) -> None:
        async with self._lifecycle_lock:
            await self._subscription_manager.update(original_filter, subscription)
            self._prune_unsubscribed_topics()

    async def reconnect(self) -> None:
        """Reconnect and restore all active subscriptions."""
        async with self._lifecycle_lock:
            await self._stop()
            await self._start()

    async def disconnect(self) -> None:
        """Disconnect from MQTT while preserving configured subscriptions."""
        await self.stop()

    async def connect(self) -> None:
        """Connect to MQTT and activate all configured subscriptions."""
        await self.start()

    async def publish(self, topic: str, payload: bytes) -> None:
        """Publish a payload through the active MQTT connection."""
        async with self._lifecycle_lock:
            await self._mqtt_gate.publish(topic, payload)

    async def messages(self) -> AsyncIterator[MqttMessage]:
        """Yield normalized messages after their current value has been stored."""
        while True:
            yield await self.message_queue.get()

    async def connection_statuses(self) -> AsyncIterator[ConnectionStatus]:
        """Yield future changes to the MQTT connection status."""
        while True:
            yield await self.connection_status_queue.get()

    async def _handle_connected(self) -> None:
        self._set_connection_status(ConnectionStatus.CONNECTED)
        if self._is_running:
            await self._subscription_manager.subscribe_once()

    def _handle_disconnected(self) -> None:
        self._subscription_manager.disconnect()

        if self._is_running and not self._is_stopping:
            self._set_connection_status(ConnectionStatus.RECONNECTING)
        else:
            self._set_connection_status(ConnectionStatus.DISCONNECTED)

    def _handle_subscription_result(self, reason_codes: Any) -> None:
        codes = reason_codes if isinstance(reason_codes, (list, tuple)) else ()
        self._subscription_rejected_count = sum(
            1 for code in codes if _reason_code_failed(code)
        )
        if not self._subscription_rejected_count:
            self._subscription_failure_count = 0

    def _set_connection_status(self, status: ConnectionStatus) -> None:
        if self.connection_status != status:
            if status == ConnectionStatus.CONNECTED:
                connected_at = self._clock()
                self.connected_at = connected_at
                if self.observation_started_at is None:
                    self.observation_started_at = connected_at
            self.connection_status = status
            self.connection_status_queue.put_nowait(status)

    def _prune_unsubscribed_topics(self) -> None:
        subscriptions = self.subscriptions
        removed: list[str] = []
        for current in self._current_topics.get_current_topics(self._broker_id):
            topic = current.message.topic
            if not any(
                mqtt_filter_matches(subscription.topic_filter, topic)
                for subscription in subscriptions
            ) and current.status is ObservationStatus.LIVE:
                removed.append(topic)
        self._message_recorder.remove_current_topics(self._broker_id, removed)


def _reason_code_failed(reason_code: Any) -> bool:
    is_failure = getattr(reason_code, "is_failure", None)
    if is_failure is not None:
        return bool(is_failure)
    try:
        return int(reason_code) >= 128
    except (TypeError, ValueError):
        return False
