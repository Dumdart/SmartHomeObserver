import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from topicgate.app.broker_runtime_state import BrokerRuntimeState
from topicgate.app.services.broker_profile_service import BrokerProfileService
from topicgate.app.services.observation_query_service import ObservationQueryService
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.broker_profile import BrokerProfile
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.message_filter import MessageFilter
from topicgate.core.models.mqtt_message import MqttMessage
from topicgate.core.models.observation_cache_administration import (
    BrokerCacheUsage,
    CacheUsageSummary,
    PersistedTopicSummary,
)
from topicgate.core.models.mqtt_observation import MqttObservation as TopicState
from topicgate.core.models.observation_status import ObservationStatus
from topicgate.core.models.observer_workspace import ObserverWorkspace
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.topic_message import TopicMessage
from topicgate.infrastructure.repository.observer_mqtt_repository import (
    ObserverMqttRepository,
)


def profile(name: str, host: str = "broker") -> BrokerProfile:
    profile_id = uuid4()
    workspace = ObserverWorkspace(
        id=uuid4(),
        profile_id=profile_id,
    )
    return BrokerProfile(
        profile_id,
        name,
        MqttConfig(host, 1883, "", ""),
        workspace.id,
        workspace,
    )


def runtime_with(
    profiles: tuple[BrokerProfile, ...],
    observation_cache=None,
    observation_query=None,
    current_topics=None,
) -> tuple[TopicGateRuntime, MagicMock, MagicMock]:
    brokers = MagicMock()
    active_id = profiles[0].id
    stored = {item.id: item for item in profiles}
    brokers.get_all_profiles.side_effect = lambda: tuple(stored.values())
    brokers.get_profile.side_effect = lambda broker_id=None: stored[
        active_id if broker_id is None else broker_id
    ]
    brokers.create_profile.side_effect = lambda name, config: profile(name, config.host)
    brokers.delete_profile.side_effect = lambda broker_id: stored.pop(broker_id)

    def activate(broker_id, mqtt=None) -> None:
        nonlocal active_id
        if mqtt is not None:
            stored[broker_id].config = mqtt
        active_id = broker_id

    brokers.select_active_profile.side_effect = activate
    mqtt = MagicMock()
    mqtt.start = AsyncMock()
    mqtt.stop = AsyncMock()
    mqtt.connect = AsyncMock()
    mqtt.disconnect = AsyncMock()
    mqtt.reconnect = AsyncMock()
    mqtt.update_broker = AsyncMock()
    mqtt.add_subscription = AsyncMock()
    mqtt.update_subscription = AsyncMock()
    mqtt.remove_subscription = AsyncMock()
    mqtt.publish = AsyncMock()
    mqtt.subscriptions = ()
    mqtt.connection_status = "disconnected"
    mqtt.dropped_message_count = 0
    mqtt.topic_update_interval = 0.1
    repositories = {item.id: mqtt for item in profiles}
    current_topics = current_topics or MagicMock()
    current_topics.get_current_topics.return_value = ()
    current_topics.get_current_topic.return_value = None
    return (
        TopicGateRuntime(
            brokers,
            repositories,
            active_id,
            lambda _profile: mqtt,
            observation_cache,
            observation_query=observation_query,
            current_topics=current_topics,
        ),
        brokers,
        mqtt,
    )


async def test_runtime_owns_the_mqtt_lifecycle() -> None:
    async def scenario() -> None:
        runtime, brokers, mqtt = runtime_with((profile("Default"),))

        await runtime.start()
        await runtime.stop()

        mqtt.start.assert_awaited_once_with()
        mqtt.stop.assert_awaited_once_with()

    await scenario()


def test_runtime_sanitizes_broker_credentials_exposed_to_callers() -> None:
    configured = profile("Configured")
    configured.config = MqttConfig(
        "broker.example",
        8883,
        "observer",
        "os-loaded-secret",
        True,
    )
    runtime, brokers, _ = runtime_with((configured,))

    listed = runtime.list_brokers()[0]
    selected = runtime.get_broker(configured.id)

    assert listed.config.password == ""
    assert listed.password_configured
    assert selected.config.password == ""
    assert selected.password_configured
    assert runtime.mqtt_config.password == ""
    assert brokers.get_profile(configured.id).config.password == "os-loaded-secret"


