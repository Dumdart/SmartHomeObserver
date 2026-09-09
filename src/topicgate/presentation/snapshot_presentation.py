from dataclasses import dataclass
from datetime import datetime

from topicgate.app.models.broker_snapshot import (
    BrokerSnapshot,
    SnapshotLimitation,
    SnapshotTopicState,
)
from topicgate.app.services.broker_snapshot_service import (
    DEFAULT_SNAPSHOT_RESULT_LIMIT,
)
from topicgate.core.mqtt_topics import mqtt_filter_matches
from topicgate.core.payload_limits import MAX_RENDERED_PAYLOAD_BYTES


@dataclass(frozen=True)
class SnapshotQuery:
    topic_filter: str = "#"
    max_age_seconds: float | None = None
    result_limit: int = DEFAULT_SNAPSHOT_RESULT_LIMIT
    payload_limit_bytes: int = MAX_RENDERED_PAYLOAD_BYTES


@dataclass(frozen=True)
class TopicStateBadge:
    key: str
    label: str
    tone: str
    target_path: str | None = None


@dataclass(frozen=True)
class BrokerSnapshotHealth:
    captured_at_label: str
    connected_at_label: str
    observation_started_at_label: str
    observed_for_label: str
    returned_count: int
    omitted_count: int
    stale_count: int
    truncated_count: int
    dropped_message_count: int
    completeness_status: str
    limitation_labels: tuple[str, ...]


def snapshot_health(snapshot: BrokerSnapshot) -> BrokerSnapshotHealth:
    return BrokerSnapshotHealth(
        captured_at_label=datetime_label(snapshot.captured_at),
        connected_at_label=datetime_label(snapshot.connected_at),
        observation_started_at_label=datetime_label(
            snapshot.observation_started_at
        ),
        observed_for_label=duration_label(snapshot.observed_for_seconds),
        returned_count=snapshot.results.returned,
        omitted_count=snapshot.results.omitted,
        stale_count=snapshot.freshness.stale_count,
        truncated_count=sum(
            1 for topic in snapshot.topics if topic.payload.truncated
        ),
        dropped_message_count=snapshot.dropped_message_count,
        completeness_status=(
            "Complete" if snapshot.completeness.is_complete else "Limited"
        ),
        limitation_labels=tuple(
            limitation_label(item)
            for item in snapshot.completeness.limitations
        ),
    )


def topic_state_badges(state: SnapshotTopicState) -> tuple[TopicStateBadge, ...]:
    status = state.status.value
    tones = {
        "live": "success",
        "cached": "info",
        "stale": "warning",
    }
    badges = [TopicStateBadge(status, status.title(), tones[status])]
    if state.source.value == "stored":
        badges.append(TopicStateBadge("stored", "Stored", "neutral"))
    return tuple(badges)


def datetime_label(value: datetime | None) -> str:
    if value is None:
        return "Not started"
    return value.isoformat(timespec="seconds")


def duration_label(seconds: float | None) -> str:
    if seconds is None:
        return "Not observing"
    return f"{seconds:.1f} seconds"


def age_label(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    return f"{seconds:.1f} seconds"


def size_label(size: int) -> str:
    return f"{size} byte" if size == 1 else f"{size} bytes"


def limitation_label(limitation: SnapshotLimitation | str) -> str:
    value = getattr(limitation, "value", limitation)
    labels = {
        "current_state_only": "Current state only; this is not message history.",
        "retained_delivery_unconfirmed": (
            "Retained delivery cannot be confirmed from snapshot state."
        ),
        "broker_disconnected": "Broker is disconnected.",
        "observation_not_started": "Live observation has not started.",
        "stored_state_predates_observation": (
            "Persisted values may predate the current observation window."
        ),
        "dropped_messages": "Messages were dropped before processing.",
        "stale_states_omitted": "Stale states were omitted by the age limit.",
        "result_limit_reached": "Topics were omitted by the result limit.",
        "payload_truncated": "One or more payloads are truncated.",
    }
    return labels[str(value)]


def status_detail(status: str) -> str:
    if status == "stale":
        return "Value predates the current observation window."
    if status == "cached":
        return "Value was restored from persisted storage."
    return "Value was received during the current runtime."


def topic_omission_notice(
    query: SnapshotQuery,
    snapshot: BrokerSnapshot,
    state: SnapshotTopicState,
) -> str:
    """Explain why an exact current topic is outside the bounded snapshot."""
    if any(item.topic == state.topic for item in snapshot.topics):
        return ""
    if not mqtt_filter_matches(query.topic_filter, state.topic):
        reason = f"the active topic filter {query.topic_filter!r} excludes it"
    elif (
        query.max_age_seconds is not None
        and state.age_seconds > query.max_age_seconds
    ):
        reason = "it is older than the active maximum age"
    elif snapshot.results.omitted_by_limit:
        reason = "it is beyond the active result limit"
    else:
        reason = "the active snapshot bounds omit it"
    return (
        "Current value available, but excluded from snapshot values and counts because "
        f"{reason}."
    )
