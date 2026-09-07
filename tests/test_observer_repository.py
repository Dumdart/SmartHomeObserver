import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.mqtt_message import MqttMessage
from topicgate.core.models.mqtt_observation import (
    MqttObservation,
    ObservationSource,
)
from topicgate.core.models.observation_retention_policy import (
    ObservationRetentionPolicy,
)
from topicgate.core.models.observation_status import ObservationStatus
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.topic_message import TopicMessage
from topicgate.core.payload_limits import MAX_PENDING_MESSAGE_NOTIFICATIONS
from topicgate.infrastructure.repository.observer_mqtt_repository import (
    ObserverMqttRepository,
)


class MemoryCurrentTopics:
    def __init__(self) -> None:
        self._current: dict[tuple[object, str], CurrentTopic] = {}

    def record_message(self, message: TopicMessage) -> None:
        self._current[(message.broker_id, message.topic)] = CurrentTopic(
            message,
            ObservationStatus.LIVE,
        )

    def get_current_topics(self, broker_id) -> tuple[CurrentTopic, ...]:
        return tuple(
            current
            for (owner_id, _), current in self._current.items()
            if owner_id == broker_id
        )

    def get_current_topic(self, broker_id, topic) -> CurrentTopic | None:
        return self._current.get((broker_id, topic))

    def remove_current_topics(self, broker_id, topics) -> None:
        for topic in topics:
            self._current.pop((broker_id, topic), None)

    def remove_current_broker(self, broker_id) -> None:
        for key in tuple(self._current):
            if key[0] == broker_id:
                self._current.pop(key)


def build_repository(
    *,
    clock=None,
    broker_id=None,
    message_recorder=None,
) -> tuple[ObserverMqttRepository, MagicMock]:
    broker_id = broker_id or uuid4()
    current_topics = MemoryCurrentTopics()
    if message_recorder is None:
        message_recorder = current_topics
    else:
        message_recorder.record_message.side_effect = current_topics.record_message
        message_recorder.remove_current_topics.side_effect = (
            current_topics.remove_current_topics
        )
    manager = MagicMock()
    manager.activate = AsyncMock()
    manager.deactivate = AsyncMock()
    manager.add = AsyncMock()
    manager.remove = AsyncMock()
    manager.update = AsyncMock()
    manager.subscribe_once = AsyncMock()
    manager.disconnect = MagicMock()
    manager.subscriptions = ()
    health_sink = MagicMock()

    with patch(
        "topicgate.infrastructure.repository.observer_mqtt_repository.SubscriptionManager",
        return_value=manager,
    ):
        repository = ObserverMqttRepository(
            MqttConfig(host="broker", port=1883, username="", password=""),
            ["SmartHome/#"],
            clock=clock,
            broker_id=broker_id,
            message_recorder=message_recorder,
            current_topics=current_topics,
            health_sink=health_sink,
        )

    return repository, manager


def test_repository_returns_state_and_value_by_topic_path() -> None:
    repository, _ = build_repository()
    message = MqttMessage(
        "SmartHome/Huehnerstall/door/status", b"open", qos=1, retain=True
    )

    repository.handle_message(None, None, message)

    state = repository.get_state(message.topic)

    assert state is not None
    assert state.topic == message.topic
    assert repository.get_value(message.topic) == b"open"
    assert repository.get_state("SmartHome/Huehnerstall/door/missing") is None
    assert repository.get_value("SmartHome/missing") is None


def test_repository_reads_topic_values_from_current_topic_repository() -> None:
    repository, _ = build_repository()
    message = MqttMessage("SmartHome/door/status", b"open", qos=1, retain=False)

    repository.handle_message(None, None, message)

    assert repository.get_state(message.topic).payload == b"open"


def test_health_failure_does_not_interrupt_observation_delivery() -> None:
    captured = []
    repository, _ = build_repository()
    repository.health_sink.evaluate_observation.side_effect = RuntimeError(
        "evaluation failed"
    )
    repository._observation_sink = captured.append

    repository.handle_message(
        None,
        None,
        MqttMessage("SmartHome/door/status", b"open", qos=1, retain=False),
    )

    assert len(captured) == 1
    assert captured[0].payload == b"open"


