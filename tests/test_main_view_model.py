import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from topicgate.core.models.mqtt_message import MqttMessage
from topicgate.core.models.message_filter import MessageFilter, OrderType
from topicgate.core.models.broker_profile import BrokerProfile
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.mqtt_observation import MqttObservation as TopicState
from topicgate.core.models.observer_workspace import ObserverWorkspace
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.observation_status import ObservationStatus
from topicgate.core.models.topic_message import TopicMessage
from topicgate.core.models.observation_cache_administration import (
    BrokerCacheUsage,
    CacheUsageSummary,
    ObservationDeletionResult,
)
from topicgate.core.models.support_bundle import SupportBundleArtifacts
from topicgate.core.models.observation_deletion_preview import (
    ObservationDeletionEntry,
    ObservationDeletionPreview,
)
from topicgate.core.models.observation_retention_policy import (
    ObservationRetentionPolicy,
)
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.app.services.broker_snapshot_service import BrokerSnapshotService
from topicgate.core.models.health import EqualCondition, TopicTarget
from topicgate.core.models.health import FreshnessCondition
from topicgate.core.models.health import NumericRangeCondition
from topicgate.core.models.health import TopicAbsentCondition
from topicgate.core.models.health import TopicExistsCondition
from topicgate.core.models.health.condition_kind import ConditionKind
from topicgate.gui.main_view_model import MainViewModel, mqtt_filter_matches
from topicgate.presentation.snapshot_presentation import SnapshotQuery
from topicgate.core.payload_limits import (
    MAX_FORMATTED_JSON_CHARACTERS,
    MAX_RENDERED_PAYLOAD_BYTES,
)


def test_expectation_editor_state_maps_topic_payloads_and_refreshes_health() -> None:
    runtime = runtime_for(FakeObserverRepository())
    management = MagicMock()
    management.list_expectations.return_value = ()
    management.create_expectation.side_effect = lambda item, **_kwargs: item
    health_query = MagicMock()
    health_query.get_health_report.return_value = MagicMock()
    view_model = MainViewModel(
        runtime,
        "devices/status",
        health_query_service=health_query,
        expectation_management_service=management,
    )

    created = view_model.save_expectation(
        target_kind="topic",
        expectation_id=None,
        name=" Device status ",
        description=" Expected online ",
        expected_values="b25saW5l",
        encoding="base64",
    )

    assert created.name == "Device status"
    assert created.description == "Expected online"
    assert created.target == TopicTarget(runtime.active_broker.id, "devices/status")
    assert created.condition == EqualCondition(b"online")
    health_query.get_health_report.assert_called_once_with(runtime.active_broker.id)


def test_expectation_editor_rejects_wildcard_topic_targets() -> None:
    view_model = MainViewModel(
        runtime_for(FakeObserverRepository()),
        "devices/#",
        expectation_management_service=MagicMock(),
    )

    with pytest.raises(ValueError, match="exact MQTT topic"):
        view_model.save_expectation(
            target_kind="topic",
            expectation_id=None,
            name="Status",
            description="",
            expected_values="online",
        )


def test_health_refresh_round_trips_latest_checkpoint_for_active_broker() -> None:
    runtime = runtime_for(FakeObserverRepository())
    broker_id = runtime.active_broker.id
    baseline = MagicMock()
    replacement = MagicMock()
    health_query = MagicMock()
    health_query.get_health_report.side_effect = (
        MagicMock(broker_id=broker_id, checkpoint=baseline),
        MagicMock(broker_id=broker_id, checkpoint=replacement),
    )
    view_model = MainViewModel(runtime, health_query_service=health_query)

    view_model.refresh_health()
    report = view_model.refresh_health()

    assert report.checkpoint is replacement
    assert health_query.get_health_report.call_args_list == [
        call(broker_id),
        call(broker_id, checkpoint=baseline),
    ]


@pytest.mark.parametrize(
    ("condition_kind", "expected_values", "expected_type"),
    [
        (ConditionKind.NUMERIC_RANGE, ("1.5", "3"), NumericRangeCondition),
        (ConditionKind.TOPIC_EXISTS, (), TopicExistsCondition),
        (ConditionKind.TOPIC_ABSENT, (), TopicAbsentCondition),
        (ConditionKind.FRESH_WITHIN, ("60",), FreshnessCondition),
    ],
)
def test_expectation_editor_state_builds_additional_topic_conditions(
    condition_kind,
    expected_values,
    expected_type,
) -> None:
    runtime = runtime_for(FakeObserverRepository())
    management = MagicMock()
    management.list_expectations.return_value = ()
    management.create_expectation.side_effect = lambda item, **_kwargs: item
    view_model = MainViewModel(
        runtime,
        "devices/status",
        expectation_management_service=management,
    )

    created = view_model.save_expectation(
        target_kind="topic",
        expectation_id=None,
        name="Condition",
        description="",
        condition_kind=condition_kind,
        expected_values=expected_values,
        encoding="base64",
    )

    assert isinstance(created.condition, expected_type)


