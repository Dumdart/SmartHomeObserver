from __future__ import annotations

import json
from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from topicgate.app.models.broker_snapshot import (
    SnapshotPayloadEncoding,
    SnapshotTopicState,
)
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.mqtt_observation import MqttObservation
from topicgate.core.models.subscription import Subscription
from topicgate.core.mqtt_topics import (
    mqtt_filter_has_wildcards,
    mqtt_filter_matches,
)
from topicgate.core.payload_limits import (
    MAX_FORMATTED_JSON_CHARACTERS,
    MAX_RENDERED_PAYLOAD_BYTES,
)
from topicgate.presentation.snapshot_presentation import (
    TopicStateBadge,
    age_label,
    size_label,
    status_detail,
    topic_state_badges,
)


@dataclass(frozen=True)
class TopicDetail:
    topic: str
    has_value: bool
    decoded_payload: str
    payload_text: str
    payload_base64: str
    raw_payload: str
    payload_display: str
    payload_encoding: str
    payload_size: int
    payload_size_label: str
    rendered_payload_size: int
    is_truncated: bool
    truncation_label: str
    qos: int | None
    qos_label: str
    retained: bool | None
    retain_label: str
    received_at: str
    message_count: int
    dropped_message_count: int
    age_seconds: float | None
    age_label: str
    source: str | None
    source_label: str
    status: str
    status_label: str
    status_detail: str
    original_payload_size: int
    original_payload_size_label: str
    available_payload_size: int
    available_payload_size_label: str
    ingestion_truncated: bool
    ingestion_truncation_label: str
    rendering_truncated: bool
    rendering_truncation_label: str
    snapshot_scope_note: str = ""


@dataclass(frozen=True)
class SubscriptionDetail:
    topic_filter: str
    qos: int | None
    qos_label: str
    retain_as_published: bool | None
    retain_as_published_label: str
    retain_handling: int | None
    retain_handling_label: str


@dataclass(frozen=True)
class WildcardFilterTopicSummary:
    topic: str
    received_at: str


@dataclass(frozen=True)
class WildcardFilterSummary:
    topic_filter: str
    matching_topic_count: int
    message_count: int
    live_count: int
    cached_count: int
    stale_count: int
    retained_count: int
    topics: tuple[WildcardFilterTopicSummary, ...]


@dataclass(frozen=True)
class TopicTreeNode:
    label: str
    path: str
    selectable: bool
    is_subscription: bool
    is_observed: bool
    children: tuple["TopicTreeNode", ...]
    is_wildcard_filter: bool = False
    badges: tuple[TopicStateBadge, ...] = ()


@dataclass(frozen=True)
class BrokerDisplaySummary:
    id: UUID
    name: str
    endpoint: str
    label: str
    is_active: bool
    connection_status: str
    connection_status_label: str


def collect_visible_topic_paths(
    subscriptions: Iterable[Subscription],
    observed_topics: Iterable[str],
) -> tuple[str, ...]:
    subscriptions = tuple(subscriptions)
    paths = {item.topic_filter for item in subscriptions}
    paths.update(
        topic
        for topic in observed_topics
        if any(
            mqtt_filter_matches(subscription.topic_filter, topic)
            for subscription in subscriptions
        )
    )
    return tuple(sorted(paths, key=lambda value: (value.casefold(), value)))


def matching_subscription(
    subscriptions: Iterable[Subscription],
    topic: str,
) -> Subscription | None:
    if not topic:
        return None
    subscriptions = tuple(subscriptions)
    exact = next(
        (item for item in subscriptions if item.topic_filter == topic),
        None,
    )
    if exact is not None:
        return exact
    matches = [
        item
        for item in subscriptions
        if mqtt_filter_matches(item.topic_filter, topic)
    ]
    if not matches:
        return None
    return min(
        matches,
        key=lambda item: (
            -len(item.topic_filter.replace("#", "").replace("+", "")),
            item.topic_filter.count("#") + item.topic_filter.count("+"),
            item.topic_filter.casefold(),
            item.topic_filter,
        ),
    )