def test_runtime_reports_when_a_broker_password_is_not_configured() -> None:
    runtime, _, _ = runtime_with((profile("No password"),))

    summary = runtime.get_broker()

    assert summary.config.password == ""
    assert not summary.password_configured


def test_runtime_preserves_a_hidden_password_when_broker_settings_change() -> None:
    configured = profile("Configured")
    configured.config = MqttConfig(
        "old-broker.example",
        1883,
        "observer",
        "os-loaded-secret",
    )
    runtime, brokers, _ = runtime_with((configured,))

    runtime.update_broker(
        configured.id,
        MqttConfig("new-broker.example", 8883, "observer", "", True),
    )

    saved = brokers.get_profile(configured.id).config
    assert saved.host == "new-broker.example"
    assert saved.password == "os-loaded-secret"


async def test_runtime_connects_with_a_stored_password_from_a_broker_summary() -> None:
    async def scenario() -> None:
        configured = profile("Configured")
        configured.config = MqttConfig(
            "broker.example",
            1883,
            "observer",
            "os-loaded-secret",
        )
        runtime, _, mqtt = runtime_with((configured,))
        sanitized_config = runtime.get_broker(configured.id).config

        await runtime.activate_broker(configured.id, sanitized_config)

        connected_config = mqtt.update_broker.await_args.args[0]
        assert connected_config.password == "os-loaded-secret"

    await scenario()


async def test_runtime_activates_and_persists_a_broker_only_after_connecting() -> None:
    async def scenario() -> None:
        default = profile("Default")
        selected = profile("Local", "local")
        runtime, brokers, mqtt = runtime_with((default, selected))
        replacement = MqttConfig("secure", 8883, "user", "secret", True)

        active = await runtime.activate_broker(
            selected.id,
            replacement,
            "Local TLS",
        )

        mqtt.update_broker.assert_awaited_once_with(
            replacement,
            subscriptions=selected.workspace.subscriptions,
        )
        brokers.update_profile.assert_called_once_with(selected)
        brokers.select_active_profile.assert_called_once_with(selected.id)
        assert active.id == selected.id
        assert active.name == "Local TLS"

    await scenario()


async def test_runtime_preserves_topic_states_across_broker_switches() -> None:
    async def scenario() -> None:
        default = profile("Default")
        selected = profile("Local", "local")
        _, brokers, _ = runtime_with((default, selected))
        current_topics = MemoryCurrentTopics()
        health_sink = MagicMock()
        default_repo = ObserverMqttRepository(
            default.config,
            [],
            broker_id=default.id,
            message_recorder=current_topics,
            current_topics=current_topics,
            health_sink=health_sink,
        )
        selected_repo = ObserverMqttRepository(
            selected.config,
            [],
            broker_id=selected.id,
            message_recorder=current_topics,
            current_topics=current_topics,
            health_sink=health_sink,
        )
        default_repo.handle_message(
            None,
            None,
            MqttMessage("home/status", b"open", 0, False),
        )
        selected_repo.handle_message(
            None,
            None,
            MqttMessage("garage/status", b"closed", 0, False),
        )
        default_repo.start = AsyncMock()
        default_repo.stop = AsyncMock()
        default_repo.update_broker = AsyncMock()
        selected_repo.start = AsyncMock()
        selected_repo.stop = AsyncMock()
        selected_repo.update_broker = AsyncMock()
        runtime = TopicGateRuntime(
            brokers,
            {
                default.id: default_repo,
                selected.id: selected_repo,
            },
            default.id,
            current_topics=current_topics,
        )

        await runtime.activate_broker(selected.id)
        await runtime.activate_broker(default.id)

        assert runtime.active_repo is default_repo
        assert runtime.get_topic_state(default.id, "home/status").payload == b"open"
        assert (
            runtime.get_topic_state(selected.id, "garage/status").payload
            == b"closed"
        )
        default_repo.stop.assert_awaited_once_with()
        selected_repo.stop.assert_awaited_once_with()

    await scenario()