def test_expectation_editor_state_rejects_topic_only_broker_condition() -> None:
    view_model = MainViewModel(
        runtime_for(FakeObserverRepository()),
        expectation_management_service=MagicMock(),
    )

    with pytest.raises(ValueError, match="require a topic target"):
        view_model.save_expectation(
            target_kind="broker",
            expectation_id=None,
            name="Fresh broker",
            description="",
            condition_kind=ConditionKind.FRESH_WITHIN,
            expected_values=("60",),
        )


async def test_publish_message_supports_utf8_and_strict_base64() -> None:
    repository = FakeObserverRepository()
    runtime = runtime_for(repository)
    published: list[tuple[UUID, str, bytes]] = []

    async def publish(broker_id: UUID, topic: str, payload: bytes) -> None:
        published.append((broker_id, topic, payload))

    runtime.publish = publish  # type: ignore[method-assign]
    view_model = MainViewModel(runtime)

    await view_model.publish_message("home/set", "på", "utf-8")
    await view_model.publish_message("camera/set", "/wA=", "base64")

    assert published[0][1:] == ("home/set", "på".encode())
    assert published[1][1:] == ("camera/set", b"\xff\x00")
    try:
        await view_model.publish_message("camera/set", "%%%", "base64")
    except ValueError as error:
        assert str(error) == "Payload is not valid base64."
    else:
        raise AssertionError("Expected invalid base64 to be rejected")


async def test_broker_lifecycle_operations_do_not_overlap() -> None:
    runtime = runtime_for(FakeObserverRepository())
    view_model = MainViewModel(runtime)
    profile = view_model.active_broker_profile

    async with view_model._operation("connection"):
        try:
            await view_model.activate_broker_profile(profile.id, profile.config)
        except RuntimeError as error:
            assert "already in progress" in str(error)
        else:
            raise AssertionError("Expected overlapping lifecycle operation to fail")


async def test_support_bundle_export_runs_collection_and_archive_work_in_threads(
    tmp_path: Path,
) -> None:
    runtime = runtime_for(FakeObserverRepository())
    artifacts = SupportBundleArtifacts(
        "{}\n",
        "# Bundle\n",
        "{}\n",
        ("Diagnostics omitted by bounds: 2.",),
    )
    exporter = MagicMock()
    exporter.export.return_value = artifacts
    writer = MagicMock()
    destination = tmp_path / "support.zip"
    writer.write.return_value = destination
    view_model = MainViewModel(
        runtime,
        support_bundle_exporter=exporter,
        support_bundle_archive_writer=writer,
    )

    async def run_in_place(function, *args):
        return function(*args)

    with patch(
        "topicgate.gui.main_view_model.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=run_in_place,
    ) as to_thread:
        result = await view_model.export_support_bundle(destination)

    assert result.destination == destination
    assert result.warnings == artifacts.warnings
    assert to_thread.await_count == 2
    options = exporter.export.call_args.args[0]
    assert options.include_payloads is False
    writer.write.assert_called_once_with(destination, artifacts)
    assert not view_model.is_busy("support-bundle")


async def test_support_bundle_exporter_errors_leave_no_archive_and_clear_busy(
    tmp_path: Path,
) -> None:
    exporter = MagicMock()
    exporter.export.side_effect = RuntimeError("collection failed")
    writer = MagicMock()
    view_model = MainViewModel(
        runtime_for(FakeObserverRepository()),
        support_bundle_exporter=exporter,
        support_bundle_archive_writer=writer,
    )

    with pytest.raises(RuntimeError, match="collection failed"):
        await view_model.export_support_bundle(tmp_path / "support.zip")

    writer.write.assert_not_called()
    assert not view_model.is_busy("support-bundle")


async def test_stored_observation_query_builds_filter_and_runs_in_thread() -> None:
    runtime = runtime_for(FakeObserverRepository())
    broker_id = runtime.active_broker.id
    after = datetime(2026, 8, 1, tzinfo=timezone.utc)
    before = datetime(2026, 8, 2, tzinfo=timezone.utc)
    message = TopicMessage(
        broker_id,
        "home/kitchen/temperature",
        b"21.5",
        1,
        False,
        before,
        4,
        7,
        uuid4(),
    )
    query = MagicMock(return_value=(message,))
    runtime.query_stored_observations = query  # type: ignore[method-assign]

    async def run_in_place(function, *args):
        return function(*args)

    with patch(
        "topicgate.gui.main_view_model.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=run_in_place,
    ) as to_thread:
        results = await MainViewModel(runtime).query_stored_observations(
            broker_id,
            "home/+/temperature",
            after,
            before,
            OrderType.MESSAGE_COUNT_DESC,
            12,
        )

    expected = MessageFilter(
        broker_id,
        "home/+/temperature",
        after,
        before,
        OrderType.MESSAGE_COUNT_DESC,
        12,
    )
    assert results == (message,)
    query.assert_called_once_with(expected)
    to_thread.assert_awaited_once_with(query, expected)


async def test_stored_observation_payload_error_is_reported_in_state() -> None:
    runtime = runtime_for(FakeObserverRepository())
    runtime.get_message = MagicMock(  # type: ignore[method-assign]
        side_effect=KeyError("Observation was deleted")
    )
    view_model = MainViewModel(runtime)

    with pytest.raises(KeyError, match="Observation was deleted"):
        await view_model.inspect_stored_observation(uuid4())

    assert view_model.stored_observation_error == "'Observation was deleted'"
    assert not view_model.selected_stored_observation_detail.has_value


