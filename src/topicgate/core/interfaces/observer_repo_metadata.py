from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.subscription import Subscription


class ObserverRepoMetadata(Protocol):
    """Connection and subscription state owned by an MQTT observer."""

    @property
    def connection_status(self) -> object: ...

    @property
    def topic_update_interval(self) -> float: ...

    @property
    def dropped_message_count(self) -> int: ...

    @property
    def recording_failure_count(self) -> int: ...

    @property
    def subscription_failure_count(self) -> int: ...

    @property
    def subscription_rejected_count(self) -> int: ...

    @property
    def connected_at(self) -> datetime | None: ...

    @property
    def observation_started_at(self) -> datetime | None: ...

    @property
    def subscriptions(self) -> tuple[Subscription, ...]: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def connection_statuses(self) -> AsyncIterator[object]: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def reconnect(self) -> None: ...

    async def update_broker(
        self,
        new_config: MqttConfig,
        subscriptions: tuple[Subscription, ...] | None = None,
    ) -> None: ...

    async def add_subscription(self, subscription: Subscription, /) -> None: ...

    async def update_subscription(
        self,
        original_filter: str,
        subscription: Subscription,
        /,
    ) -> None: ...

    async def remove_subscription(self, subscription: Subscription, /) -> None: ...

    async def publish(self, topic: str, payload: bytes, /) -> None: ...
