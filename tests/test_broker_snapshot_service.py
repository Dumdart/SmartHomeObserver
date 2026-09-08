from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from topicgate.app.models.broker_snapshot import (
    SnapshotLimitation,
    SnapshotPayloadEncoding,
    SnapshotTopicStatus,
)
from topicgate.app.services.broker_snapshot_service import BrokerSnapshotService
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.mqtt_observation import (
    MqttObservation,
    ObservationSource,
)
from topicgate.core.models.observation_status import ObservationStatus
from topicgate.core.models.topic_message import TopicMessage

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def observation(
    topic: str,
    payload: bytes,
    *,
    age_seconds: float = 0,
    payload_size: int | None = None,
    source: ObservationSource = ObservationSource.LIVE,
) -> MqttObservation:
    return MqttObservation(
        name=topic.rsplit("/", 1)[-1],
        topic=topic,
        payload=payload,
        qos=1,
        retain=True,
        recieved_at=NOW - timedelta(seconds=age_seconds),
        payload_size=payload_size,
        message_count=2,
        source=source,
    )


def snapshot_service(
    *states: MqttObservation,
    status: ConnectionStatus = ConnectionStatus.CONNECTED,
    dropped: int = 0,
) -> tuple[BrokerSnapshotService, BrokerSummary, MagicMock]:
    selected = BrokerSummary(
        id=uuid4(),
        name="Primary",
        config=MqttConfig("broker", 1883, "", ""),
        password_configured=False,
    )
    runtime = MagicMock()
    runtime.list_brokers.return_value = (selected,)
    runtime.get_current_topics.return_value = tuple(
        CurrentTopic(
            TopicMessage(
                broker_id=selected.id,
                topic=state.topic,
                payload=state.payload,
                qos=state.qos,
                retain=state.retain,
                received_at=state.received_at,
                payload_size=state.payload_size or len(state.payload),
                message_count=state.message_count,
                observation_id=uuid4(),
            ),
            (
                ObservationStatus.CACHED
                if state.source is ObservationSource.STORED
                else ObservationStatus.LIVE
            ),
        )
        for state in states
    )
    runtime.get_connection_status.return_value = status
    runtime.get_dropped_message_count.return_value = dropped
    runtime.get_connected_at.return_value = NOW - timedelta(seconds=30)
    runtime.get_observation_started_at.return_value = NOW - timedelta(seconds=60)
    runtime.activate_broker = AsyncMock()
    return BrokerSnapshotService(runtime, clock=lambda: NOW), selected, runtime


@pytest.mark.parametrize(
    ("topic_filter", "expected"),
    [
        ("home/kitchen/temp", ("home/kitchen/temp",)),
        ("home/+/temp", ("home/kitchen/temp", "home/living/temp")),
        ("home/#", ("home/kitchen/temp", "home/living/temp", "home/status")),
    ],
)
async def test_snapshot_matches_exact_single_and_multi_level_filters(
    topic_filter: str,
    expected: tuple[str, ...],
) -> None:
    service, selected, _ = snapshot_service(
        observation("home/status", b"on"),
        observation("home/living/temp", b"21"),
        observation("home/kitchen/temp", b"20"),
        observation("garage/status", b"closed"),
    )

    snapshot = await service.build(selected.id, topic_filter=topic_filter)

    assert tuple(item.topic for item in snapshot.topics) == expected


async def test_snapshot_reports_stale_and_limited_states_without_hiding_them() -> None:
    service, selected, _ = snapshot_service(
        observation("sensor/c", b"stale", age_seconds=11),
        observation("sensor/b", b"fresh", age_seconds=2),
        observation("sensor/a", b"fresh", age_seconds=1),
    )

    snapshot = await service.build(
        selected.id,
        topic_filter="sensor/#",
        max_age_seconds=10,
        result_limit=1,
    )

    assert tuple(item.topic for item in snapshot.topics) == ("sensor/a",)
    assert snapshot.freshness.stale_count == 1
    assert snapshot.results.total == 3
    assert snapshot.results.returned == 1
    assert snapshot.results.omitted == 2
    assert snapshot.results.omitted_as_stale == 1
    assert snapshot.results.omitted_by_limit == 1
    assert snapshot.results.truncated
    assert SnapshotLimitation.STALE_STATES_OMITTED in (
        snapshot.completeness.limitations
    )
    assert SnapshotLimitation.RESULT_LIMIT_REACHED in (
        snapshot.completeness.limitations
    )