async def test_cache_deletion_refreshes_results_and_both_storage_summaries() -> None:
    runtime = runtime_for(FakeObserverRepository())
    broker_id = runtime.active_broker.id
    observation_id = uuid4()
    entry = ObservationDeletionEntry(
        broker_id,
        "home/old",
        observation_id,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        3,
    )
    preview = ObservationDeletionPreview(broker_id, (entry,), "broker")
    deletion = ObservationDeletionResult((entry,), (entry,), ())
    remaining = TopicMessage(
        broker_id,
        "home/current",
        b"on",
        0,
        True,
        datetime(2026, 8, 2, tzinfo=timezone.utc),
        2,
        1,
        uuid4(),
    )
    all_usage = CacheUsageSummary(
        (BrokerCacheUsage(broker_id, 1, 2, remaining.received_at, remaining.received_at),)
    )
    scoped_usage = CacheUsageSummary(all_usage.brokers)
    runtime.confirm_cache_deletion_detailed = MagicMock(  # type: ignore[method-assign]
        return_value=deletion
    )
    runtime.get_observation_storage_summary = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda broker=None: all_usage if broker is None else scoped_usage
    )
    runtime.list_persisted_topics = MagicMock(return_value=())  # type: ignore[method-assign]
    runtime.query_stored_observations = MagicMock(  # type: ignore[method-assign]
        return_value=(remaining,)
    )
    view_model = MainViewModel(runtime)

    await view_model.confirm_cache_deletion(preview)

    assert view_model.cache_usage_summary is all_usage
    assert view_model.broker_cache_usage_summary is scoped_usage
    assert view_model.stored_observation_results == (remaining,)
    summary_calls = runtime.get_observation_storage_summary.call_args_list
    assert [item.args for item in summary_calls] == [(None,), (broker_id,)]


async def test_subscription_change_refreshes_storage_when_cleanup_is_enabled() -> None:
    runtime = runtime_for(FakeObserverRepository())
    usage = CacheUsageSummary(())
    runtime.get_observation_storage_summary = MagicMock(  # type: ignore[method-assign]
        return_value=usage
    )
    runtime.list_persisted_topics = MagicMock(return_value=())  # type: ignore[method-assign]
    runtime.query_stored_observations = MagicMock(return_value=())  # type: ignore[method-assign]
    view_model = MainViewModel(runtime)
    view_model._retention_policy = ObservationRetentionPolicy(
        auto_remove_unsubscribed=True
    )

    await view_model.add_subscription(Subscription("new/#"))

    runtime.query_stored_observations.assert_called_once_with(
        MessageFilter(view_model.active_broker_profile.id)
    )
    assert runtime.get_observation_storage_summary.call_count == 2

    async with view_model._operation("stored-observations"):
        with pytest.raises(RuntimeError, match="already in progress"):
            await view_model.reconnect_to_broker()


class FakeObserverRepository:
    topic_update_interval = 0.0
    connection_status = "disconnected"

    def __init__(self) -> None:
        self._states: dict[str, TopicState] = {}
        self._messages: asyncio.Queue[MqttMessage] = asyncio.Queue()
        self.subscriptions: tuple[Subscription, ...] = ()
        self.connection_operations: list[str] = []
        self.removed_subscriptions: list[Subscription] = []
        self.broker_configurations: list[MqttConfig] = []
        self.dropped_message_count = 0

    async def start(self) -> None:
        self.connection_status = "connected"

    async def stop(self) -> None:
        self.connection_status = "disconnected"

    def get_state(self, topic: str) -> TopicState | None:
        return self._states.get(topic)

    async def messages(self) -> AsyncIterator[MqttMessage]:
        while True:
            yield await self._messages.get()

    async def connection_statuses(self) -> AsyncIterator[object]:
        if False:
            yield "disconnected"

    def drain_pending_messages(self) -> tuple[MqttMessage, ...]:
        messages: list[MqttMessage] = []
        while not self._messages.empty():
            messages.append(self._messages.get_nowait())
        return tuple(messages)

    def publish(self, message: MqttMessage) -> None:
        self._states[message.topic] = TopicState(
            name=message.topic.rsplit("/", maxsplit=1)[-1],
            topic=message.topic,
            payload=message.payload,
            qos=message.qos,
            retain=message.retain,
            recieved_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )
        self._messages.put_nowait(message)

    async def connect(self) -> None:
        self.connection_operations.append("connect")

    async def reconnect(self) -> None:
        self.connection_operations.append("reconnect")

    async def disconnect(self) -> None:
        self.connection_operations.append("disconnect")

    async def update_broker(
        self,
        new_config: MqttConfig,
        subscriptions: tuple[Subscription, ...] | None = None,
    ) -> None:
        self.broker_configurations.append(new_config)
        if subscriptions is not None:
            self.subscriptions = subscriptions

    async def remove_subscription(self, subscription: Subscription) -> None:
        self.removed_subscriptions.append(subscription)
        self.subscriptions = tuple(
            item for item in self.subscriptions if item != subscription
        )

    async def add_subscription(self, subscription: Subscription) -> None:
        self.subscriptions += (subscription,)

    async def update_subscription(
        self, original_filter: str, subscription: Subscription
    ) -> None:
        self.subscriptions = tuple(
            subscription if item.topic_filter == original_filter else item
            for item in self.subscriptions
        )