def test_repository_truncates_before_updating_model_and_sink() -> None:
    captured = []
    repository, _ = build_repository()
    repository._retention_policy = lambda: ObservationRetentionPolicy(
        max_payload_bytes_per_topic=4
    )
    repository._observation_sink = captured.append

    repository.handle_message(
        None,
        None,
        MqttMessage("SmartHome/value", b"123456", 0, False),
    )

    state = repository.get_state("SmartHome/value")
    assert state is not None
    assert state.payload == b"1234"
    assert state.payload_size == 6
    assert state.observation_id is not None
    assert captured == [state]


def test_repository_records_processed_topic_message() -> None:
    broker_id = uuid4()
    recorder = MagicMock()
    repository, _ = build_repository(
        broker_id=broker_id,
        message_recorder=recorder,
    )

    repository.handle_message(
        None,
        None,
        MqttMessage("SmartHome/value", b"42", 1, True),
    )

    recorded = recorder.record_message.call_args.args[0]
    observation = repository.get_state("SmartHome/value")
    assert observation is not None
    assert recorded.broker_id == broker_id
    assert recorded.topic == observation.topic
    assert recorded.payload == observation.payload
    assert recorded.observation_id == observation.observation_id


def test_repository_counts_message_recording_failures() -> None:
    recorder = MagicMock()
    repository, _ = build_repository(message_recorder=recorder)
    recorder.record_message.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        repository.handle_message(
            None,
            None,
            MqttMessage("SmartHome/value", b"42", 1, True),
        )

    assert repository.recording_failure_count == 1


def test_repository_tracks_rejected_subscriptions() -> None:
    repository, _ = build_repository()
    rejected = SimpleNamespace(is_failure=True)
    accepted = SimpleNamespace(is_failure=False)

    repository._handle_subscription_result((accepted, rejected))

    assert repository.subscription_rejected_count == 1


def test_repository_reads_current_values_instead_of_supplied_model_values() -> None:
    stored_id = uuid4()
    replacement_id = uuid4()
    received_at = datetime.now(timezone.utc)
    stored = MqttObservation(
        "stored",
        "home/stored",
        b"stored",
        0,
        False,
        received_at,
        source=ObservationSource.STORED,
        observation_id=stored_id,
    )
    live = MqttObservation(
        "live",
        "home/live",
        b"live",
        0,
        False,
        received_at,
        source=ObservationSource.LIVE,
        observation_id=replacement_id,
    )
    repository, _ = build_repository()
    assert repository.get_state(stored.topic) is None
    assert repository.get_state(live.topic) is None


async def test_repository_updates_topic_state_before_publishing_message() -> None:
    async def scenario() -> None:
        repository, _ = build_repository()
        message = MqttMessage(
            "SmartHome/discovered/value", b"42", qos=0, retain=False
        )
        stream = repository.messages()

        repository.handle_message(None, None, message)

        assert repository.get_value(message.topic) == b"42"
        assert await anext(stream) == message
        await stream.aclose()

    await scenario()


def test_message_notification_backlog_is_bounded() -> None:
    repository, _ = build_repository()

    for index in range(MAX_PENDING_MESSAGE_NOTIFICATIONS + 1):
        repository.handle_message(
            None,
            None,
            MqttMessage("untrusted/topic", str(index).encode(), 0, False),
        )

    assert repository.message_queue.qsize() == MAX_PENDING_MESSAGE_NOTIFICATIONS
    assert repository.dropped_message_count == 1
    assert repository.message_queue.get_nowait().payload == b"1"
    assert repository.get_value("untrusted/topic") == str(
        MAX_PENDING_MESSAGE_NOTIFICATIONS
    ).encode()


async def test_repository_delegates_subscription_operations() -> None:
    async def scenario() -> None:
        repository, manager = build_repository()
        original = Subscription("SmartHome/old/#")
        replacement = Subscription("SmartHome/new/#", qos=2)
        manager.subscriptions = (original,)

        await repository.add_subscription(replacement)
        await repository.remove_subscription(original)
        await repository.update_subscription(original.topic_filter, replacement)

        assert repository.subscriptions == (original,)
        manager.add.assert_awaited_once_with(replacement)
        manager.remove.assert_awaited_once_with(original)
        manager.update.assert_awaited_once_with(original.topic_filter, replacement)

    await scenario()