async def test_snapshot_renders_utf8_without_splitting_a_multibyte_character() -> None:
    service, selected, _ = snapshot_service(
        observation("text", "AøB".encode("utf-8"))
    )

    snapshot = await service.build(selected.id, payload_limit_bytes=2)
    payload = snapshot.topics[0].payload

    assert payload.encoding == SnapshotPayloadEncoding.UTF8
    assert payload.value == "A"
    assert payload.original_size == 4
    assert payload.available_size == 4
    assert payload.rendered_size == 1
    assert payload.rendering_truncated
    assert payload.truncated


async def test_snapshot_renders_binary_as_bounded_base64() -> None:
    service, selected, _ = snapshot_service(observation("binary", b"\xff\x00abc"))

    snapshot = await service.build(selected.id, payload_limit_bytes=2)
    payload = snapshot.topics[0].payload

    assert payload.encoding == SnapshotPayloadEncoding.BASE64
    assert payload.value == "/wA="
    assert payload.rendered_size == 2
    assert payload.rendering_truncated


async def test_snapshot_preserves_prior_ingestion_truncation_metadata() -> None:
    service, selected, _ = snapshot_service(
        observation("large", b"stored", payload_size=100)
    )

    payload = (await service.build(selected.id)).topics[0].payload

    assert payload.original_size == 100
    assert payload.available_size == 6
    assert payload.ingestion_truncated
    assert payload.truncated


async def test_snapshot_reports_provenance_age_window_and_drop_limitations() -> None:
    service, selected, _ = snapshot_service(
        observation(
            "cached",
            b"value",
            age_seconds=15,
            source=ObservationSource.STORED,
        ),
        dropped=4,
    )

    snapshot = await service.build(selected.id)

    assert snapshot.broker.id == selected.id
    assert snapshot.connection_status == "connected"
    assert snapshot.captured_at == NOW
    assert snapshot.observed_for_seconds == 60
    assert snapshot.topics[0].age_seconds == 15
    assert snapshot.topics[0].source == ObservationSource.STORED
    assert snapshot.topics[0].status == SnapshotTopicStatus.CACHED
    assert snapshot.dropped_message_count == 4
    assert not snapshot.completeness.is_complete
    assert SnapshotLimitation.CURRENT_STATE_ONLY in snapshot.completeness.limitations
    assert SnapshotLimitation.RETAINED_DELIVERY_UNCONFIRMED in (
        snapshot.completeness.limitations
    )
    assert SnapshotLimitation.STORED_STATE_PREDATES_OBSERVATION in (
        snapshot.completeness.limitations
    )
    assert SnapshotLimitation.DROPPED_MESSAGES in (
        snapshot.completeness.limitations
    )


async def test_snapshot_classifies_live_cached_and_stale_topic_state() -> None:
    service, selected, runtime = snapshot_service(
        observation("live", b"new", age_seconds=10),
        observation(
            "cached",
            b"stored",
            age_seconds=20,
            source=ObservationSource.STORED,
        ),
        observation(
            "stale",
            b"old",
            age_seconds=90,
            source=ObservationSource.STORED,
        ),
    )
    runtime.get_observation_started_at.return_value = NOW - timedelta(seconds=60)

    snapshot = await service.build(selected.id)

    assert {item.topic: item.status for item in snapshot.topics} == {
        "cached": SnapshotTopicStatus.CACHED,
        "live": SnapshotTopicStatus.LIVE,
        "stale": SnapshotTopicStatus.STALE,
    }