class FakeBrokerRepository:
    def __init__(self, mqtt: MqttConfig) -> None:
        default_profile = self._profile("Default", mqtt)
        local_profile = self._profile("Local MQTT", MqttConfig("localhost", 1883, "", ""))
        self._profiles = {
            default_profile.id: default_profile,
            local_profile.id: local_profile,
        }
        self._active_profile_id = default_profile.id
        self.updated_mqtt: list[MqttConfig] = []

    def get_mqtt(self) -> MqttConfig:
        return self.get_profile().config

    def update_mqtt(self, mqtt: MqttConfig) -> None:
        self.activate_profile(self._active_profile_id, mqtt)

    def get_profile(self, profile_id: UUID | None = None) -> BrokerProfile:
        return self._profiles[profile_id or self._active_profile_id]

    def get_all_profiles(self) -> tuple[BrokerProfile, ...]:
        return tuple(self._profiles.values())

    def create_profile(self, name: str, mqtt: MqttConfig) -> BrokerProfile:
        profile = self._profile(name.strip(), mqtt)
        self._profiles[profile.id] = profile
        return profile

    def update_profile(self, profile: BrokerProfile) -> None:
        self._profiles[profile.id] = profile

    def delete_profile(self, profile_id: UUID) -> BrokerProfile:
        return self._profiles.pop(profile_id)

    def activate_profile(self, profile_id: UUID, mqtt: MqttConfig | None = None) -> None:
        profile = self.get_profile(profile_id)
        if mqtt is not None:
            profile.config = mqtt
            self.updated_mqtt.append(mqtt)
        self._active_profile_id = profile_id

    def select_active_profile(self, profile_id: UUID) -> None:
        self.activate_profile(profile_id)
        self.updated_mqtt.append(self.get_profile(profile_id).config)

    def replace_subscriptions(
        self, workspace_id: UUID, subscriptions: tuple[Subscription, ...]
    ) -> None:
        profile = next(
            item for item in self._profiles.values() if item.workspace_id == workspace_id
        )
        profile.workspace.subscriptions = subscriptions

    def update_observer_workspace(self, workspace: ObserverWorkspace) -> None:
        self._profiles[workspace.profile_id].workspace = workspace

    @staticmethod
    def _profile(name: str, mqtt: MqttConfig) -> BrokerProfile:
        profile_id = uuid4()
        workspace = ObserverWorkspace(
            id=uuid4(),
            profile_id=profile_id,
        )
        return BrokerProfile(profile_id, name, mqtt, workspace.id, workspace)


class FakeTopicGateRuntime(TopicGateRuntime):
    """Runtime test double backed by in-memory repositories."""


class FakeCurrentTopicReader:
    def __init__(
        self,
        repositories: dict[UUID, FakeObserverRepository],
    ) -> None:
        self._repositories = repositories
        self._observation_ids: dict[tuple[UUID, str], UUID] = {}

    def get_current_topics(self, broker_id: UUID) -> tuple[CurrentTopic, ...]:
        repository = self._repositories.get(broker_id)
        if repository is None:
            return ()
        return tuple(
            self._current_topic(broker_id, state)
            for state in repository._states.values()
        )

    def get_current_topic(
        self,
        broker_id: UUID,
        topic: str,
    ) -> CurrentTopic | None:
        repository = self._repositories.get(broker_id)
        state = None if repository is None else repository.get_state(topic)
        if state is None:
            return None
        return self._current_topic(broker_id, state)

    def _current_topic(self, broker_id: UUID, state: TopicState) -> CurrentTopic:
        observation_id = self._observation_ids.setdefault(
            (broker_id, state.topic),
            uuid4(),
        )
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
                observation_id=observation_id,
            ),
            ObservationStatus.LIVE,
        )


def runtime_for(
    repository: FakeObserverRepository,
    broker_repository: FakeBrokerRepository | None = None,
) -> FakeTopicGateRuntime:
    brokers = broker_repository or FakeBrokerRepository(
        MqttConfig("default", 1883, "", "")
    )
    profiles = brokers.get_all_profiles()
    repositories = {profile.id: repository for profile in profiles}
    active_broker_id = brokers.get_profile().id
    return FakeTopicGateRuntime(
        brokers,
        repositories,
        active_broker_id,
        lambda _profile: repository,
        current_topics=FakeCurrentTopicReader(repositories),
    )


async def test_view_model_displays_and_refreshes_the_selected_topic_state() -> None:
    async def scenario() -> None:
        topic = "SmartHome/Huehnerstall/door/status"
        repository = FakeObserverRepository()
        view_model = MainViewModel(runtime_for(repository), topic)

        assert view_model.value == "Waiting for a message"

        await view_model.start()
        repository.publish(MqttMessage(topic, b"open", qos=1, retain=True))
        await asyncio.sleep(0)

        assert view_model.topic == topic
        assert view_model.value == "open"
        assert view_model.quality_of_service == "1"
        assert view_model.retained == "True"
        assert view_model.received_at == "2026-08-04T12:00:00+00:00"

        await view_model.stop()

    await scenario()