def build_topic_tree(
    paths: Iterable[str],
    subscriptions: Iterable[Subscription] = (),
    observed_topics: Iterable[str] = (),
    snapshot_states: Iterable[SnapshotTopicState] = (),
) -> tuple[TopicTreeNode, ...]:
    subscriptions = tuple(subscriptions)
    subscription_paths = {item.topic_filter for item in subscriptions}
    wildcard_filters = tuple(
        sorted(
            (
                item
                for item in subscriptions
                if mqtt_filter_has_wildcards(item.topic_filter)
            ),
            key=lambda item: (
                item.topic_filter.casefold(),
                item.topic_filter,
            ),
        )
    )
    filter_labels = {
        item.topic_filter: f"Filter {index}"
        for index, item in enumerate(wildcard_filters, start=1)
    }
    observed_paths = set(observed_topics)
    states_by_topic = {state.topic: state for state in snapshot_states}
    root: dict[str, dict] = {}
    for full_path in sorted(set(paths), key=lambda value: (value.casefold(), value)):
        children = root
        partial: list[str] = []
        for segment in full_path.split("/"):
            partial.append(segment)
            node = children.setdefault(
                segment,
                {"path": "/".join(partial), "children": {}},
            )
            children = node["children"]

    def convert(nodes: dict[str, dict]) -> tuple[TopicTreeNode, ...]:
        result: list[TopicTreeNode] = []
        for segment in sorted(nodes, key=lambda value: (value.casefold(), value)):
            node = nodes[segment]
            path = node["path"]
            is_subscription = path in subscription_paths
            is_observed = path in observed_paths
            is_wildcard_filter = (
                is_subscription and mqtt_filter_has_wildcards(path)
            )
            source_filter = (
                matching_subscription(wildcard_filters, path)
                if is_observed and not is_subscription
                else None
            )
            badges = (
                topic_state_badges(states_by_topic[path])
                if path in states_by_topic
                else ()
            )
            if source_filter is not None:
                badges = (
                    TopicStateBadge(
                        "filter-reference",
                        filter_labels[source_filter.topic_filter].replace(
                            "Filter ", "F"
                        ),
                        "filter",
                        source_filter.topic_filter,
                    ),
                    *badges,
                )
            if is_wildcard_filter:
                badges = (
                    TopicStateBadge(
                        "filter",
                        filter_labels[path],
                        "filter",
                        path,
                    ),
                    *badges,
                )
            result.append(
                TopicTreeNode(
                    label=segment or "/",
                    path=path,
                    selectable=is_subscription or is_observed,
                    is_subscription=is_subscription,
                    is_observed=is_observed,
                    children=convert(node["children"]),
                    is_wildcard_filter=is_wildcard_filter,
                    badges=badges,
                )
            )
        return tuple(result)

    return convert(root)


