from pathlib import Path
from topicgate.app.services.topic_history_service import TopicHistoryService
from topicgate.app.services.history_retention_service import HistoryRetentionService
from topicgate.infrastructure.repository.history_retention_repository import HistoryRetentionRepository
from topicgate.app.services.history_recording_service import HistoryRecordingService
from topicgate.app.services.observation_recorder import ObservationRecorder
from topicgate.infrastructure.repository.history_recording_repository import HistoryRecordingRepository
from topicgate.infrastructure.repository.observation_history_repository import ObservationHistoryRepository

from topicgate.app.services.broker_health_monitor import BrokerHealthMonitor
from topicgate.app.services.broker_health_wait_service import BrokerHealthWaitService
from topicgate.app.services.expectation_management_service import (
    ExpectationManagementService,
)
from topicgate.app.services.failure_history_service import FailureHistoryService
from topicgate.app.services.health_report_service import HealthReportService
from topicgate.app.services.health_query_service import HealthQueryService
from topicgate.app.services.health_expectation_service import HealthExpectationService
from topicgate.app.services.observation_query_service import ObservationQueryService
from topicgate.app.services.service_item import ServiceItem
from topicgate.app.services.persistence_lifecycle import PersistenceLifecycle
from topicgate.app.services.broker_profile_service import BrokerProfileService
from topicgate.app.services.observation_cache_service import ObservationCacheService
from topicgate.app.services.observation_retention_policy_service import (
    ObservationRetentionPolicyService,
)
from topicgate.app.services.broker_snapshot_service import BrokerSnapshotService
from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.app.services.control_operation_service import ControlOperationService
from topicgate.app.services.mcp_setup_service import McpSetupService
from topicgate.app.services.integration_service import IntegrationService
from topicgate.app.broker_runtime_state import BrokerRuntimeState
from topicgate.app.services.support_bundle_export_service import (
    SupportBundleExporter,
)
from topicgate.app.services.support_bundle_service import SupportBundleService
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.interfaces.observer_repository import ObserverRepository
from topicgate.core.models.broker_profile import BrokerProfile
from topicgate.core.models.health import ActionKind
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.credentials.credential_store import CredentialStore
from topicgate.infrastructure.credentials.os_credential_store import OSCredentialStore
from topicgate.infrastructure.health_actions.log_health_action import LogHealthAction
from topicgate.infrastructure.health_actions.persist_failure_action import (
    PersistFailureAction,
)
from topicgate.infrastructure.support_bundle_archive import (
    SupportBundleArchiveWriter,
)
from topicgate.infrastructure.repository.expectation_failure_repository import (
    ExpectationFailureRepository,
)
from topicgate.infrastructure.repository.health_expectation_repository import (
    HealthExpectationRepository,
)
from topicgate.infrastructure.repository.diagnostic_profile_repository import SqlDiagnosticProfileRepository
from topicgate.infrastructure.diagnostic_packs import DiagnosticPackRegistry, load_zigbee2mqtt_pack
from topicgate.app.services.diagnostic_profile_service import DiagnosticProfileService
from topicgate.app.services.diagnostic_profile_editor import DiagnosticProfileEditor
from topicgate.infrastructure.repository.expectation_state_repository import (
    ExpectationStateRepository,
)
from topicgate.infrastructure.repository.observer_mqtt_repository import (
    ObserverMqttRepository,
)
from topicgate.infrastructure.repository.observation_retention_policy_repository import (
    ObservationRetentionPolicyRepository,
)
from topicgate.infrastructure.repository.topic_message_repository import (
    TopicMessageRepository,
)
from topicgate.paths import prepare_database_path, sqlite_url
from topicgate.processors.action_dispatcher import ActionDispatcher
from topicgate.processors.health_action_registry import HealthActionRegistry
from topicgate.processors.transition_tracker import TransitionTracker