async def test_view_model_batches_notifications_and_reports_dropped_messages() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        repository.dropped_message_count = 4
        view_model = MainViewModel(runtime_for(repository), "untrusted/topic")
        logs: list[str] = []
        view_model.log_message.connect(logs.append)

        for index in range(3):
            repository.publish(
                MqttMessage(
                    "untrusted/topic", str(index).encode(), 0, False
                )
            )

        await view_model.start()
        await asyncio.sleep(0)
        await view_model.stop()

        assert view_model.value == "2"
        assert view_model.dropped_message_count == "4"
        assert logs == [
            "Received 3 MQTT messages (latest: untrusted/topic)",
            "Dropped 4 MQTT messages during admission (4 total)",
        ]

    await scenario()


async def test_view_model_throttles_topic_tree_updates() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        repository.topic_update_interval = 0.02
        view_model = MainViewModel(runtime_for(repository), "untrusted/topic")
        state_changes: list[bool] = []
        topic_changes: list[bool] = []
        view_model.state_changed.connect(lambda: state_changes.append(True))
        view_model.topics_changed.connect(lambda: topic_changes.append(True))
        await view_model.start()
        state_changes.clear()
        topic_changes.clear()

        repository.publish(MqttMessage("untrusted/topic", b"value", 0, False))
        await asyncio.sleep(0)

        assert state_changes == []
        assert topic_changes == []

        await asyncio.sleep(0.03)
        assert state_changes == [True]
        assert topic_changes == [True]
        await view_model.stop()

    await scenario()


def test_discovered_topic_updates_details_and_only_matching_filter_is_editable() -> None:
    repository = FakeObserverRepository()
    repository.subscriptions = (Subscription("SmartHome/+/status", qos=2),)
    repository.publish(MqttMessage("SmartHome/kitchen/status", b"open", 1, False))
    repository.publish(MqttMessage("Other/device/value", b"42", 0, False))
    view_model = MainViewModel(runtime_for(repository))

    view_model.select_topic("SmartHome/kitchen/status")

    assert view_model.decoded_payload == "open"
    assert view_model.selected_subscription == repository.subscriptions[0]
    assert view_model.selected_wildcard_subscription is None

    view_model.select_topic("SmartHome/+/status")

    assert view_model.selected_wildcard_subscription == repository.subscriptions[0]
    summary = view_model.selected_wildcard_filter_summary
    assert summary is not None
    assert summary.topic_filter == "SmartHome/+/status"
    assert summary.matching_topic_count == 1
    assert summary.message_count == 1
    assert [topic.topic for topic in summary.topics] == [
        "SmartHome/kitchen/status"
    ]

    view_model.select_topic("Other/device/value")

    assert view_model.decoded_payload == "42"
    assert view_model.selected_subscription is None
    assert view_model.selected_wildcard_filter_summary is None


def test_payload_rendering_is_bounded_and_reports_truncation() -> None:
    repository = FakeObserverRepository()
    payload = b'"' + b"x" * (MAX_RENDERED_PAYLOAD_BYTES + 100) + b'"'
    repository.publish(MqttMessage("untrusted/topic", payload, 0, False))
    view_model = MainViewModel(runtime_for(repository), "untrusted/topic")
    view_model.refresh()

    notice = (
        f"[Payload truncated: showing {MAX_RENDERED_PAYLOAD_BYTES} of "
        f"{len(payload)} bytes]"
    )
    assert view_model.decoded_payload.endswith(notice)
    assert len(view_model.decoded_payload) < MAX_RENDERED_PAYLOAD_BYTES + 100
    assert view_model.raw_payload.endswith(notice)
    assert len(view_model.raw_payload) < MAX_RENDERED_PAYLOAD_BYTES * 3 + 100


def test_small_json_payload_is_still_pretty_printed() -> None:
    repository = FakeObserverRepository()
    repository.publish(MqttMessage("trusted/topic", b'{"open":true}', 0, False))
    view_model = MainViewModel(runtime_for(repository), "trusted/topic")
    view_model.refresh()

    assert view_model.decoded_payload == '{\n  "open": true\n}'


def test_size_amplified_json_formatting_is_bounded() -> None:
    repository = FakeObserverRepository()
    payload = ("[" * 500 + "0" + "]" * 500).encode()
    repository.publish(MqttMessage("untrusted/json", payload, 0, False))
    view_model = MainViewModel(runtime_for(repository), "untrusted/json")
    view_model.refresh()

    assert view_model.decoded_payload.endswith("[Formatted JSON truncated]")
    assert len(view_model.decoded_payload) < MAX_FORMATTED_JSON_CHARACTERS + 100


def test_topic_paths_include_configured_filters_and_discovered_topics() -> None:
    repository = FakeObserverRepository()
    repository.subscriptions = (Subscription("SmartHome/#"),)
    repository.publish(MqttMessage("SmartHome/kitchen/temperature", b"21", 0, False))

    assert MainViewModel(runtime_for(repository)).topic_paths == [
        "SmartHome/#",
        "SmartHome/kitchen/temperature",
    ]


def test_topic_paths_exclude_discovered_topics_without_an_active_filter() -> None:
    repository = FakeObserverRepository()
    repository.subscriptions = (Subscription("SmartHome/#"),)
    repository.publish(MqttMessage("SmartHome/kitchen/status", b"open", 1, False))
    repository.publish(MqttMessage("Other/device/status", b"open", 1, False))

    assert MainViewModel(runtime_for(repository)).topic_paths == [
        "SmartHome/#",
        "SmartHome/kitchen/status",
    ]