async def test_runtime_does_not_persist_a_failed_broker_activation() -> None:
    async def scenario() -> None:
        selected = profile("Default")
        runtime, brokers, mqtt = runtime_with((selected,))
        mqtt.update_broker.side_effect = ConnectionError("unavailable")

        try:
            await runtime.activate_broker(
                selected.id,
                MqttConfig("new", 1883, "", ""),
            )
        except ConnectionError:
            pass
        else:
            raise AssertionError("Expected broker activation to fail")

        brokers.update_profile.assert_not_called()
        brokers.select_active_profile.assert_not_called()

    await scenario()


async def test_failed_broker_switch_restarts_the_previous_repository() -> None:
    async def scenario() -> None:
        default = profile("Default")
        selected = profile("Local", "local")
        _, brokers, _ = runtime_with((default, selected))
        default_repo = MagicMock()
        default_repo.stop = AsyncMock()
        default_repo.start = AsyncMock()
        selected_repo = MagicMock()
        selected_repo.update_broker = AsyncMock(
            side_effect=ConnectionError("unavailable")
        )
        runtime = TopicGateRuntime(
            brokers,
            {
                default.id: default_repo,
                selected.id: selected_repo,
            },
            default.id,
        )

        try:
            await runtime.activate_broker(selected.id)
        except ConnectionError:
            pass
        else:
            raise AssertionError("Expected broker activation to fail")

        assert runtime.active_repo is default_repo
        default_repo.stop.assert_awaited_once_with()
        default_repo.start.assert_awaited_once_with()
        brokers.select_active_profile.assert_not_called()

    await scenario()


async def test_runtime_persists_subscription_changes_to_the_active_workspace() -> None:
    async def scenario() -> None:
        active = profile("Default")
        runtime, brokers, mqtt = runtime_with((active,))
        subscription = Subscription("home/#")
        mqtt.subscriptions = (subscription,)

        await runtime.add_subscription(active.id, subscription)
        updated = Subscription("house/#", qos=2)
        mqtt.subscriptions = (updated,)
        await runtime.update_subscription(
            active.id,
            subscription.topic_filter,
            updated,
        )
        mqtt.subscriptions = ()
        await runtime.remove_subscription(active.id, updated)

        mqtt.add_subscription.assert_awaited_once_with(subscription)
        mqtt.update_subscription.assert_awaited_once_with(
            subscription.topic_filter,
            updated,
        )
        mqtt.remove_subscription.assert_awaited_once_with(updated)
        assert active.workspace.subscriptions == ()
        assert brokers.replace_subscriptions.call_count == 3

    await scenario()


async def test_runtime_exposes_topic_queries_connection_commands_and_publish() -> None:
    async def scenario() -> None:
        active = profile("Default")
        state = TopicState(
            name="status",
            topic="home/status",
            payload=b"on",
            qos=1,
            retain=False,
            recieved_at=datetime.now(timezone.utc),
        )
        current_topics = _current_reader(_current(active.id, state))
        runtime, _, mqtt = runtime_with(
            (active,),
            current_topics=current_topics,
        )

        projected = runtime.get_topic_state(active.id, "home/status")
        assert projected is not None
        assert projected.payload == state.payload
        mqtt.get_state.assert_not_called()
        await runtime.connect()
        await runtime.reconnect()
        await runtime.disconnect()
        await runtime.publish(active.id, "home/set", b"off")

        mqtt.connect.assert_awaited_once_with()
        mqtt.reconnect.assert_awaited_once_with()
        mqtt.disconnect.assert_awaited_once_with()
        mqtt.publish.assert_awaited_once_with("home/set", b"off")

    await scenario()