async def test_snapshot_validates_all_caller_controlled_bounds() -> None:
    service, selected, _ = snapshot_service()

    invalid_arguments = (
        {"topic_filter": "home/#/invalid"},
        {"max_age_seconds": -1},
        {"result_limit": 0},
        {"result_limit": 1_001},
        {"payload_limit_bytes": -1},
        {"payload_limit_bytes": 16 * 1024 + 1},
    )
    for arguments in invalid_arguments:
        with pytest.raises(ValueError):
            await service.build(selected.id, **arguments)


async def test_snapshot_read_does_not_activate_or_wait() -> None:
    async def unexpected_sleep(_duration: float) -> None:
        raise AssertionError("Read-only snapshots must not wait")

    service, selected, runtime = snapshot_service()
    service = BrokerSnapshotService(
        runtime,
        clock=lambda: NOW,
        sleep=unexpected_sleep,
    )

    snapshot = await service.build(selected.id)

    runtime.activate_broker.assert_not_awaited()
    assert snapshot.settling.requested_seconds == 0
    assert snapshot.settling.actual_seconds == 0


async def test_async_and_synchronous_snapshot_reads_share_one_result() -> None:
    service, selected, runtime = snapshot_service(observation("sensor/value", b"12"))

    synchronous = service.build_current(selected.id)
    asynchronous = await service.build(selected.id)

    assert synchronous == asynchronous
    assert runtime.get_current_topics.call_count == 2


def test_resolved_snapshot_bypasses_broker_resolution() -> None:
    _, selected, runtime = snapshot_service(observation("sensor/value", b"12"))
    resolver = MagicMock()
    service = BrokerSnapshotService(
        runtime,
        resolver=resolver,
        clock=lambda: NOW,
    )

    snapshot = service.build_resolved_current(selected)

    assert snapshot.broker.id == selected.id
    resolver.resolve.assert_not_called()


async def test_observe_activates_waits_and_reports_actual_duration() -> None:
    monotonic_values = iter((10.0, 10.75))
    waited: list[float] = []

    async def sleep(duration: float) -> None:
        waited.append(duration)

    service, selected, runtime = snapshot_service()
    service = BrokerSnapshotService(
        runtime,
        clock=lambda: NOW,
        monotonic_clock=lambda: next(monotonic_values),
        sleep=sleep,
    )

    snapshot = await service.observe(selected.id, wait_seconds=0.5)

    runtime.activate_broker.assert_awaited_once_with(selected.id)
    assert waited == [0.5]
    assert snapshot.settling.requested_seconds == 0.5
    assert snapshot.settling.actual_seconds == 0.75
    assert not snapshot.completeness.is_complete


async def test_observe_defaults_to_one_second_wait() -> None:
    waited: list[float] = []

    async def sleep(duration: float) -> None:
        waited.append(duration)

    service, selected, runtime = snapshot_service()
    service = BrokerSnapshotService(runtime, clock=lambda: NOW, sleep=sleep)

    snapshot = await service.observe(selected.id)

    assert waited == [1.0]
    assert snapshot.settling.requested_seconds == 1.0


async def test_observe_validates_before_activation() -> None:
    service, selected, runtime = snapshot_service()

    with pytest.raises(ValueError, match="wait_seconds"):
        await service.observe(selected.id, wait_seconds=5.1)

    runtime.activate_broker.assert_not_awaited()


async def test_observe_does_not_wait_after_activation_failure() -> None:
    waited: list[float] = []

    async def sleep(duration: float) -> None:
        waited.append(duration)

    service, selected, runtime = snapshot_service()
    runtime.activate_broker.side_effect = ConnectionError("unavailable")
    service = BrokerSnapshotService(runtime, clock=lambda: NOW, sleep=sleep)

    with pytest.raises(ConnectionError, match="unavailable"):
        await service.observe(selected.id)

    assert waited == []