def test_mqtt_filter_matching_supports_wildcards_and_system_topic_rules() -> None:
    assert mqtt_filter_matches("home/+/temperature", "home/kitchen/temperature")
    assert mqtt_filter_matches("home/#", "home/kitchen/temperature")
    assert mqtt_filter_matches("home/#", "home")
    assert not mqtt_filter_matches("home/+/temperature", "home/temperature")
    assert not mqtt_filter_matches("#", "$SYS/broker/uptime")
    assert mqtt_filter_matches("$SYS/#", "$SYS/broker/uptime")


async def test_connection_commands_are_forwarded_to_the_repository() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        view_model = MainViewModel(runtime_for(repository))

        await view_model.connect_to_broker()
        await view_model.reconnect_to_broker()
        await view_model.disconnect_from_broker()

        assert repository.connection_operations == [
            "connect",
            "reconnect",
            "disconnect",
        ]

    await scenario()


async def test_mqtt_configuration_is_applied_then_stored() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        initial = MqttConfig("old", 1883, "", "")
        broker_repository = FakeBrokerRepository(initial)
        view_model = MainViewModel(runtime_for(repository, broker_repository))
        replacement = MqttConfig("new", 8883, "observer", "password", True)
        logs: list[str] = []
        changes: list[bool] = []
        view_model.log_message.connect(logs.append)
        view_model.configuration_changed.connect(lambda: changes.append(True))

        await view_model.update_mqtt_config(replacement)

        assert view_model.mqtt_config.password == ""
        assert view_model.active_broker_profile.password_configured
        assert repository.broker_configurations == [replacement]
        assert broker_repository.updated_mqtt == [replacement]
        assert changes == [True]
        assert logs == [
            "Connecting to MQTT broker: new:8883",
            "Updated MQTT broker: new:8883",
        ]

    await scenario()


async def test_switching_broker_profile_activates_its_workspace_after_connecting() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        broker_repository = FakeBrokerRepository(MqttConfig("default", 1883, "", ""))
        view_model = MainViewModel(runtime_for(repository, broker_repository))
        local_profile = broker_repository.get_all_profiles()[1]

        await view_model.update_broker_profile(local_profile.id, local_profile.config)

        assert repository.broker_configurations == [local_profile.config]
        assert view_model.active_broker_profile.id == local_profile.id
        assert view_model.mqtt_config == local_profile.config

    await scenario()


async def test_switching_broker_clears_previous_health_report_and_history() -> None:
    repository = FakeObserverRepository()
    brokers = FakeBrokerRepository(MqttConfig("default", 1883, "", ""))
    runtime = runtime_for(repository, brokers)
    view_model = MainViewModel(runtime)
    previous = view_model.active_broker_profile.id
    replacement = view_model.broker_profiles[1]
    view_model._health_report_result = MagicMock(broker_id=previous)
    view_model._health_history_result = MagicMock(items=(MagicMock(),))
    changes: list[bool] = []
    view_model.health_changed.connect(lambda: changes.append(True))

    await view_model.activate_broker_profile(
        replacement.id,
        replacement.config,
    )

    assert view_model.health_report is None
    assert view_model.health_history.items == ()
    assert changes == [True]


async def test_switching_broker_profile_moves_live_message_observation() -> None:
    async def scenario() -> None:
        brokers = FakeBrokerRepository(MqttConfig("default", 1883, "", ""))
        default_profile, selected_profile = brokers.get_all_profiles()
        selected_profile.workspace.subscriptions = (Subscription("#"),)
        default_repo = FakeObserverRepository()
        selected_repo = FakeObserverRepository()
        repositories = {
            default_profile.id: default_repo,
            selected_profile.id: selected_repo,
        }
        runtime = FakeTopicGateRuntime(
            brokers,
            repositories,
            default_profile.id,
            current_topics=FakeCurrentTopicReader(repositories),
        )
        view_model = MainViewModel(runtime)
        await view_model.start()

        await view_model.activate_broker_profile(
            selected_profile.id,
            selected_profile.config,
        )
        selected_repo.publish(
            MqttMessage("garage/status", b"closed", 0, False)
        )
        await asyncio.sleep(0)

        assert "garage/status" in view_model.topic_paths
        await view_model.stop()

    await scenario()


def test_saving_inactive_broker_profile_does_not_connect_or_activate_it() -> None:
    repository = FakeObserverRepository()
    broker_repository = FakeBrokerRepository(MqttConfig("default", 1883, "", ""))
    view_model = MainViewModel(runtime_for(repository, broker_repository))
    active_profile = broker_repository.get_profile()
    inactive_profile = broker_repository.get_all_profiles()[1]
    replacement = MqttConfig("fixed", 8883, "observer", "secret", True)

    saved = view_model.save_broker_profile(
        inactive_profile.id,
        replacement,
        "Fixed local",
    )

    assert repository.broker_configurations == []
    assert broker_repository.get_profile().id == active_profile.id
    assert broker_repository.updated_mqtt == []
    assert saved.name == "Fixed local"
    assert saved.config.password == ""
    assert saved.password_configured