async def test_removing_subscription_clears_uncovered_retained_state() -> None:
    async def scenario() -> None:
        repository, manager = build_repository()
        removed = Subscription("devices/#")
        remaining = Subscription("sensors/#")
        manager.subscriptions = (removed, remaining)
        repository.handle_message(
            None,
            None,
            MqttMessage("devices/untrusted", b"value", 0, False),
        )
        repository.handle_message(
            None,
            None,
            MqttMessage("sensors/temperature", b"21", 0, False),
        )

        async def remove(_subscription: Subscription) -> None:
            manager.subscriptions = (remaining,)

        manager.remove.side_effect = remove
        await repository.remove_subscription(removed)

        assert repository.get_state("devices/untrusted") is None
        assert repository.get_value("sensors/temperature") == b"21"
        assert repository.subscriptions == (remaining,)

    await scenario()


async def test_start_connects_then_activates_subscription_manager() -> None:
    async def scenario() -> None:
        repository, manager = build_repository()
        repository._mqtt_gate.start = AsyncMock()
        status_stream = repository.connection_statuses()

        await repository.start()

        repository._mqtt_gate.start.assert_awaited_once()
        manager.activate.assert_awaited_once()
        assert repository.connection_status == ConnectionStatus.CONNECTED
        assert await anext(status_stream) == ConnectionStatus.CONNECTING
        assert await anext(status_stream) == ConnectionStatus.CONNECTED
        await status_stream.aclose()

    await scenario()


async def test_repository_tracks_connection_and_observation_windows() -> None:
    first_connection = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    second_connection = datetime(2026, 8, 17, 10, 5, tzinfo=timezone.utc)
    connection_times = iter((first_connection, second_connection))
    repository, _ = build_repository(clock=lambda: next(connection_times))
    repository._mqtt_gate.start = AsyncMock()
    repository._mqtt_gate.stop = AsyncMock()

    await repository.start()
    await repository.stop()
    await repository.start()

    assert repository.connected_at == second_connection
    assert repository.observation_started_at == first_connection


async def test_stop_deactivates_manager_before_stopping_mqtt_gate() -> None:
    async def scenario() -> None:
        repository, manager = build_repository()
        repository._is_running = True
        events: list[str] = []

        async def deactivate() -> None:
            events.append("deactivate")

        async def stop() -> None:
            events.append("stop")

        manager.deactivate.side_effect = deactivate
        repository._mqtt_gate.stop = AsyncMock(side_effect=stop)

        await repository.stop()

        assert events == ["deactivate", "stop"]
        assert repository.connection_status == ConnectionStatus.DISCONNECTED

    await scenario()


async def test_connected_and_disconnected_callbacks_delegate_to_manager() -> None:
    async def scenario() -> None:
        repository, manager = build_repository()
        repository._is_running = True

        await repository._handle_connected()
        repository._handle_disconnected()

        manager.subscribe_once.assert_awaited_once()
        manager.disconnect.assert_called_once()
        assert repository.connection_status == ConnectionStatus.RECONNECTING

    await scenario()


async def test_startup_failure_stops_gate_and_leaves_repository_disconnected() -> None:
    async def scenario() -> None:
        repository, manager = build_repository()
        repository._mqtt_gate.start = AsyncMock(
            side_effect=RuntimeError("broker unavailable")
        )
        repository._mqtt_gate.stop = AsyncMock()

        try:
            await repository.start()
        except ConnectionError:
            pass
        else:
            raise AssertionError("Expected startup to fail")

        repository._mqtt_gate.stop.assert_awaited_once()
        manager.activate.assert_not_awaited()
        assert repository.connection_status == ConnectionStatus.DISCONNECTED

    await scenario()