def topic_detail(
    state: MqttObservation | SnapshotTopicState | None,
    topic: str = "",
    dropped_message_count: int = 0,
) -> TopicDetail:
    if state is None:
        return TopicDetail(
            topic=topic,
            has_value=False,
            decoded_payload="Waiting for a message",
            payload_text="",
            payload_base64="",
            raw_payload="-",
            payload_display="No value observed",
            payload_encoding="-",
            payload_size=0,
            payload_size_label="-",
            rendered_payload_size=0,
            is_truncated=False,
            truncation_label="",
            qos=None,
            qos_label="-",
            retained=None,
            retain_label="-",
            received_at="-",
            message_count=0,
            dropped_message_count=dropped_message_count,
            age_seconds=None,
            age_label="-",
            source=None,
            source_label="-",
            status="waiting",
            status_label="Waiting",
            status_detail="No value has been observed for this topic.",
            original_payload_size=0,
            original_payload_size_label="-",
            available_payload_size=0,
            available_payload_size_label="-",
            ingestion_truncated=False,
            ingestion_truncation_label="No",
            rendering_truncated=False,
            rendering_truncation_label="No",
        )

    if isinstance(state, SnapshotTopicState):
        return _snapshot_topic_detail(state, dropped_message_count)

    visible = state.payload[:MAX_RENDERED_PAYLOAD_BYTES]
    payload_size = state.payload_size or len(state.payload)
    is_truncated = payload_size > len(visible)
    truncation_label = (
        f"Payload truncated: showing {len(visible)} of {payload_size} bytes"
        if is_truncated
        else ""
    )
    notice = f"\n\n[{truncation_label}]" if truncation_label else ""
    payload_base64 = b64encode(visible).decode("ascii")
    try:
        payload_text = visible.decode("utf-8")
        decoded = payload_text if is_truncated else _format_json(payload_text)
        payload_display = decoded
        encoding = "UTF-8"
    except UnicodeDecodeError:
        payload_text = "Payload is not valid UTF-8."
        decoded = "Binary payload (see raw payload below)"
        payload_display = payload_base64
        encoding = "Base64"

    return TopicDetail(
        topic=state.topic,
        has_value=True,
        decoded_payload=decoded + notice,
        payload_text=payload_text,
        payload_base64=payload_base64,
        raw_payload=visible.hex(" ") + notice,
        payload_display=payload_display + notice,
        payload_encoding=encoding,
        payload_size=payload_size,
        payload_size_label=(
            f"{payload_size} byte" if payload_size == 1 else f"{payload_size} bytes"
        ),
        rendered_payload_size=len(visible),
        is_truncated=is_truncated,
        truncation_label=truncation_label,
        qos=state.qos,
        qos_label=str(state.qos),
        retained=state.retain,
        retain_label="Yes" if state.retain else "No",
        received_at=_format_datetime(state.recieved_at),
        message_count=state.message_count,
        dropped_message_count=dropped_message_count,
        age_seconds=_observation_age(state.received_at),
        age_label=age_label(_observation_age(state.received_at)),
        source=state.source.value,
        source_label=(
            "Persisted storage"
            if state.source.value == "stored"
            else "Live MQTT observation"
        ),
        status=state.source.value,
        status_label=state.source.value.title(),
        status_detail=(
            "Value was restored from persisted storage."
            if state.source.value == "stored"
            else "Value was received during the current runtime."
        ),
        original_payload_size=payload_size,
        original_payload_size_label=size_label(payload_size),
        available_payload_size=len(state.payload),
        available_payload_size_label=size_label(len(state.payload)),
        ingestion_truncated=payload_size > len(state.payload),
        ingestion_truncation_label=(
            f"Yes - {len(state.payload)} of {payload_size} bytes available"
            if payload_size > len(state.payload)
            else "No"
        ),
        rendering_truncated=len(visible) < len(state.payload),
        rendering_truncation_label=(
            f"Yes - showing {len(visible)} of {len(state.payload)} available bytes"
            if len(visible) < len(state.payload)
            else "No"
        ),
    )


def _snapshot_topic_detail(
    state: SnapshotTopicState,
    dropped_message_count: int,
) -> TopicDetail:
    payload = state.payload
    is_utf8 = payload.encoding == SnapshotPayloadEncoding.UTF8
    rendered_bytes = (
        payload.value.encode("utf-8")
        if is_utf8
        else b64decode(payload.value)
    )
    payload_text = (
        payload.value if is_utf8 else "Payload is not valid UTF-8."
    )
    decoded = _format_json(payload.value) if is_utf8 else (
        "Binary payload (see raw payload below)"
    )
    truncation_label = (
        "Payload truncated: showing "
        f"{payload.rendered_size} of {payload.original_size} bytes"
        if payload.truncated
        else ""
    )
    notice = f"\n\n[{truncation_label}]" if truncation_label else ""
    return TopicDetail(
        topic=state.topic,
        has_value=True,
        decoded_payload=decoded + notice,
        payload_text=payload_text,
        payload_base64=(
            b64encode(rendered_bytes).decode("ascii")
            if is_utf8
            else payload.value
        ),
        raw_payload=rendered_bytes.hex(" ") + notice,
        payload_display=payload.value + notice,
        payload_encoding="UTF-8" if is_utf8 else "Base64",
        payload_size=payload.original_size,
        payload_size_label=size_label(payload.original_size),
        rendered_payload_size=payload.rendered_size,
        is_truncated=payload.truncated,
        truncation_label=truncation_label,
        qos=state.qos,
        qos_label=str(state.qos),
        retained=state.retain,
        retain_label="Yes" if state.retain else "No",
        received_at=state.received_at.isoformat(timespec="seconds"),
        message_count=state.message_count,
        dropped_message_count=dropped_message_count,
        age_seconds=state.age_seconds,
        age_label=age_label(state.age_seconds),
        source=state.source.value,
        source_label=(
            "Persisted storage"
            if state.source.value == "stored"
            else "Live MQTT observation"
        ),
        status=state.status.value,
        status_label=state.status.value.title(),
        status_detail=status_detail(state.status.value),
        original_payload_size=payload.original_size,
        original_payload_size_label=size_label(payload.original_size),
        available_payload_size=payload.available_size,
        available_payload_size_label=size_label(payload.available_size),
        ingestion_truncated=payload.ingestion_truncated,
        ingestion_truncation_label=(
            f"Yes - {payload.available_size} of {payload.original_size} bytes available"
            if payload.ingestion_truncated
            else "No"
        ),
        rendering_truncated=payload.rendering_truncated,
        rendering_truncation_label=(
            "Yes - showing "
            f"{payload.rendered_size} of {payload.available_size} available bytes"
            if payload.rendering_truncated
            else "No"
        ),
    )