async def test_profile_operations_allow_credentials_without_tls() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        broker_repository = FakeBrokerRepository(
            MqttConfig("default", 1883, "", "")
        )
        view_model = MainViewModel(runtime_for(repository, broker_repository))
        profile = broker_repository.get_profile()
        insecure = MqttConfig("broker", 1883, "observer", "secret")

        saved = view_model.save_broker_profile(profile.id, insecure)
        created = view_model.create_broker_profile("Plain MQTT", insecure)
        await view_model.activate_broker_profile(profile.id, insecure)

        assert saved.config.password == ""
        assert saved.password_configured
        assert created.config.password == ""
        assert created.password_configured
        assert broker_repository.get_profile().config == insecure
        assert len(broker_repository.get_all_profiles()) == 3
        assert repository.broker_configurations == [insecure]

    await scenario()


async def test_switching_broker_profile_replaces_the_visible_workspace_tree() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        broker_repository = FakeBrokerRepository(
            MqttConfig("default", 1883, "", "")
        )
        default_profile, local_profile = broker_repository.get_all_profiles()
        default_profile.workspace.subscriptions = (Subscription("home/status"),)
        repository.subscriptions = default_profile.workspace.subscriptions
        local_profile.workspace.subscriptions = (
            Subscription("bridge/connected"),
        )
        view_model = MainViewModel(runtime_for(repository, broker_repository))

        assert view_model.topic_paths == ["home/status"]

        await view_model.update_broker_profile(
            local_profile.id,
            local_profile.config,
        )

        assert view_model.topic_paths == ["bridge/connected"]
        assert repository.subscriptions == (Subscription("bridge/connected"),)

    await scenario()


async def test_view_model_creates_and_renames_broker_profiles() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        broker_repository = FakeBrokerRepository(MqttConfig("default", 1883, "", ""))
        view_model = MainViewModel(runtime_for(repository, broker_repository))
        changes: list[bool] = []
        logs: list[str] = []
        view_model.configuration_changed.connect(lambda: changes.append(True))
        view_model.log_message.connect(logs.append)

        created = view_model.create_broker_profile(
            "Remote",
            MqttConfig("remote", 1883, "", ""),
        )
        await view_model.update_broker_profile(
            created.id,
            MqttConfig("remote-new", 8883, "user", "secret", True),
            "Remote TLS",
        )

        assert view_model.active_broker_profile.name == "Remote TLS"
        assert view_model.active_broker_profile.config.host == "remote-new"
        assert changes == [True, True]
        assert logs[0] == "Created broker profile: Remote"

    await scenario()


async def test_deleting_active_profile_switches_before_removing_it() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        broker_repository = FakeBrokerRepository(MqttConfig("default", 1883, "", ""))
        view_model = MainViewModel(runtime_for(repository, broker_repository))
        deleted_profile = view_model.active_broker_profile
        replacement = view_model.broker_profiles[1]

        await view_model.delete_broker_profile(deleted_profile.id)

        assert view_model.active_broker_profile.id == replacement.id
        assert deleted_profile not in view_model.broker_profiles
        assert repository.broker_configurations == [replacement.config]

    await scenario()


async def test_failed_mqtt_configuration_is_stored_for_offline_editing() -> None:
    class FailingObserverRepository(FakeObserverRepository):
        async def update_broker(
            self,
            new_config: MqttConfig,
            subscriptions: tuple[Subscription, ...] | None = None,
        ) -> None:
            raise ConnectionError("broker unavailable")

    async def scenario() -> None:
        initial = MqttConfig("old", 1883, "", "")
        broker_repository = FakeBrokerRepository(initial)
        view_model = MainViewModel(runtime_for(FailingObserverRepository(), broker_repository))
        logs: list[str] = []
        changes: list[bool] = []
        health_changes: list[bool] = []
        view_model._health_report_result = MagicMock(
            broker_id=view_model.active_broker_profile.id
        )
        view_model.log_message.connect(logs.append)
        view_model.configuration_changed.connect(lambda: changes.append(True))
        view_model.health_changed.connect(lambda: health_changes.append(True))

        try:
            await view_model.update_mqtt_config(MqttConfig("new", 8883, "", ""))
        except ConnectionError:
            pass
        else:
            raise AssertionError("Expected the broker update to fail")

        assert view_model.mqtt_config == MqttConfig("new", 8883, "", "")
        assert broker_repository.updated_mqtt == [
            MqttConfig("new", 8883, "", "")
        ]
        assert changes == [True]
        assert view_model.health_report is None
        assert health_changes == [True]
        assert logs == [
            "Connecting to MQTT broker: new:8883",
            "Broker selected but connection failed: broker unavailable",
        ]

    await scenario()