async def test_update_broker_replaces_gate_and_manager_preserving_subscriptions() -> None:
    async def scenario() -> None:
        repository, previous_manager = build_repository()
        subscription = Subscription("SmartHome/door/#", qos=2)
        previous_manager.subscriptions = (subscription,)
        repository._is_running = True
        previous_gate = repository._mqtt_gate
        previous_gate.stop = AsyncMock()

        replacement_gate = MagicMock()
        replacement_gate.is_started = False
        replacement_gate.start = AsyncMock()
        replacement_gate.stop = AsyncMock()
        replacement_manager = MagicMock()
        replacement_manager.activate = AsyncMock()

        new_config = MqttConfig(
            host="new-broker",
            port=8883,
            username="observer",
            password="secret",
            use_tls=True,
        )
        with (
            patch(
                "topicgate.infrastructure.repository.observer_mqtt_repository.MqttGate",
                return_value=replacement_gate,
            ) as mqtt_gate,
            patch(
                "topicgate.infrastructure.repository.observer_mqtt_repository.SubscriptionManager",
                return_value=replacement_manager,
            ) as subscription_manager,
        ):
            await repository.update_broker(new_config)

        previous_manager.deactivate.assert_awaited_once()
        previous_gate.stop.assert_awaited_once()
        mqtt_gate.assert_called_once()
        assert mqtt_gate.call_args.args[0] == new_config
        assert mqtt_gate.call_args.args[2] == [subscription]
        subscription_manager.assert_called_once_with(
            replacement_gate,
            repository.handle_message,
        )
        replacement_gate.start.assert_awaited_once()
        replacement_manager.activate.assert_awaited_once()

    await scenario()


async def test_update_broker_replaces_subscriptions_for_the_selected_profile() -> None:
    async def scenario() -> None:
        repository, previous_manager = build_repository()
        previous_manager.subscriptions = (Subscription("SmartHome/default/#"),)
        profile_subscriptions = (Subscription("bridge/#", qos=2),)
        replacement_gate = MagicMock()
        replacement_gate.is_started = False
        replacement_gate.start = AsyncMock()
        replacement_gate.stop = AsyncMock()
        replacement_manager = MagicMock()
        replacement_manager.activate = AsyncMock()

        with (
            patch(
                "topicgate.infrastructure.repository.observer_mqtt_repository.MqttGate",
                return_value=replacement_gate,
            ) as mqtt_gate,
            patch(
                "topicgate.infrastructure.repository.observer_mqtt_repository.SubscriptionManager",
                return_value=replacement_manager,
            ),
        ):
            await repository.update_broker(
                MqttConfig("local", 1883, "", ""),
                subscriptions=profile_subscriptions,
            )

        assert mqtt_gate.call_args.args[2] == list(profile_subscriptions)

    await scenario()


async def test_update_broker_connects_when_previously_disconnected() -> None:
    async def scenario() -> None:
        repository, _ = build_repository()
        replacement_gate = MagicMock()
        replacement_gate.is_started = False
        replacement_gate.start = AsyncMock()
        replacement_gate.stop = AsyncMock()
        replacement_manager = MagicMock()
        replacement_manager.activate = AsyncMock()

        with (
            patch(
                "topicgate.infrastructure.repository.observer_mqtt_repository.MqttGate",
                return_value=replacement_gate,
            ),
            patch(
                "topicgate.infrastructure.repository.observer_mqtt_repository.SubscriptionManager",
                return_value=replacement_manager,
            ),
        ):
            await repository.update_broker(
                MqttConfig("new-broker", 1883, "", "")
            )

        replacement_gate.start.assert_awaited_once()
        replacement_manager.activate.assert_awaited_once()
        assert repository.connection_status == ConnectionStatus.CONNECTED

    await scenario()


async def test_failed_broker_update_restores_the_previous_connection() -> None:
    async def scenario() -> None:
        repository, previous_manager = build_repository()
        repository._is_running = True
        previous_gate = repository._mqtt_gate
        previous_gate.start = AsyncMock()
        previous_gate.stop = AsyncMock()
        replacement_gate = MagicMock()
        replacement_gate.is_started = False
        replacement_gate.start = AsyncMock(side_effect=RuntimeError("unavailable"))
        replacement_gate.stop = AsyncMock()
        replacement_manager = MagicMock()
        replacement_manager.activate = AsyncMock()

        with (
            patch(
                "topicgate.infrastructure.repository.observer_mqtt_repository.MqttGate",
                return_value=replacement_gate,
            ),
            patch(
                "topicgate.infrastructure.repository.observer_mqtt_repository.SubscriptionManager",
                return_value=replacement_manager,
            ),
        ):
            try:
                await repository.update_broker(MqttConfig("new-broker", 1883, "", ""))
            except ConnectionError:
                pass
            else:
                raise AssertionError("Expected the broker update to fail")

        assert repository._mqtt_gate is previous_gate
        assert repository._subscription_manager is previous_manager
        previous_gate.start.assert_awaited_once()
        assert repository.connection_status == ConnectionStatus.CONNECTED

    await scenario()
