import asyncio
import base64
import math
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

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
from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.interfaces.current_topic_reader import CurrentTopicReader
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.mqtt_observation import ObservationSource
from topicgate.core.models.observation_status import ObservationStatus
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.topic_message import TopicMessage
from topicgate.core.mqtt_topics import mqtt_filter_matches
from topicgate.core.observer_limits import MAX_OBSERVED_TOPICS
from topicgate.core.payload_limits import MAX_RENDERED_PAYLOAD_BYTES

DEFAULT_SNAPSHOT_RESULT_LIMIT = 100
MAX_SNAPSHOT_RESULT_LIMIT = MAX_OBSERVED_TOPICS
DEFAULT_SNAPSHOT_WAIT_SECONDS = 1.0
MAX_SNAPSHOT_WAIT_SECONDS = 5.0

Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class BrokerSnapshotService:
    """Build bounded broker snapshots for any application presentation layer."""

    def __init__(
        self,
        runtime: TopicGateRuntime,
        *,
        current_topics: CurrentTopicReader | None = None,
        resolver: BrokerResolver | None = None,
        clock: Clock | None = None,
        monotonic_clock: MonotonicClock | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        self._runtime = runtime
        self._current_topics = current_topics or runtime
        self._resolver = resolver or BrokerResolver(runtime)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._sleep = sleep or asyncio.sleep

    async def build(
        self,
        broker: UUID | str,
        *,
        topic_filter: str = "#",
        max_age_seconds: float | None = None,
        result_limit: int = DEFAULT_SNAPSHOT_RESULT_LIMIT,
        payload_limit_bytes: int = MAX_RENDERED_PAYLOAD_BYTES,
    ) -> BrokerSnapshot:
        return self.build_current(
            broker,
            topic_filter=topic_filter,
            max_age_seconds=max_age_seconds,
            result_limit=result_limit,
            payload_limit_bytes=payload_limit_bytes,
        )

    def build_current(
        self,
        broker: UUID | str,
        *,
        topic_filter: str = "#",
        max_age_seconds: float | None = None,
        result_limit: int = DEFAULT_SNAPSHOT_RESULT_LIMIT,
        payload_limit_bytes: int = MAX_RENDERED_PAYLOAD_BYTES,
    ) -> BrokerSnapshot:
        """Build current broker state without activation, waiting, or I/O."""
        resolved = self._resolver.resolve(broker)
        return self.build_resolved_current(
            resolved,
            topic_filter=topic_filter,
            max_age_seconds=max_age_seconds,
            result_limit=result_limit,
            payload_limit_bytes=payload_limit_bytes,
        )

    def build_resolved_current(
        self,
        broker: BrokerSummary,
        *,
        topic_filter: str = "#",
        max_age_seconds: float | None = None,
        result_limit: int = DEFAULT_SNAPSHOT_RESULT_LIMIT,
        payload_limit_bytes: int = MAX_RENDERED_PAYLOAD_BYTES,
    ) -> BrokerSnapshot:
        """Build current state from an already-safe broker summary."""
        validated_filter = self._validate_topic_filter(topic_filter)
        max_age_seconds = self._validate_max_age(max_age_seconds)
        result_limit = self._validate_result_limit(result_limit)
        payload_limit_bytes = self._validate_payload_limit(payload_limit_bytes)

        return self._capture(
            broker,
            topic_filter=validated_filter,
            max_age_seconds=max_age_seconds,
            result_limit=result_limit,
            payload_limit_bytes=payload_limit_bytes,
            requested_wait_seconds=0.0,
            actual_wait_seconds=0.0,
        )

    async def observe(
        self,
        broker: UUID | str,
        *,
        topic_filter: str = "#",
        max_age_seconds: float | None = None,
        result_limit: int = DEFAULT_SNAPSHOT_RESULT_LIMIT,
        payload_limit_bytes: int = MAX_RENDERED_PAYLOAD_BYTES,
        wait_seconds: float = DEFAULT_SNAPSHOT_WAIT_SECONDS,
    ) -> BrokerSnapshot:
        resolved = self._resolver.resolve(broker)
        validated_filter = self._validate_topic_filter(topic_filter)
        max_age_seconds = self._validate_max_age(max_age_seconds)
        result_limit = self._validate_result_limit(result_limit)
        payload_limit_bytes = self._validate_payload_limit(payload_limit_bytes)
        wait_seconds = self._validate_wait_seconds(wait_seconds)

        with self._runtime.control_operation("reconnect and observe"):
            await self._runtime.activate_broker(resolved.id)
            actual_wait_seconds = await self._wait(wait_seconds)
            return self._capture(
                resolved,
                topic_filter=validated_filter,
                max_age_seconds=max_age_seconds,
                result_limit=result_limit,
                payload_limit_bytes=payload_limit_bytes,
                requested_wait_seconds=wait_seconds,
                actual_wait_seconds=actual_wait_seconds,
            )

    def _capture(
        self,
        resolved: BrokerSummary,
        *,
        topic_filter: str,
        max_age_seconds: float | None,
        result_limit: int,
        payload_limit_bytes: int,
        requested_wait_seconds: float,
        actual_wait_seconds: float,
    ) -> BrokerSnapshot:
        captured_at = self._as_utc(self._clock())
        matching = sorted(
            (
                current
                for current in self._current_topics.get_current_topics(resolved.id)
                if mqtt_filter_matches(topic_filter, current.message.topic)
            ),
            key=lambda current: current.message.topic,
        )
        aged = tuple(
            (
                current,
                self._age_seconds(captured_at, current.message.received_at),
            )
            for current in matching
        )
        fresh = tuple(
            (current, age)
            for current, age in aged
            if max_age_seconds is None or age <= max_age_seconds
        )
        stale_count = len(aged) - len(fresh)
        selected = fresh[:result_limit]
        omitted_by_limit = len(fresh) - len(selected)
        status = self._runtime.get_connection_status(resolved.id)
        dropped_message_count = self._runtime.get_dropped_message_count(
            resolved.id
        )
        connected_at = self._runtime.get_connected_at(resolved.id)
        observation_started_at = self._runtime.get_observation_started_at(
            resolved.id
        )
        topics = tuple(
            self._topic_snapshot(
                current,
                age,
                payload_limit_bytes,
                observation_started_at=observation_started_at,
            )
            for current, age in selected
        )

        observed_for_seconds = (
            None
            if observation_started_at is None
            else self._age_seconds(captured_at, observation_started_at)
        )
        limitations = self._limitations(
            status=status,
            matching=matching,
            topics=topics,
            observation_started_at=observation_started_at,
            dropped_message_count=dropped_message_count,
            stale_count=stale_count,
            omitted_by_limit=omitted_by_limit,
        )

        return BrokerSnapshot(
            broker=SnapshotBrokerIdentity(resolved.id, resolved.name),
            connection_status=self._status_value(status),
            captured_at=captured_at,
            connected_at=(
                None if connected_at is None else self._as_utc(connected_at)
            ),
            observation_started_at=(
                None
                if observation_started_at is None
                else self._as_utc(observation_started_at)
            ),
            observed_for_seconds=observed_for_seconds,
            topic_filter=topic_filter,
            topics=topics,
            dropped_message_count=dropped_message_count,
            freshness=SnapshotFreshness(max_age_seconds, stale_count),
            results=SnapshotResultLimit(
                limit=result_limit,
                total=len(aged),
                returned=len(topics),
                omitted=len(aged) - len(topics),
                omitted_as_stale=stale_count,
                omitted_by_limit=omitted_by_limit,
                truncated=omitted_by_limit > 0,
            ),
            settling=SnapshotSettling(
                requested_seconds=requested_wait_seconds,
                maximum_seconds=MAX_SNAPSHOT_WAIT_SECONDS,
                actual_seconds=actual_wait_seconds,
            ),
            completeness=SnapshotCompleteness(False, limitations),
        )

    async def _wait(self, wait_seconds: float) -> float:
        if wait_seconds == 0:
            return 0.0
        started_at = self._monotonic_clock()
        await self._sleep(wait_seconds)
        return max(0.0, self._monotonic_clock() - started_at)

    @classmethod
    def _topic_snapshot(
        cls,
        current: CurrentTopic,
        age_seconds: float,
        payload_limit_bytes: int,
        *,
        observation_started_at: datetime | None,
    ) -> SnapshotTopicState:
        message = current.message
        return SnapshotTopicState(
            topic=message.topic,
            payload=cls._render_payload(message, payload_limit_bytes),
            qos=message.qos,
            retain=message.retain,
            received_at=cls._as_utc(message.received_at),
            age_seconds=age_seconds,
            message_count=message.message_count,
            source=(
                ObservationSource.STORED
                if current.status is ObservationStatus.CACHED
                else ObservationSource.LIVE
            ),
            status=cls._topic_status(current, observation_started_at),
        )

    @classmethod
    def _topic_status(
        cls,
        current: CurrentTopic,
        observation_started_at: datetime | None,
    ) -> SnapshotTopicStatus:
        message = current.message
        if (
            observation_started_at is not None
            and cls._as_utc(message.received_at)
            < cls._as_utc(observation_started_at)
        ):
            return SnapshotTopicStatus.STALE
        if current.status is ObservationStatus.CACHED:
            return SnapshotTopicStatus.CACHED
        return SnapshotTopicStatus.LIVE

    @staticmethod
    def _render_payload(
        message: TopicMessage,
        payload_limit_bytes: int,
    ) -> SnapshotPayload:
        available = bytes(message.payload)
        original_size = max(message.payload_size, len(available))
        rendered = available[:payload_limit_bytes]
        try:
            available.decode("utf-8")
        except UnicodeDecodeError:
            encoding = SnapshotPayloadEncoding.BASE64
            value = base64.b64encode(rendered).decode("ascii")
            rendered_size = len(rendered)
        else:
            encoding = SnapshotPayloadEncoding.UTF8
            value = rendered.decode("utf-8", errors="ignore")
            rendered_size = len(value.encode("utf-8"))

        ingestion_truncated = original_size > len(available)
        rendering_truncated = rendered_size < len(available)
        return SnapshotPayload(
            encoding=encoding,
            value=value,
            original_size=original_size,
            available_size=len(available),
            rendered_size=rendered_size,
            ingestion_truncated=ingestion_truncated,
            rendering_truncated=rendering_truncated,
            truncated=ingestion_truncated or rendering_truncated,
        )

    @staticmethod
    def _limitations(
        *,
        status: object,
        matching: list[CurrentTopic],
        topics: tuple[SnapshotTopicState, ...],
        observation_started_at: datetime | None,
        dropped_message_count: int,
        stale_count: int,
        omitted_by_limit: int,
    ) -> tuple[SnapshotLimitation, ...]:
        limitations = [
            SnapshotLimitation.CURRENT_STATE_ONLY,
            SnapshotLimitation.RETAINED_DELIVERY_UNCONFIRMED,
        ]
        if BrokerSnapshotService._status_value(status) != ConnectionStatus.CONNECTED:
            limitations.append(SnapshotLimitation.BROKER_DISCONNECTED)
        if observation_started_at is None:
            limitations.append(SnapshotLimitation.OBSERVATION_NOT_STARTED)
        if any(
            current.status is ObservationStatus.CACHED for current in matching
        ):
            limitations.append(
                SnapshotLimitation.STORED_STATE_PREDATES_OBSERVATION
            )
        if dropped_message_count:
            limitations.append(SnapshotLimitation.DROPPED_MESSAGES)
        if stale_count:
            limitations.append(SnapshotLimitation.STALE_STATES_OMITTED)
        if omitted_by_limit:
            limitations.append(SnapshotLimitation.RESULT_LIMIT_REACHED)
        if any(topic.payload.truncated for topic in topics):
            limitations.append(SnapshotLimitation.PAYLOAD_TRUNCATED)
        return tuple(limitations)

    @staticmethod
    def _validate_topic_filter(topic_filter: str) -> str:
        return Subscription(topic_filter).topic_filter

    @staticmethod
    def _validate_max_age(max_age_seconds: float | None) -> float | None:
        if max_age_seconds is None:
            return None
        value = float(max_age_seconds)
        if not math.isfinite(value) or value < 0:
            raise ValueError("max_age_seconds must be a finite non-negative value.")
        return value

    @staticmethod
    def _validate_result_limit(result_limit: int) -> int:
        if isinstance(result_limit, bool) or not isinstance(result_limit, int):
            raise ValueError("result_limit must be an integer.")
        if not 1 <= result_limit <= MAX_SNAPSHOT_RESULT_LIMIT:
            raise ValueError(
                f"result_limit must be between 1 and {MAX_SNAPSHOT_RESULT_LIMIT}."
            )
        return result_limit

    @staticmethod
    def _validate_payload_limit(payload_limit_bytes: int) -> int:
        if isinstance(payload_limit_bytes, bool) or not isinstance(
            payload_limit_bytes, int
        ):
            raise ValueError("payload_limit_bytes must be an integer.")
        if not 0 <= payload_limit_bytes <= MAX_RENDERED_PAYLOAD_BYTES:
            raise ValueError(
                "payload_limit_bytes must be between 0 and "
                f"{MAX_RENDERED_PAYLOAD_BYTES}."
            )
        return payload_limit_bytes

    @staticmethod
    def _validate_wait_seconds(wait_seconds: float) -> float:
        value = float(wait_seconds)
        if not math.isfinite(value) or not 0 <= value <= MAX_SNAPSHOT_WAIT_SECONDS:
            raise ValueError(
                "wait_seconds must be between 0 and "
                f"{MAX_SNAPSHOT_WAIT_SECONDS}."
            )
        return value

    @staticmethod
    def _age_seconds(captured_at: datetime, observed_at: datetime) -> float:
        return max(
            0.0,
            (captured_at - BrokerSnapshotService._as_utc(observed_at)).total_seconds(),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _status_value(status: object) -> str:
        return str(getattr(status, "value", status)).lower()