async def test_failed_broker_switch_updates_health_report_scope() -> None:
    class FailingObserverRepository(FakeObserverRepository):
        async def update_broker(
            self,
            new_config: MqttConfig,
            subscriptions: tuple[Subscription, ...] | None = None,
        ) -> None:
            self.connection_status = "disconnected"
            raise ConnectionError("broker unavailable")

    brokers = FakeBrokerRepository(MqttConfig("default", 1883, "", ""))
    replacement = brokers.get_all_profiles()[1]
    health_query = MagicMock()
    health_query.get_health_report.side_effect = lambda broker_id: MagicMock(
        broker_id=broker_id
    )
    view_model = MainViewModel(
        runtime_for(FailingObserverRepository(), brokers),
        health_query_service=health_query,
    )

    with pytest.raises(ConnectionError, match="broker unavailable"):
        await view_model.activate_broker_profile(
            replacement.id,
            replacement.config,
        )

    report = view_model.refresh_health()

    assert view_model.active_broker_profile.id == replacement.id
    assert view_model.connection_status == "disconnected"
    assert report.broker_id == replacement.id
    health_query.get_health_report.assert_called_once_with(replacement.id)


async def test_removing_subscription_updates_topics_and_clears_stale_selection() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        subscription = Subscription("SmartHome/#")
        repository.subscriptions = (subscription,)
        view_model = MainViewModel(
            runtime_for(repository), subscription.topic_filter
        )
        changed: list[str] = []
        logs: list[str] = []
        view_model.state_changed.connect(lambda: changed.append("state"))
        view_model.topics_changed.connect(lambda: changed.append("topics"))
        view_model.subscriptions_changed.connect(
            lambda: changed.append("subscriptions")
        )
        view_model.log_message.connect(logs.append)

        await view_model.remove_subscription(subscription)

        assert repository.removed_subscriptions == [subscription]
        assert repository.subscriptions == ()
        assert view_model.topic == ""
        assert changed == ["state", "topics", "subscriptions"]
        assert logs == ["Removed subscription: SmartHome/#"]

    await scenario()


async def test_removing_subscription_hides_its_cached_topic_and_clears_selection() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        subscription = Subscription("SmartHome/kitchen/status")
        repository.subscriptions = (subscription,)
        repository.publish(
            MqttMessage(subscription.topic_filter, b"open", 1, False)
        )
        view_model = MainViewModel(
            runtime_for(repository), subscription.topic_filter
        )

        await view_model.remove_subscription(subscription)

        assert view_model.topic == ""
        assert view_model.topic_paths == []

    await scenario()


async def test_removing_subscription_hides_cached_configured_topic() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        subscription = Subscription("SmartHome/kitchen/status")
        repository.subscriptions = (subscription,)
        broker_repository = FakeBrokerRepository(
            MqttConfig("default", 1883, "", "")
        )
        broker_repository.get_profile().workspace.subscriptions = (subscription,)
        view_model = MainViewModel(
            runtime_for(repository, broker_repository),
            subscription.topic_filter,
        )

        await view_model.remove_subscription(subscription)

        assert view_model.topic == ""
        assert view_model.topic_paths == []

    await scenario()


async def test_removing_subscription_keeps_topics_covered_by_another_filter() -> None:
    async def scenario() -> None:
        repository = FakeObserverRepository()
        removed = Subscription("SmartHome/kitchen/status")
        remaining = Subscription("SmartHome/#")
        repository.subscriptions = (removed, remaining)
        repository.publish(MqttMessage(removed.topic_filter, b"open", 1, False))
        view_model = MainViewModel(runtime_for(repository), removed.topic_filter)

        await view_model.remove_subscription(removed)

        assert view_model.topic == removed.topic_filter
        assert view_model.topic_paths == [
            remaining.topic_filter,
            removed.topic_filter,
        ]

    await scenario()


def test_snapshot_query_validation_and_reset_preserve_cached_state() -> None:
    repository = FakeObserverRepository()
    runtime = runtime_for(repository)
    view_model = MainViewModel(runtime)
    original_snapshot = view_model.broker_snapshot

    try:
        view_model.apply_snapshot_query(
            SnapshotQuery(topic_filter="home/#/invalid")
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected the invalid MQTT filter to be rejected")

    assert view_model.snapshot_query == SnapshotQuery()
    assert view_model.broker_snapshot is original_snapshot

    view_model.apply_snapshot_query(SnapshotQuery("home/#", 5, 10, 256))
    view_model.reset_snapshot_query()

    assert view_model.snapshot_query == SnapshotQuery()
    assert view_model.broker_snapshot.topic_filter == "#"


async def test_reconnect_observe_failure_preserves_query_and_snapshot() -> None:
    runtime = runtime_for(FakeObserverRepository())
    real_service = BrokerSnapshotService(runtime)
    snapshot_service = MagicMock()
    snapshot_service.build_current.side_effect = real_service.build_current
    snapshot_service.observe = AsyncMock(side_effect=RuntimeError("offline"))
    view_model = MainViewModel(runtime, snapshot_service=snapshot_service)
    original_query = view_model.snapshot_query
    original_snapshot = view_model.broker_snapshot
    requested = SnapshotQuery("home/#", 30, 20, 512)

    try:
        await view_model.reconnect_and_observe(requested)
    except RuntimeError as error:
        assert str(error) == "offline"
    else:
        raise AssertionError("Expected reconnect and observe to fail")

    assert view_model.snapshot_query is original_query
    assert view_model.broker_snapshot is original_snapshot
    snapshot_service.observe.assert_awaited_once_with(
        view_model.active_broker_profile.id,
        topic_filter="home/#",
        max_age_seconds=30,
        result_limit=20,
        payload_limit_bytes=512,
    )