def _observation_age(received_at: datetime) -> float:
    value = received_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - value).total_seconds())


def subscription_detail(subscription: Subscription | None) -> SubscriptionDetail:
    if subscription is None:
        return SubscriptionDetail(
            topic_filter="No matching subscription",
            qos=None,
            qos_label="-",
            retain_as_published=None,
            retain_as_published_label="-",
            retain_handling=None,
            retain_handling_label="-",
        )
    return SubscriptionDetail(
        topic_filter=subscription.topic_filter,
        qos=subscription.qos,
        qos_label={
            0: "0 · At most once",
            1: "1 · At least once",
            2: "2 · Exactly once",
        }[subscription.qos],
        retain_as_published=subscription.retain_as_published,
        retain_as_published_label=(
            "Preserve retained flag"
            if subscription.retain_as_published
            else "Rewrite retained flag"
        ),
        retain_handling=subscription.retain_handling,
        retain_handling_label={
            0: "Send retained messages",
            1: "Only for a new subscription",
            2: "Do not send retained messages",
        }[subscription.retain_handling],
    )


def wildcard_filter_summary(
    subscription: Subscription,
    snapshot_states: Iterable[SnapshotTopicState],
) -> WildcardFilterSummary:
    states = tuple(
        sorted(
            (
                state
                for state in snapshot_states
                if mqtt_filter_matches(subscription.topic_filter, state.topic)
            ),
            key=lambda state: (state.topic.casefold(), state.topic),
        )
    )
    return WildcardFilterSummary(
        topic_filter=subscription.topic_filter,
        matching_topic_count=len(states),
        message_count=sum(state.message_count for state in states),
        live_count=sum(state.status.value == "live" for state in states),
        cached_count=sum(state.status.value == "cached" for state in states),
        stale_count=sum(state.status.value == "stale" for state in states),
        retained_count=sum(state.retain for state in states),
        topics=tuple(
            WildcardFilterTopicSummary(
                topic=state.topic,
                received_at=state.received_at.isoformat(timespec="seconds"),
            )
            for state in states
        ),
    )


def broker_display_summary(
    broker: BrokerSummary,
    active_broker_id: UUID,
    connection_status: object,
) -> BrokerDisplaySummary:
    scheme = "mqtts" if broker.config.use_tls else "mqtt"
    endpoint = f"{scheme}://{broker.config.host}:{broker.config.port}"
    status = str(getattr(connection_status, "value", connection_status)).lower()
    return BrokerDisplaySummary(
        id=broker.id,
        name=broker.name,
        endpoint=endpoint,
        label=f"{broker.name} ({endpoint})",
        is_active=broker.id == active_broker_id,
        connection_status=status,
        connection_status_label=status.replace("_", " ").title(),
    )


def _format_json(decoded: str) -> str:
    try:
        value = json.loads(decoded)
        chunks: list[str] = []
        length = 0
        for chunk in json.JSONEncoder(indent=2, ensure_ascii=False).iterencode(value):
            remaining = MAX_FORMATTED_JSON_CHARACTERS - length
            if len(chunk) > remaining:
                chunks.extend((chunk[:remaining], "\n\n[Formatted JSON truncated]"))
                break
            chunks.append(chunk)
            length += len(chunk)
        return "".join(chunks)
    except (json.JSONDecodeError, RecursionError, TypeError):
        return decoded


def _format_datetime(value: datetime) -> str:
    return value.isoformat(timespec="seconds")