def test_runtime_delegates_stored_observation_queries() -> None:
    active = profile("Default")
    _, brokers, mqtt = runtime_with((active,))
    query = MagicMock(spec=ObservationQueryService)
    message_id = uuid4()
    message_filter = MessageFilter(active.id)
    message = MagicMock(spec=TopicMessage)
    persisted_topics = (MagicMock(spec=PersistedTopicSummary),)
    usage = CacheUsageSummary((BrokerCacheUsage(active.id, 1, 2, None, None),))
    query.get_message.return_value = message
    query.query_stored_observations.return_value = (message,)
    query.get_cache_usage.return_value = usage
    query.get_persisted_topics.return_value = persisted_topics
    runtime = TopicGateRuntime(
        brokers,
        {active.id: mqtt},
        active.id,
        observation_query=query,
    )

    assert runtime.get_message(message_id) is message
    assert runtime.query_stored_observations(message_filter) == (message,)
    assert runtime.get_cache_usage() == usage
    assert runtime.list_persisted_topics(active.id) == persisted_topics

    query.get_message.assert_called_once_with(message_id)
    query.query_stored_observations.assert_called_once_with(message_filter)
    query.get_cache_usage.assert_called_once_with()
    query.get_persisted_topics.assert_called_once_with(active.id, ())


def test_runtime_scopes_observation_storage_summary_to_a_broker() -> None:
    active = profile("Default")
    other = profile("Other")
    query = MagicMock(spec=ObservationQueryService)
    active_usage = BrokerCacheUsage(active.id, 2, 12, None, None)
    other_usage = BrokerCacheUsage(other.id, 1, 6, None, None)
    query.get_cache_usage.return_value = CacheUsageSummary(
        (active_usage, other_usage)
    )
    runtime, _, _ = runtime_with(
        (active, other),
        observation_query=query,
    )

    assert runtime.get_observation_storage_summary() == CacheUsageSummary(
        (active_usage, other_usage)
    )
    assert runtime.get_observation_storage_summary(other.id) == CacheUsageSummary(
        (other_usage,)
    )


def test_runtime_exposes_mqtt_event_streams() -> None:
    active = profile("Default")
    runtime, _, mqtt = runtime_with((active,))
    message_stream = object()
    connection_stream = object()
    mqtt.messages.return_value = message_stream
    mqtt.connection_statuses.return_value = connection_stream

    assert runtime.messages() is message_stream
    assert runtime.connection_statuses() is connection_stream


def test_runtime_exposes_broker_specific_snapshot_inputs() -> None:
    first = profile("First")
    second = profile("Second")
    _, brokers, _ = runtime_with((first, second))
    first_repo = MagicMock()
    first_repo.get_all_topics.return_value = ("first/topic",)
    first_repo.connection_status = "connected"
    first_repo.dropped_message_count = 2
    first_repo.topic_update_interval = 0.25
    first_repo.connected_at = datetime(2026, 8, 17, tzinfo=timezone.utc)
    first_repo.observation_started_at = datetime(
        2026, 8, 16, tzinfo=timezone.utc
    )
    second_repo = MagicMock()
    current_topics = _current_reader(
        _current(first.id, _state("first/topic")),
        _current(second.id, _state("second/topic")),
    )
    runtime = TopicGateRuntime(
        brokers,
        {first.id: first_repo, second.id: second_repo},
        first.id,
        current_topics=current_topics,
    )

    assert runtime.list_topics(first.id) == ("first/topic",)
    assert runtime.list_topics(second.id) == ("second/topic",)
    assert runtime.get_connection_status(first.id) == "connected"
    assert runtime.get_dropped_message_count(first.id) == 2
    assert runtime.get_topic_update_interval(first.id) == 0.25
    assert runtime.get_connected_at(first.id) == first_repo.connected_at
    assert (
        runtime.get_observation_started_at(first.id)
        == first_repo.observation_started_at
    )


def test_runtime_exposes_preview_and_confirmed_cache_deletion() -> None:
    active = profile("Default")
    cache = MagicMock()
    preview = MagicMock()
    preview.broker_id = active.id
    cache.preview_clear_cache.return_value = preview
    cache.preview_unsubscribed.return_value = preview
    cache.confirm_deletion.return_value = 3
    runtime, _, _ = runtime_with((active,), cache)

    assert runtime.preview_clear_cache(active.id) is preview
    assert runtime.preview_unsubscribed_cache(active.id) is preview
    assert runtime.confirm_cache_deletion(preview) == 3
    cache.preview_clear_cache.assert_called_once_with(active.id, None)
    cache.preview_unsubscribed.assert_called_once_with(
        active.id,
        runtime.list_subscriptions(active.id),
    )
    cache.confirm_deletion.assert_called_once_with(preview)


