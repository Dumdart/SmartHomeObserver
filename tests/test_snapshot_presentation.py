from datetime import datetime, timedelta, timezone
from uuid import uuid4

from topicgate.app.models.broker_snapshot import (
    BrokerSnapshot,
    SnapshotBrokerIdentity,
    SnapshotCompleteness,
    SnapshotFreshness,
    SnapshotLimitation,
    SnapshotPayload,
    SnapshotPayloadEncoding,
    SnapshotResultLimit,
    SnapshotSettling,
    SnapshotTopicState,
    SnapshotTopicStatus,
)
from topicgate.core.models.mqtt_observation import ObservationSource
from topicgate.presentation.snapshot_presentation import (
    SnapshotQuery,
    snapshot_health,
    topic_omission_notice,
    topic_state_badges,
)
from topicgate.presentation.topic_presentation import topic_detail

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def test_snapshot_health_formats_times_counts_and_limitations() -> None:
    snapshot = _snapshot(
        _topic(
            "cached/value",
            source=ObservationSource.STORED,
            status=SnapshotTopicStatus.CACHED,
            truncated=True,
        )
    )

    health = snapshot_health(snapshot)

    assert health.captured_at_label == "2026-08-17T12:00:00+00:00"
    assert health.connected_at_label == "2026-08-17T11:59:30+00:00"
    assert health.observation_started_at_label == "2026-08-17T11:59:00+00:00"
    assert health.observed_for_label == "60.0 seconds"
    assert health.returned_count == 1
    assert health.omitted_count == 2
    assert health.stale_count == 1
    assert health.truncated_count == 1
    assert health.dropped_message_count == 3
    assert health.completeness_status == "Limited"
    assert health.limitation_labels == (
        "Messages were dropped before processing.",
        "One or more payloads are truncated.",
    )


def test_topic_badges_keep_status_and_stored_provenance_separate() -> None:
    cached = topic_state_badges(
        _topic(
            "cached",
            source=ObservationSource.STORED,
            status=SnapshotTopicStatus.CACHED,
        )
    )
    stale = topic_state_badges(
        _topic(
            "stale",
            source=ObservationSource.STORED,
            status=SnapshotTopicStatus.STALE,
        )
    )
    live = topic_state_badges(_topic("live"))

    assert [badge.label for badge in cached] == ["Cached", "Stored"]
    assert [badge.label for badge in stale] == ["Stale", "Stored"]
    assert [badge.label for badge in live] == ["Live"]


def test_snapshot_topic_detail_explains_both_truncation_stages() -> None:
    detail = topic_detail(
        _topic(
            "large",
            source=ObservationSource.STORED,
            status=SnapshotTopicStatus.STALE,
            truncated=True,
        )
    )

    assert detail.source_label == "Persisted storage"
    assert detail.status_label == "Stale"
    assert detail.age_label == "15.0 seconds"
    assert detail.original_payload_size == 20
    assert detail.available_payload_size == 10
    assert detail.rendered_payload_size == 5
    assert detail.ingestion_truncated
    assert detail.rendering_truncated
    assert detail.ingestion_truncation_label == "Yes - 10 of 20 bytes available"
    assert detail.rendering_truncation_label == (
        "Yes - showing 5 of 10 available bytes"
    )


def test_snapshot_query_defaults_match_read_only_snapshot_defaults() -> None:
    assert SnapshotQuery() == SnapshotQuery(
        topic_filter="#",
        max_age_seconds=None,
        result_limit=100,
        payload_limit_bytes=16_384,
    )


def test_topic_omission_notice_distinguishes_filter_and_result_limit() -> None:
    state = _topic("devices/z/status")
    snapshot = _snapshot(_topic("devices/a/status"))

    assert topic_omission_notice(
        SnapshotQuery(topic_filter="other/#"), snapshot, state
    ) == (
        "Current value available, but excluded from snapshot values and counts because "
        "the active topic filter 'other/#' excludes it."
    )
    assert topic_omission_notice(
        SnapshotQuery(result_limit=1), snapshot, state
    ) == (
        "Current value available, but excluded from snapshot values and counts because "
        "it is beyond the active result limit."
    )


def _topic(
    topic: str,
    *,
    source: ObservationSource = ObservationSource.LIVE,
    status: SnapshotTopicStatus = SnapshotTopicStatus.LIVE,
    truncated: bool = False,
) -> SnapshotTopicState:
    available_size = 10 if truncated else 5
    original_size = 20 if truncated else 5
    rendered_size = 5
    return SnapshotTopicState(
        topic=topic,
        payload=SnapshotPayload(
            encoding=SnapshotPayloadEncoding.UTF8,
            value="value",
            original_size=original_size,
            available_size=available_size,
            rendered_size=rendered_size,
            ingestion_truncated=truncated,
            rendering_truncated=truncated,
            truncated=truncated,
        ),
        qos=1,
        retain=False,
        received_at=NOW - timedelta(seconds=15),
        age_seconds=15,
        message_count=2,
        source=source,
        status=status,
    )


def _snapshot(*topics: SnapshotTopicState) -> BrokerSnapshot:
    return BrokerSnapshot(
        broker=SnapshotBrokerIdentity(uuid4(), "Primary"),
        connection_status="connected",
        captured_at=NOW,
        connected_at=NOW - timedelta(seconds=30),
        observation_started_at=NOW - timedelta(seconds=60),
        observed_for_seconds=60,
        topic_filter="#",
        topics=topics,
        dropped_message_count=3,
        freshness=SnapshotFreshness(None, 1),
        results=SnapshotResultLimit(100, 3, 1, 2, 1, 1, True),
        settling=SnapshotSettling(0, 5, 0),
        completeness=SnapshotCompleteness(
            False,
            (
                SnapshotLimitation.DROPPED_MESSAGES,
                SnapshotLimitation.PAYLOAD_TRUNCATED,
            ),
        ),
    )
