from collections.abc import AsyncIterator, Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from topicgate.app.services.service_item import ServiceItem
from topicgate.app.services.observation_cache_service import ObservationCacheService
from topicgate.app.services.observation_query_service import ObservationQueryService
from topicgate.app.services.control_operation_service import ControlOperationService
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.interfaces.broker_profile_store import BrokerProfileStore
from topicgate.core.interfaces.current_topic_reader import CurrentTopicReader
from topicgate.core.interfaces.observer_repository import ObserverRepository
from topicgate.core.models.broker_profile import BrokerProfile
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.message_filter import MessageFilter
from topicgate.core.models.mqtt_message import MqttMessage
from topicgate.core.models.mqtt_observation import MqttObservation
from topicgate.core.models.observation_deletion_preview import (
    ObservationDeletionPreview,
)
from topicgate.core.models.observation_cache_administration import (
    BrokerCacheUsage,
    CacheUsageSummary,
    ObservationDeletionResult,
    PersistedTopicSummary,
    RetentionPolicyApplicationResult,
    RetentionPolicyPreview,
)
from topicgate.core.models.observation_retention_policy import (
    ObservationRetentionPolicy,
)
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.topic_message import TopicMessage


class TopicGateRuntime(ServiceItem):
    """Qt-independent application service for broker-backed topic access."""

    def __init__(
        self,
        broker_repository: BrokerProfileStore,
        mqtt_repositories: dict[UUID, ObserverRepository],
        active_broker_id: UUID | None = None,
        mqtt_repository_factory: Callable[
            [BrokerProfile], ObserverRepository
        ] | None = None,
        observation_cache: ObservationCacheService | None = None,
        control_operations: ControlOperationService | None = None,
        observation_query: ObservationQueryService | None = None,
        current_topics: CurrentTopicReader | None = None,
    ) -> None:
        self._brokers = broker_repository
        self._active_broker_id = (
            broker_repository.get_profile().id
            if active_broker_id is None
            else active_broker_id
        )
        self._mqtt_repositories = mqtt_repositories
        self._mqtt_repository_factory = mqtt_repository_factory
        self._observation_cache = observation_cache
        self._observation_query = observation_query
        self._current_topics = current_topics
        self._control_operations = control_operations
        if self._active_broker_id not in self._mqtt_repositories:
            raise ValueError("The active broker requires an MQTT repository.")

    @property
    def active_repo(self) -> ObserverRepository:
        return self._mqtt_repositories[self._active_broker_id]

    async def start(self) -> None:
        await self.active_repo.start()

    async def stop(self) -> None:
        await self.active_repo.stop()

    def list_brokers(self) -> tuple[BrokerSummary, ...]:
        return tuple(
            self._broker_summary(profile)
            for profile in self._brokers.get_all_profiles()
        )

    def list_topics(self, broker_id: UUID | None = None) -> tuple[str, ...]:
        selected_id = self._active_broker_id if broker_id is None else broker_id
        return tuple(
            current.message.topic
            for current in self.get_current_topics(selected_id)
        )

    def get_message(self, message_id: UUID) -> TopicMessage:
        return self._require_observation_query().get_message(message_id)

    def get_current_topics(self, broker_id: UUID) -> tuple[CurrentTopic, ...]:
        self._get_broker_profile(broker_id)
        return self._require_current_topics().get_current_topics(broker_id)

    def get_current_topic(
        self,
        broker_id: UUID,
        topic: str,
    ) -> CurrentTopic | None:
        self._get_broker_profile(broker_id)
        return self._require_current_topics().get_current_topic(broker_id, topic)

    def query_stored_observations(
        self, message_filter: MessageFilter
    ) -> tuple[TopicMessage, ...]:
        self._get_broker_profile(message_filter.broker_id)
        return self._require_observation_query().query_stored_observations(
            message_filter
        )

    def get_observation_storage_summary(
        self,
        broker: UUID | None = None,
    ) -> CacheUsageSummary:
        """Return persisted observation storage usage for one or all brokers."""
        if broker is not None:
            self._get_broker_profile(broker)

        usage = self.get_cache_usage()
        if broker is None:
            return usage

        return CacheUsageSummary(
            tuple(item for item in usage.brokers if item.broker_id == broker)
        )

    def get_broker(self, broker_id: UUID | None = None) -> BrokerSummary:
        return self._broker_summary(self._get_broker_profile(broker_id))

    @property
    def active_broker(self) -> BrokerSummary:
        return self.get_broker()

    @property
    def mqtt_config(self) -> MqttConfig:
        return self.active_broker.config

    def get_topic_state(
        self,
        broker_id: UUID,
        topic: str,
    ) -> MqttObservation | None:
        current = self.get_current_topic(broker_id, topic)
        return None if current is None else current.to_observation()

    def list_subscriptions(self, broker_id: UUID) -> tuple[Subscription, ...]:
        return tuple(self._mqtt_repositories[broker_id].subscriptions)

    def get_retention_policy(self) -> ObservationRetentionPolicy:
        return self._require_observation_cache().get_retention_policy()

    def update_retention_policy(
        self,
        policy: ObservationRetentionPolicy,
    ) -> ObservationRetentionPolicy:
        with self.control_operation("update retention policy"):
            return self._require_observation_cache().update_retention_policy(policy)

    def get_cache_usage(self) -> CacheUsageSummary:
        usage = self._require_observation_query().get_cache_usage()
        by_broker = {item.broker_id: item for item in usage.brokers}
        return CacheUsageSummary(
            tuple(
                by_broker.get(
                    broker.id,
                    BrokerCacheUsage(broker.id, 0, 0, None, None),
                )
                for broker in self.list_brokers()
            )
        )

    def list_persisted_topics(
        self,
        broker_id: UUID,
    ) -> tuple[PersistedTopicSummary, ...]:
        self._get_broker_profile(broker_id)
        return self._require_observation_query().get_persisted_topics(
            broker_id,
            self.list_subscriptions(broker_id),
        )

    def preview_retention_policy(
        self,
        policy: ObservationRetentionPolicy,
    ) -> RetentionPolicyPreview:
        return self._require_observation_cache().preview_retention_policy(
            policy,
            self._subscriptions_by_broker(),
        )

    def confirm_retention_policy(
        self,
        preview: RetentionPolicyPreview,
    ) -> RetentionPolicyApplicationResult:
        with self.control_operation("enforce retention policy"):
            result = self._require_observation_cache().confirm_retention_policy(
                preview,
                self._subscriptions_by_broker(),
            )
            return result

    def preview_clear_cache(
        self,
        broker_id: UUID,
        topics: tuple[str, ...] | None = None,
    ) -> ObservationDeletionPreview:
        self._get_broker_profile(broker_id)
        return self._require_observation_cache().preview_clear_cache(
            broker_id,
            topics,
        )

    def preview_unsubscribed_cache(
        self,
        broker_id: UUID,
    ) -> ObservationDeletionPreview:
        self._get_broker_profile(broker_id)
        return self._require_observation_cache().preview_unsubscribed(
            broker_id,
            self.list_subscriptions(broker_id),
        )

    def preview_all_cache(self) -> ObservationDeletionPreview:
        return self._require_observation_cache().preview_all()

    def confirm_cache_deletion_detailed(
        self,
        preview: ObservationDeletionPreview,
    ) -> ObservationDeletionResult:
        with self.control_operation("delete stored observations"):
            for broker_id in preview.broker_ids:
                self._get_broker_profile(broker_id)
            result = self._require_observation_cache().confirm_deletion_detailed(
                preview
            )
            return result

    def confirm_cache_deletion(
        self,
        preview: ObservationDeletionPreview,
    ) -> int:
        with self.control_operation("delete stored observations"):
            if preview.broker_id is not None:
                self._get_broker_profile(preview.broker_id)
            return self._require_observation_cache().confirm_deletion(preview)

    @property
    def connection_status(self) -> object:
        return self.get_connection_status(self._active_broker_id)

    def get_connection_status(self, broker_id: UUID) -> object:
        return self._repository_for(broker_id).connection_status

    @property
    def dropped_message_count(self) -> int:
        return self.get_dropped_message_count(self._active_broker_id)

    def get_dropped_message_count(self, broker_id: UUID) -> int:
        return self._repository_for(broker_id).dropped_message_count

    def get_recording_failure_count(self, broker_id: UUID) -> int:
        return self._repository_for(broker_id).recording_failure_count

    def get_subscription_failure_count(self, broker_id: UUID) -> int:
        return self._repository_for(broker_id).subscription_failure_count

    def get_subscription_rejected_count(self, broker_id: UUID) -> int:
        return self._repository_for(broker_id).subscription_rejected_count

    def get_connected_at(self, broker_id: UUID) -> datetime | None:
        return getattr(self._repository_for(broker_id), "connected_at", None)

    def get_observation_started_at(self, broker_id: UUID) -> datetime | None:
        return getattr(
            self._repository_for(broker_id),
            "observation_started_at",
            None,
        )

    @property
    def topic_update_interval(self) -> float:
        return self.get_topic_update_interval(self._active_broker_id)

    def get_topic_update_interval(self, broker_id: UUID) -> float:
        return self._repository_for(broker_id).topic_update_interval

    def messages(self) -> AsyncIterator[MqttMessage]:
        return self.active_repo.messages()

    def drain_pending_messages(self) -> tuple[MqttMessage, ...]:
        return self.active_repo.drain_pending_messages()

    def connection_statuses(self) -> AsyncIterator[object]:
        return self.active_repo.connection_statuses()

    async def connect(self) -> None:
        with self.control_operation("connect broker"):
            await self.active_repo.connect()

    async def disconnect(self) -> None:
        with self.control_operation("disconnect broker"):
            await self.active_repo.disconnect()

    async def reconnect(self) -> None:
        with self.control_operation("reconnect broker"):
            await self.active_repo.reconnect()

    def create_broker(self, name: str, mqtt_config: MqttConfig) -> BrokerSummary:
        with self.control_operation("create broker profile"):
            if self._mqtt_repository_factory is None:
                raise RuntimeError("An observer repository factory is required.")
            profile = self._brokers.create_profile(name, mqtt_config)
            self._mqtt_repositories[profile.id] = self._mqtt_repository_factory(profile)
            return self._broker_summary(profile)

    def update_broker(
        self,
        broker_id: UUID,
        mqtt_config: MqttConfig,
        name: str | None = None,
    ) -> BrokerSummary:
        with self.control_operation("update broker profile"):
            profile = self._get_broker_profile(broker_id)
            config = self._config_with_stored_password(profile, mqtt_config)
            profile.name = (
                self._validated_profile_name(name, broker_id)
                if name is not None
                else profile.name
            )
            profile.config = config
            self._brokers.update_profile(profile)
            return self.get_broker(broker_id)

    async def activate_broker(
        self,
        broker_id: UUID,
        mqtt_config: MqttConfig | None = None,
        name: str | None = None,
    ) -> BrokerSummary:
        with self.control_operation("activate broker profile"):
            return await self._activate_broker(broker_id, mqtt_config, name)

    async def _activate_broker(
        self,
        broker_id: UUID,
        mqtt_config: MqttConfig | None,
        name: str | None,
    ) -> BrokerSummary:
        profile = self._get_broker_profile(broker_id)
        config = (
            profile.config
            if mqtt_config is None
            else self._config_with_stored_password(profile, mqtt_config)
        )
        normalized_name = (
            self._validated_profile_name(name, broker_id)
            if name is not None
            else profile.name
        )
        previous_repo = self.active_repo
        selected_repo = self._mqtt_repositories[broker_id]
        switching_repositories = selected_repo is not previous_repo
        profile.name = normalized_name
        profile.config = config
        self._brokers.update_profile(profile)
        self._brokers.select_active_profile(broker_id)
        self._active_broker_id = broker_id
        if switching_repositories:
            await previous_repo.stop()
        await selected_repo.update_broker(
            config,
            subscriptions=profile.workspace.subscriptions,
        )
        return self.active_broker

    async def delete_broker(self, broker_id: UUID) -> BrokerSummary:
        with self.control_operation("delete broker profile"):
            profile = self._get_broker_profile(broker_id)
            profiles = self.list_brokers()
            if len(profiles) == 1:
                raise ValueError("At least one broker profile is required.")
            if profile.id == self.active_broker.id:
                replacement = next(item for item in profiles if item.id != profile.id)
                await self.activate_broker(replacement.id)
            if self._observation_cache is not None:
                self._observation_cache.flush_pending_writes()
            deleted = self._brokers.delete_profile(profile.id)
            self._mqtt_repositories.pop(profile.id)
            return self._broker_summary(deleted)

    async def add_subscription(
        self,
        broker_id: UUID,
        subscription: Subscription,
    ) -> None:
        with self.control_operation("add subscription"):
            self._require_active_broker(broker_id)
            await self.active_repo.add_subscription(subscription)
            self._persist_active_subscriptions()

    async def update_subscription(
        self,
        broker_id: UUID,
        original_filter: str,
        subscription: Subscription,
    ) -> ObservationDeletionResult | None:
        with self.control_operation("update subscription"):
            self._require_active_broker(broker_id)
            await self.active_repo.update_subscription(original_filter, subscription)
            self._persist_active_subscriptions()
            return self._cleanup_unsubscribed_if_enabled(broker_id)

    async def remove_subscription(
        self,
        broker_id: UUID,
        subscription: Subscription,
    ) -> ObservationDeletionResult | None:
        with self.control_operation("remove subscription"):
            self._require_active_broker(broker_id)
            await self.active_repo.remove_subscription(subscription)
            self._persist_active_subscriptions()
            return self._cleanup_unsubscribed_if_enabled(broker_id)

    async def publish(self, broker_id: UUID, topic: str, payload: bytes) -> None:
        with self.control_operation("publish MQTT message"):
            self._require_active_broker(broker_id)
            await self.active_repo.publish(topic, payload)

    def control_operation(self, name: str):
        if self._control_operations is None:
            return nullcontext()
        return self._control_operations.operation(name)

    def check_control_ownership(self) -> None:
        if self._control_operations is not None:
            self._control_operations.check_ownership()

    def _persist_active_subscriptions(self) -> None:
        workspace_id = self._get_broker_profile().workspace_id
        self._brokers.replace_subscriptions(
            workspace_id,
            tuple(self.active_repo.subscriptions),
        )

    def _cleanup_unsubscribed_if_enabled(
        self,
        broker_id: UUID,
    ) -> ObservationDeletionResult | None:
        cache = self._observation_cache
        if cache is None or not cache.get_retention_policy().auto_remove_unsubscribed:
            return None
        preview = cache.preview_unsubscribed(
            broker_id,
            self.list_subscriptions(broker_id),
        )
        if preview.entries:
            return cache.confirm_deletion_detailed(preview)
        return None

    def _subscriptions_by_broker(
        self,
    ) -> dict[UUID, tuple[Subscription, ...]]:
        return {
            broker.id: self.list_subscriptions(broker.id)
            for broker in self.list_brokers()
        }

    def _require_active_broker(self, broker_id: UUID) -> None:
        if broker_id != self.active_broker.id:
            raise ValueError("The operation requires the active broker profile.")

    def _repository_for(self, broker_id: UUID | None) -> ObserverRepository:
        selected_id = self._active_broker_id if broker_id is None else broker_id
        try:
            return self._mqtt_repositories[selected_id]
        except KeyError as error:
            raise KeyError(f"Unknown broker runtime: {selected_id}") from error

    def _require_observation_cache(self) -> ObservationCacheService:
        if self._observation_cache is None:
            raise RuntimeError("Observation cache operations are unavailable.")
        return self._observation_cache

    def _require_observation_query(self) -> ObservationQueryService:
        if self._observation_query is None:
            raise RuntimeError("Observation query operations are unavailable.")
        return self._observation_query

    def _require_current_topics(self) -> CurrentTopicReader:
        if self._current_topics is None:
            raise RuntimeError("Current topic reads are unavailable.")
        return self._current_topics

    def _validated_profile_name(self, name: str, broker_id: UUID) -> str:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("A broker profile name is required.")
        if any(
            profile.id != broker_id
            and profile.name.casefold() == normalized_name.casefold()
            for profile in self.list_brokers()
        ):
            raise ValueError("A broker profile with that name already exists.")
        return normalized_name

    def _get_broker_profile(self, broker_id: UUID | None = None) -> BrokerProfile:
        return self._brokers.get_profile(broker_id)

    @staticmethod
    def _config_with_stored_password(
        profile: BrokerProfile,
        mqtt_config: MqttConfig,
    ) -> MqttConfig:
        if mqtt_config.password or not profile.config.password:
            return mqtt_config
        return replace(mqtt_config, password=profile.config.password)

    @staticmethod
    def _broker_summary(profile: BrokerProfile) -> BrokerSummary:
        config = profile.config
        return BrokerSummary(
            id=profile.id,
            name=profile.name,
            config=MqttConfig(
                host=config.host,
                port=config.port,
                username=config.username,
                password="",
                use_tls=config.use_tls,
                id=config.id,
            ),
            password_configured=bool(config.password),
        )