def test_runtime_reads_current_values_from_current_repository() -> None:
    active = profile("Default")
    observer = MagicMock()
    observer.subscriptions = (Subscription("home/#"),)
    current = _current(active.id, _state("home/status", b"canonical"))
    reader = _current_reader(current)
    brokers = MagicMock()
    brokers.get_profile.return_value = active
    runtime = TopicGateRuntime(
        brokers,
        {active.id: observer},
        active.id,
        current_topics=reader,
    )

    topics = runtime.get_current_topics(active.id)

    assert tuple(item.message.topic for item in topics) == ("home/status",)
    assert topics[0].message.payload == b"canonical"


async def test_runtime_creates_updates_and_deletes_broker_profiles() -> None:
    async def scenario() -> None:
        default = profile("Default")
        removable = profile("Remote")
        runtime, brokers, _ = runtime_with((default, removable))
        created = runtime.create_broker(
            "New",
            MqttConfig("new", 1883, "", ""),
        )
        updated = runtime.update_broker(
            removable.id,
            MqttConfig("remote", 1883, "", ""),
            "Renamed",
        )
        deleted = await runtime.delete_broker(removable.id)

        brokers.create_profile.assert_called_once()
        brokers.update_profile.assert_called_once_with(removable)
        assert created.name == "New"
        assert updated.name == "Renamed"
        assert deleted.id == removable.id

    await scenario()


async def test_runtime_deletes_active_broker_with_shared_runtime_state(
    credential_store,
) -> None:
    state = BrokerRuntimeState()
    brokers = BrokerProfileService(
        credential_store=credential_store,
        runtime_state=state,
    )
    active, replacement = brokers.get_all_profiles()
    repositories = {
        profile.id: MagicMock(
            stop=AsyncMock(),
            start=AsyncMock(),
            update_broker=AsyncMock(),
        )
        for profile in (active, replacement)
    }
    state.repositories.update(repositories)
    runtime = TopicGateRuntime(brokers, state.repositories, active.id)

    deleted = await runtime.delete_broker(active.id)

    assert deleted.id == active.id
    assert runtime.active_broker.id == replacement.id
    assert active.id not in state.repositories
    assert tuple(profile.id for profile in brokers.get_all_profiles()) == (
        replacement.id,
    )


async def test_runtime_flushes_observations_before_deleting_a_broker() -> None:
    active = profile("Default")
    removable = profile("Remote")
    cache = MagicMock()
    runtime, brokers, _ = runtime_with((active, removable), cache)

    await runtime.delete_broker(removable.id)

    cache.flush_pending_writes.assert_called_once_with()
    brokers.delete_profile.assert_called_once_with(removable.id)


def _state(topic: str, payload: bytes = b"value") -> TopicState:
    return TopicState(
        name=topic.rsplit("/", 1)[-1],
        topic=topic,
        payload=payload,
        qos=1,
        retain=False,
        recieved_at=datetime.now(timezone.utc),
    )


def _current(broker_id, state: TopicState) -> CurrentTopic:
    return CurrentTopic(
        TopicMessage(
            broker_id=broker_id,
            topic=state.topic,
            payload=state.payload,
            qos=state.qos,
            retain=state.retain,
            received_at=state.received_at,
            payload_size=state.payload_size or len(state.payload),
            message_count=state.message_count,
            observation_id=state.observation_id or uuid4(),
        ),
        ObservationStatus.LIVE,
    )


def _current_reader(*topics: CurrentTopic) -> MagicMock:
    reader = MagicMock()
    reader.get_current_topics.side_effect = lambda broker_id: tuple(
        current
        for current in topics
        if current.message.broker_id == broker_id
    )
    reader.get_current_topic.side_effect = lambda broker_id, topic: next(
        (
            current
            for current in topics
            if current.message.broker_id == broker_id
            and current.message.topic == topic
        ),
        None,
    )
    return reader


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