class AppDependencies:
    """Build application components and expose their lifecycle order."""

    def __init__(
        self,
        data_dir: Path | None = None,
        credential_store: CredentialStore | None = None,
        *,
        control_owner: str = "application",
    ) -> None:

        database_path = prepare_database_path(data_dir)
        self._db_context = DatabaseContext(sqlite_url(database_path))
        self.database_path = database_path
        self.credential_store = (
            OSCredentialStore() if credential_store is None else credential_store
        )

        self.broker_runtime_state = BrokerRuntimeState()
        self.observation_retention_policy = ObservationRetentionPolicyRepository(
            self._db_context
        )
        self.retention_policy = ObservationRetentionPolicyService(
            self.observation_retention_policy
        )
        self.topic_messages = TopicMessageRepository(
            self._db_context,
            policy_provider=self.retention_policy.get,
        )
        self.observation_cache = ObservationCacheService(
            self.topic_messages,
            self.retention_policy,
            administrator=self.topic_messages,
        )
        self.observation_query = ObservationQueryService(self.topic_messages)
        self.history_repository = ObservationHistoryRepository(self._db_context)
        self.history_retention_repository = HistoryRetentionRepository(self._db_context)
        self.history_retention = HistoryRetentionService(self.history_retention_repository)
        self.history_recording_repository = HistoryRecordingRepository(self._db_context)
        self.history_recording = HistoryRecordingService(
            self.history_repository, self.history_recording_repository,
        )
        self.observation_recorder = ObservationRecorder(
            self.topic_messages, self.topic_messages.record_canonical_message,
            self.history_recording,
        )
        self.topic_history = TopicHistoryService(
            self.history_repository, self.history_retention, self.history_recording.status,
        )
        self.control_operations = ControlOperationService(
            self._db_context,
            control_owner,
        )
        self.persistence = PersistenceLifecycle(
            self.topic_messages,
            self._db_context,
            self.history_recording,
        )
        self.broker_profiles = BrokerProfileService(
            self._db_context,
            credential_store=self.credential_store,
            runtime_state=self.broker_runtime_state,
            topic_messages=self.topic_messages,
        )

        self.diagnostic_pack_registry = DiagnosticPackRegistry((load_zigbee2mqtt_pack(),))
        self.health_expectation_repo = HealthExpectationRepository(
            self._db_context, self.diagnostic_pack_registry
        )
        self.diagnostic_profile_repo = SqlDiagnosticProfileRepository(self._db_context)
        self.expectation_state_repo = ExpectationStateRepository(self._db_context)
        self.expectation_failure_repo = ExpectationFailureRepository(
            self._db_context
        )
        self.transition_tracker = TransitionTracker()
        self.health_action_registry = HealthActionRegistry(
            handlers={
                ActionKind.LOG: LogHealthAction(),
                ActionKind.STORE_FAILURE: PersistFailureAction(
                    self.expectation_failure_repo
                ),
            }
        )
        self.action_dispatcher = ActionDispatcher(self.health_action_registry)
        self.health_sink = HealthExpectationService(
            health_expectation_repo=self.health_expectation_repo,
            expectation_state_repo=self.expectation_state_repo,
            expectation_failure_repo=self.expectation_failure_repo,
            transaction_manager=self._db_context,
            transition_tracker=self.transition_tracker,
            action_dispatcher=self.action_dispatcher,
            subscriptions_reader=self.broker_profiles.list_subscriptions,
            broker_metadata_reader=lambda broker_id: (
                self.broker_runtime_state.repositories[broker_id]
            ),
            current_topics_reader=self.topic_messages.get_current_topics,
        )

        self.expectation_management_service = ExpectationManagementService(
            health_expectation_repository=self.health_expectation_repo,
            expectation_state_repository=self.expectation_state_repo,
            expectation_failure_repository=self.expectation_failure_repo,
            transaction_manager=self._db_context,
            control_operation=self.control_operations.operation,
            subscriptions_reader=self.broker_profiles.list_subscriptions,
        )
        self.diagnostic_profiles = DiagnosticProfileService(
            self.diagnostic_profile_repo,
            self.health_expectation_repo,
            self.diagnostic_pack_registry,
            self._db_context,
            expectation_state_repository=self.expectation_state_repo,
            expectation_failure_repository=self.expectation_failure_repo,
        )
        self.diagnostic_profile_editor = DiagnosticProfileEditor(
            self.diagnostic_profiles, self.health_sink
        )
        self.failure_history_service = FailureHistoryService(
            expectation_failure_repository=self.expectation_failure_repo,
            health_expectation_repository=self.health_expectation_repo,
        )
        self.health_report_service = HealthReportService(
            health_expectation_repository=self.health_expectation_repo,
            expectation_state_repository=self.expectation_state_repo,
            expectation_failure_repository=self.expectation_failure_repo,
            subscriptions_reader=self.broker_profiles.list_subscriptions,
        )
        self.health_query_service = HealthQueryService(
            self.health_sink,
            self.expectation_management_service,
            self.failure_history_service,
            self.health_report_service,
        )

        profile = self.broker_profiles.get_profile()

        self.broker_runtime_state.repositories.update(
            {
                item.id: self._create_observer_repository(item)
                for item in self.broker_profiles.get_all_profiles()
            }
        )

        self.runtime = TopicGateRuntime(
            self.broker_profiles,
            self.broker_runtime_state.repositories,
            profile.id,
            mqtt_repository_factory=self._create_observer_repository,
            observation_cache=self.observation_cache,
            observation_query=self.observation_query,
            current_topics=self.topic_messages,
            control_operations=self.control_operations,
            history_recording=self.history_recording,
            history_retention=self.history_retention,
            topic_history=self.topic_history,
        )
        self.broker_resolver = BrokerResolver(self.runtime)
        self.health_wait_service = BrokerHealthWaitService(
            self.runtime, self.expectation_management_service,
            self.health_sink, self.health_query_service,
        )
        self.snapshot_service = BrokerSnapshotService(
            self.runtime,
            current_topics=self.topic_messages,
            resolver=self.broker_resolver,
        )
        self.mcp_setup = McpSetupService(
            self.runtime,
            self.snapshot_service,
            self._db_context,
            self.credential_store,
            database_path.parent,
            database_path,
            broker_reader=self.broker_profiles.list_profile_summaries,
            active_broker_reader=self.broker_profiles.get_profile_summary,
            subscriptions_reader=self.broker_profiles.list_subscriptions,
        )
        self.integration_service = IntegrationService(self.mcp_setup.information)
        self.support_bundle_service = SupportBundleService(
            self.runtime,
            self.snapshot_service,
            health_query_service=self.health_query_service,
            preflight_reader=self.mcp_setup.preflight,
            broker_reader=self.broker_profiles.list_profile_summaries,
            version=self.mcp_setup.information.version,
        )
        self.support_bundle_exporter = SupportBundleExporter(
            self.support_bundle_service
        )
        self.support_bundle_archive_writer = SupportBundleArchiveWriter()
        self.health_monitor = BrokerHealthMonitor(
            self.health_sink,
            broker_ids_reader=lambda: (self.runtime.active_broker.id,),
        )

        self.service_items: tuple[ServiceItem, ...] = (
            self.persistence,
            self.history_retention,
            self.health_monitor,
            self.runtime,
        )

    def _create_observer_repository(
        self,
        profile: BrokerProfile,
    ) -> ObserverRepository:
        return ObserverMqttRepository(
            profile.config,
            list(profile.workspace.subscriptions),
            retention_policy=self.retention_policy.get,
            broker_id=profile.id,
            message_recorder=self.topic_messages,
            current_topics=self.topic_messages,
            health_sink=self.health_sink,
            accepted_recorder=self.observation_recorder,
        )
