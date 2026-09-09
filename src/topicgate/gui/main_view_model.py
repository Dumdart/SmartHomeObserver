import asyncio
import binascii
from base64 import b64decode
from contextlib import asynccontextmanager
from contextlib import suppress
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, Signal

from topicgate.app.models.broker_snapshot import BrokerSnapshot
from topicgate.app.models.topic_history import TopicHistoryResult
from topicgate.core.models.history_retention import HistoryRetentionPolicy, HistoryUsage
from topicgate.core.models.history_recording import HistoryRecordingStatus
from topicgate.app.models.expectation_health_report import (
    ExpectationHealthReport,
    FailureHistoryResult,
    FindingCheckpoint,
)
from topicgate.app.services.broker_snapshot_service import BrokerSnapshotService
from topicgate.app.services.expectation_management_service import (
    ExpectationManagementService,
)
from topicgate.app.services.health_query_service import HealthQueryService
from topicgate.app.services.mcp_setup_service import McpSetupService
from topicgate.app.models.mcp_setup import McpPreflightCheck, McpSetupInformation
from topicgate.app.models.support_bundle_export import SupportBundleExportResult
from topicgate.app.services.support_bundle_export_service import (
    SupportBundleExporter,
)
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.health.condition_kind import ConditionKind
from topicgate.core.models.message_filter import MessageFilter, OrderType
from topicgate.core.models.mqtt_observation import MqttObservation, ObservationSource
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.observation_cache_administration import (
    CacheUsageSummary,
    ObservationDeletionResult,
    PersistedTopicSummary,
    RetentionPolicyApplicationResult,
    RetentionPolicyPreview,
)
from topicgate.core.models.observation_deletion_preview import (
    ObservationDeletionPreview,
)
from topicgate.core.models.observation_retention_policy import (
    ObservationRetentionPolicy,
)
from topicgate.core.models.topic_message import TopicMessage
from topicgate.core.models.support_bundle import SupportBundleOptions
from topicgate.infrastructure.support_bundle_archive import (
    SupportBundleArchiveWriter,
)
from topicgate.core.models.health import (
    ActionKind,
    BrokerTarget,
    EqualCondition,
    HealthExpectation,
    HealthSeverity,
    TopicTarget,
)
from topicgate.core.mqtt_topics import (
    mqtt_filter_has_wildcards,
    mqtt_filter_matches,
)
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.presentation.snapshot_presentation import (
    BrokerSnapshotHealth,
    SnapshotQuery,
    snapshot_health,
    topic_omission_notice,
)
from topicgate.presentation.topic_presentation import (
    TopicDetail,
    TopicTreeNode,
    WildcardFilterSummary,
    build_topic_tree,
    collect_visible_topic_paths,
    matching_subscription,
    topic_detail,
    wildcard_filter_summary,
)
from topicgate.presentation.retention_presentation import (
    RETENTION_PRESETS,
    RetentionPreset,
    validate_retention_policy_values,
)
from topicgate.presentation.snapshot_presentation import size_label
from topicgate.presentation.health_presentation import (
    HealthSummary,
    TopicHealthSummary,
    broker_health_summary,
    topic_health_summary,
)
from topicgate.processors.condition_factory import ConditionFactory


class MainViewModel(QObject):
    """Presentation state for the observer workspace."""

    state_changed = Signal()
    history_settings_changed = Signal()
    event_history_changed = Signal()
    topics_changed = Signal()
    subscriptions_changed = Signal()
    connection_changed = Signal()
    configuration_changed = Signal()
    log_message = Signal(str)
    operation_state_changed = Signal()
    operation_failed = Signal(str, str)
    stored_observations_changed = Signal()
    health_changed = Signal()

    def __init__(
        self,
        runtime: TopicGateRuntime,
        topic: str = "",
        *,
        snapshot_service: BrokerSnapshotService | None = None,
        mcp_setup_service: McpSetupService | None = None,
        health_query_service: HealthQueryService | None = None,
        expectation_management_service: (
            ExpectationManagementService | None
        ) = None,
        support_bundle_exporter: SupportBundleExporter | None = None,
        support_bundle_archive_writer: SupportBundleArchiveWriter | None = None,
    ) -> None:
        super().__init__()
        self._runtime = runtime
        self.history_policy: HistoryRetentionPolicy | None = None
        self.history_recording_status: HistoryRecordingStatus | None = None
        self.history_usage: HistoryUsage | None = None
        self.history_settings_broker: UUID | None = None
        self.history_settings_error: str | None = None
        self._history_settings_generation = 0
        self.event_history_result: TopicHistoryResult | None = None
        self.event_history_error: str | None = None
        self.event_history_busy = False
        self._event_history_generation = 0
        self._snapshot_service = snapshot_service or BrokerSnapshotService(runtime)
        self._mcp_setup_service = mcp_setup_service
        self._health_query_service = health_query_service
        self._expectation_management_service = expectation_management_service
        self._support_bundle_exporter = support_bundle_exporter
        self._support_bundle_archive_writer = support_bundle_archive_writer
        self._health_report_result: ExpectationHealthReport | None = None
        self._health_checkpoints: dict[UUID, FindingCheckpoint] = {}
        self._health_history_result = FailureHistoryResult((), None, 0)
        self._snapshot_query = SnapshotQuery()
        self._topic = topic
        self._snapshot = self._build_current_snapshot(self._snapshot_query)
        self._message_task: asyncio.Task[None] | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._connection_status = self._status_text(
            runtime.connection_status
        )
        self._reported_dropped_messages = 0
        self._busy_operations: set[str] = set()
        self._preserve_snapshot_during_observation = False
        self._retention_policy: ObservationRetentionPolicy | None = None
        self._cache_usage = CacheUsageSummary(())
        self._broker_cache_usage = CacheUsageSummary(())
        self._persisted_topics: tuple[PersistedTopicSummary, ...] = ()
        self._stored_observations_broker_id = self.active_broker_profile.id
        self._stored_observation_filter = MessageFilter(
            self._stored_observations_broker_id
        )
        self._stored_observation_results: tuple[TopicMessage, ...] = ()
        self._selected_stored_observation: TopicMessage | None = None
        self._stored_observation_error: str | None = None

    @property
    def title(self) -> str:
        return "TopicGate Desktop"

    @property
    def health_report(self) -> ExpectationHealthReport | None:
        return self._current_health_report()

    @property
    def health_history(self) -> FailureHistoryResult:
        return self._health_history_result

    @property
    def health_reporting_available(self) -> bool:
        return self._health_query_service is not None

    @property
    def health_summary(self) -> HealthSummary:
        return broker_health_summary(
            self._current_health_report(),
            self._broker_health_expectations(),
            available=self.health_reporting_available,
        )

    @property
    def selected_topic_health(self) -> TopicHealthSummary:
        return topic_health_summary(
            self._topic,
            self._current_health_report(),
            self.topic_expectations,
            available=self.health_reporting_available,
        )

    @property
    def topic_expectations(self) -> tuple[HealthExpectation, ...]:
        if (
            self._expectation_management_service is None
            or not self._topic
            or mqtt_filter_has_wildcards(self._topic)
        ):
            return ()
        return tuple(
            item
            for item in self._expectation_management_service.list_expectations(
                self.active_broker_profile.id
            )
            if isinstance(item.target, TopicTarget)
            and item.target.topic == self._topic
        )

    @property
    def broker_expectations(self) -> tuple[HealthExpectation, ...]:
        if self._expectation_management_service is None:
            return ()
        return tuple(
            item
            for item in self._expectation_management_service.list_expectations(
                self.active_broker_profile.id
            )
            if isinstance(item.target, BrokerTarget)
        )

    def _broker_health_expectations(self) -> tuple[HealthExpectation, ...]:
        if self._expectation_management_service is None:
            return ()
        return tuple(
            self._expectation_management_service.list_expectations(
                self.active_broker_profile.id
            )
        )

    def _current_health_report(self) -> ExpectationHealthReport | None:
        report = self._health_report_result
        if (
            report is not None
            and report.broker_id == self.active_broker_profile.id
        ):
            return report
        return None

    def refresh_health(self) -> ExpectationHealthReport:
        if self._health_query_service is None:
            raise RuntimeError("Health reporting is unavailable.")
        self._health_report_result = self._evaluate_health()
        self.health_changed.emit()
        return self._health_report_result

    def _evaluate_health(self) -> ExpectationHealthReport:
        if self._health_query_service is None:
            raise RuntimeError("Health reporting is unavailable.")
        broker_id = self.active_broker_profile.id
        checkpoint = self._health_checkpoints.get(broker_id)
        report = (
            self._health_query_service.get_health_report(broker_id)
            if checkpoint is None
            else self._health_query_service.get_health_report(
                broker_id,
                checkpoint=checkpoint,
            )
        )
        if report.checkpoint is not None:
            self._health_checkpoints[broker_id] = report.checkpoint
        return report

    def query_health_history(
        self,
        *,
        topic: str | None = None,
        status: str = "all",
        after: datetime | None = None,
        before: datetime | None = None,
        cursor: int | None = None,
    ) -> FailureHistoryResult:
        if self._health_query_service is None:
            raise RuntimeError("Health history is unavailable.")
        page = self._health_query_service.query_failure_history(
            broker_id=self.active_broker_profile.id,
            topic=topic or None,
            status=status,
            after=after,
            before=before,
            cursor=cursor,
        )
        self._health_history_result = (
            FailureHistoryResult(
                self._health_history_result.items + page.items,
                page.next_cursor,
                len(self._health_history_result.items) + page.returned_count,
            )
            if cursor is not None
            else page
        )
        self.health_changed.emit()
        return self._health_history_result

    def delete_health_history(self, failure_id: UUID) -> None:
        if self._health_query_service is None:
            raise RuntimeError("Health history is unavailable.")
        self._health_query_service.delete_failure_history(failure_id)
        remaining = tuple(
            item
            for item in self._health_history_result.items
            if item.failure_id != failure_id
        )
        next_cursor = self._health_history_result.next_cursor
        if next_cursor is not None:
            next_cursor = max(0, next_cursor - 1)
        self._health_history_result = FailureHistoryResult(
            remaining,
            next_cursor,
            len(remaining),
        )
        self.health_changed.emit()

    def save_expectation(
        self,
        *,
        target_kind: str,
        expectation_id: UUID | None,
        name: str,
        description: str,
        expected_values: tuple[str, ...] | str,
        condition_kind: ConditionKind = ConditionKind.EQUAL,
        encoding: str = "utf-8",
        enabled: bool = True,
        log_action: bool = True,
        store_failure: bool = True,
    ) -> HealthExpectation:
        service = self._require_expectation_management()

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Expectation name is required.")
        broker_id = self.active_broker_profile.id
        if isinstance(expected_values, str):
            expected_values = (expected_values,)

        if target_kind == "topic":
            if not self._topic or mqtt_filter_has_wildcards(self._topic):
                raise ValueError("Select an exact MQTT topic first.")
            target = TopicTarget(broker_id, self._topic)
            condition_values = (
                self._decode_expected_values(expected_values, encoding)
                if condition_kind
                in {
                    ConditionKind.EQUAL,
                    ConditionKind.IN_RANGE,
                    ConditionKind.OUTSIDE,
                }
                else expected_values
            )
        elif target_kind == "broker":
            if condition_kind in {
                ConditionKind.NUMERIC_RANGE,
                ConditionKind.TOPIC_EXISTS,
                ConditionKind.TOPIC_ABSENT,
                ConditionKind.FRESH_WITHIN,
            }:
                raise ValueError(
                    "Numeric range, existence, and freshness conditions require "
                    "a topic target."
                )
            target = BrokerTarget(broker_id)
            condition_values = expected_values
        else:
            raise ValueError("target_kind must be 'topic' or 'broker'")

        actions = frozenset(
            action
            for selected, action in (
                (log_action, ActionKind.LOG),
                (store_failure, ActionKind.STORE_FAILURE),
            )
            if selected
        )

        if expectation_id is None:
            result = service.create_expectation(
                HealthExpectation(
                    expectation_id=uuid4(),
                    revision=1,
                    enabled=enabled,
                    severity=HealthSeverity.CRITICAL,
                    target=target,
                    condition=ConditionFactory.build_condition(
                        condition_kind,
                        condition_values,
                    ),
                    actions=actions,
                    name=normalized_name,
                    description=description.strip(),
                ),
                broker_id=broker_id,
            )
        else:
            result = service.edit_expectation(
                expectation_id,
                broker_id=broker_id,
                is_enabled=enabled,
                new_target=target,
                new_condition=ConditionFactory.build_condition(
                    condition_kind,
                    condition_values,
                ),
                new_actions=actions,
                name=normalized_name,
                description=description.strip(),
            )
        self._refresh_after_health_change()
        return result

    def set_expectation_enabled(
        self,
        expectation_id: UUID,
        enabled: bool,
    ) -> HealthExpectation:
        service = self._require_expectation_management()
        result = (
            service.enable_expectation(
                expectation_id,
                broker_id=self.active_broker_profile.id,
            )
            if enabled
            else service.disable_expectation(
                expectation_id,
                broker_id=self.active_broker_profile.id,
            )
        )
        self._refresh_after_health_change()
        return result

    def delete_expectation(self, expectation_id: UUID) -> None:
        self._require_expectation_management().delete_expectation(
            expectation_id,
            broker_id=self.active_broker_profile.id,
        )
        self._refresh_after_health_change()

    def expectation_is_pack_backed(self, expectation_id: UUID) -> bool:
        service = self._require_expectation_management()
        expectation = next(
            (
                item
                for item in service.list_expectations(
                    self.active_broker_profile.id
                )
                if item.expectation_id == expectation_id
            ),
            None,
        )
        return expectation is not None and expectation.source_kind == "pack"

    def remove_expectation(self, expectation_id: UUID) -> None:
        service = self._require_expectation_management()
        if self.expectation_is_pack_backed(expectation_id):
            service.disable_expectation(
                expectation_id,
                broker_id=self.active_broker_profile.id,
            )
        else:
            service.delete_expectation(
                expectation_id,
                broker_id=self.active_broker_profile.id,
            )
        self._refresh_after_health_change()

    def _refresh_after_health_change(self) -> None:
        if self._health_query_service is not None:
            self._health_report_result = self._evaluate_health()
        self.health_changed.emit()

    def _require_expectation_management(self) -> ExpectationManagementService:
        if self._expectation_management_service is None:
            raise RuntimeError("Expectation management is unavailable.")
        return self._expectation_management_service

    @staticmethod
    def _decode_expected_values(
        values: tuple[str, ...],
        encoding: str,
    ) -> tuple[bytes, ...]:
        if encoding == "utf-8":
            return tuple(value.encode("utf-8") for value in values)

        if encoding == "base64":
            try:
                return tuple(
                    b64decode(value, validate=True)
                    for value in values
                )
            except (binascii.Error, ValueError) as error:
                raise ValueError(
                    "One or more expected values are not valid base64."
                ) from error

        raise ValueError("Encoding must be UTF-8 or base64.")

    @property
    def topic(self) -> str:
        return self._topic

    @property
    def decoded_payload(self) -> str:
        return self.topic_detail.decoded_payload

    @property
    def value(self) -> str:
        """Backward-compatible alias for the decoded payload."""
        return self.decoded_payload

    @property
    def raw_payload(self) -> str:
        return self.topic_detail.raw_payload

    @property
    def topic_detail(self) -> TopicDetail:
        state = next(
            (
                item
                for item in self._snapshot.topics
                if item.topic == self._topic
            ),
            None,
        )
        scope_note = ""
        if (
            state is None
            and self._topic
            and not mqtt_filter_has_wildcards(self._topic)
        ):
            exact_snapshot = self._snapshot_service.build_resolved_current(
                self.active_broker_profile,
                topic_filter=self._topic,
                result_limit=1,
                payload_limit_bytes=self._snapshot_query.payload_limit_bytes,
            )
            state = next(iter(exact_snapshot.topics), None)
            if state is not None:
                scope_note = topic_omission_notice(
                    self._snapshot_query,
                    self._snapshot,
                    state,
                )
        detail = topic_detail(
            state,
            self._topic,
            self._snapshot.dropped_message_count,
        )
        if scope_note:
            return replace(detail, snapshot_scope_note=scope_note)
        return detail

    @property
    def received_at(self) -> str:
        return self.topic_detail.received_at

    @property
    def quality_of_service(self) -> str:
        return self.topic_detail.qos_label

    @property
    def retained(self) -> str:
        retained = self.topic_detail.retained
        return "-" if retained is None else str(retained)

    @property
    def message_count(self) -> str:
        return str(self.topic_detail.message_count)

    @property
    def dropped_message_count(self) -> str:
        return str(self._snapshot.dropped_message_count)

    @property
    def snapshot_query(self) -> SnapshotQuery:
        return self._snapshot_query

    @property
    def applied_snapshot_query(self) -> SnapshotQuery:
        return self._snapshot_query

    @property
    def broker_snapshot(self) -> BrokerSnapshot:
        return self._snapshot

    @property
    def cached_broker_snapshot(self) -> BrokerSnapshot:
        return self._snapshot

    @property
    def snapshot_health(self) -> BrokerSnapshotHealth:
        return snapshot_health(self._snapshot)

    @property
    def effective_retention_policy(self) -> ObservationRetentionPolicy | None:
        return self._retention_policy

    @property
    def cache_usage_summary(self) -> CacheUsageSummary:
        return self._cache_usage

    @property
    def broker_cache_usage_summary(self) -> CacheUsageSummary:
        return self._broker_cache_usage

    @property
    def stored_observations_broker_id(self) -> UUID:
        return self._stored_observations_broker_id

    @property
    def stored_observation_filter(self) -> MessageFilter:
        return self._stored_observation_filter

    @property
    def stored_observation_results(self) -> tuple[TopicMessage, ...]:
        return self._stored_observation_results

    @property
    def stored_observation_error(self) -> str | None:
        return self._stored_observation_error

    @property
    def selected_stored_observation_detail(self) -> TopicDetail:
        message = self._selected_stored_observation
        if message is None:
            return topic_detail(None)
        return topic_detail(
            MqttObservation(
                name=message.topic.rsplit("/", maxsplit=1)[-1],
                topic=message.topic,
                payload=message.payload,
                qos=message.qos,
                retain=message.retain,
                recieved_at=message.received_at,
                message_count=message.message_count,
                payload_size=message.payload_size,
                source=ObservationSource.STORED,
                observation_id=message.observation_id,
            )
        )

    @property
    def persisted_topics(self) -> tuple[PersistedTopicSummary, ...]:
        return self._persisted_topics

    @property
    def retention_presets(self) -> tuple[RetentionPreset, ...]:
        return RETENTION_PRESETS

    @property
    def mcp_setup_information(self) -> McpSetupInformation | None:
        if self._mcp_setup_service is None:
            return None
        return self._mcp_setup_service.information

    def mcp_configuration(self, mode: str = "read-only") -> str:
        if self._mcp_setup_service is None:
            import json
            import sys

            rendered = json.dumps(
                {
                    "mcpServers": {
                        "topicgate": {
                            "type": "stdio",
                            "command": sys.executable,
                            "args": ["-m", "topicgate", "--mode", mode],
                        }
                    }
                },
                indent=2,
            )
            return rendered.replace(
                f'        "--mode",\n        "{mode}"',
                f'        "--mode", "{mode}"',
            )
        return self._mcp_setup_service.configuration(mode)

    def run_mcp_preflight(self) -> tuple[McpPreflightCheck, ...]:
        if self._mcp_setup_service is None:
            return (
                McpPreflightCheck(
                    "Desktop integration",
                    "warning",
                    "Full path and database diagnostics are available in the installed desktop application.",
                ),
            )
        return self._mcp_setup_service.preflight()

    def support_bundle_manifest_preview(
        self,
        *,
        include_payloads: bool = False,
    ) -> str:
        if self._support_bundle_exporter is None:
            raise RuntimeError("Support-bundle export is unavailable.")
        return self._support_bundle_exporter.preview_redaction_manifest(
            SupportBundleOptions(include_payloads=include_payloads)
        )

    async def export_support_bundle(
        self,
        destination: Path,
        *,
        include_payloads: bool = False,
    ) -> SupportBundleExportResult:
        if (
            self._support_bundle_exporter is None
            or self._support_bundle_archive_writer is None
        ):
            raise RuntimeError("Support-bundle export is unavailable.")
        async with self._operation("support-bundle"):
            artifacts = await asyncio.to_thread(
                self._support_bundle_exporter.export,
                SupportBundleOptions(include_payloads=include_payloads),
            )
            written = await asyncio.to_thread(
                self._support_bundle_archive_writer.write,
                destination,
                artifacts,
            )
        self.log_message.emit("Exported a redacted support bundle.")
        return SupportBundleExportResult(written, artifacts.warnings)

    def test_broker_snapshot(self) -> BrokerSnapshotHealth:
        self.refresh_snapshot(clear_invalid_selection=False)
        return self.snapshot_health

    @staticmethod
    def validate_retention_policy_draft(
        values: dict[str, object],
    ) -> dict[str, str]:
        return validate_retention_policy_values(values)

    @property
    def connection_status(self) -> str:
        return self._connection_status

    def is_busy(self, operation: str) -> bool:
        return operation in self._busy_operations

    @property
    def any_operation_busy(self) -> bool:
        return bool(self._busy_operations)

    @property
    def mqtt_config(self) -> MqttConfig:
        return self._runtime.mqtt_config

    @property
    def broker_profiles(self) -> tuple[BrokerSummary, ...]:
        return self._runtime.list_brokers()

    @property
    def active_broker_profile(self) -> BrokerSummary:
        return self._runtime.active_broker

    def broker_name(self, broker_id: UUID) -> str:
        return self._runtime.get_broker(broker_id).name

    @property
    def subscriptions(self) -> tuple[Subscription, ...]:
        return self._runtime.list_subscriptions(self.active_broker_profile.id)

    @property
    def topic_paths(self) -> list[str]:
        subscriptions = self.subscriptions
        observed_topics = tuple(item.topic for item in self._snapshot.topics)
        return list(collect_visible_topic_paths(subscriptions, observed_topics))

    @property
    def topic_tree(self) -> tuple[TopicTreeNode, ...]:
        observed_topics = tuple(item.topic for item in self._snapshot.topics)
        return build_topic_tree(
            self.topic_paths,
            self.subscriptions,
            observed_topics,
            self._snapshot.topics,
        )

    @property
    def selected_subscription(self) -> Subscription | None:
        return matching_subscription(self.subscriptions, self._topic)

    @property
    def selected_wildcard_subscription(self) -> Subscription | None:
        return next(
            (
                item
                for item in self.subscriptions
                if item.topic_filter == self._topic
                and mqtt_filter_has_wildcards(item.topic_filter)
            ),
            None,
        )

    @property
    def selected_wildcard_filter_summary(self) -> WildcardFilterSummary | None:
        subscription = self.selected_wildcard_subscription
        if subscription is None:
            return None
        return wildcard_filter_summary(subscription, self._snapshot.topics)

    async def start(self) -> None:
        """Load the current value and listen for messages and connection changes."""
        self.refresh_snapshot(clear_invalid_selection=False)
        self.connection_changed.emit()
        if self._message_task is None:
            self._message_task = asyncio.create_task(self._observe_messages())
        if self._connection_task is None:
            self._connection_task = asyncio.create_task(
                self._observe_connection_statuses()
            )

    async def stop(self) -> None:
        """Stop listening for repository events."""
        await self._cancel_observer_tasks()

    async def _cancel_observer_tasks(self) -> None:
        tasks = [
            task
            for task in (self._message_task, self._connection_task)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._message_task = None
        self._connection_task = None

    async def _restart_observer_tasks(self) -> None:
        observe_messages = self._message_task is not None
        observe_connections = self._connection_task is not None
        await self._cancel_observer_tasks()
        if observe_messages:
            self._message_task = asyncio.create_task(self._observe_messages())
        if observe_connections:
            self._connection_task = asyncio.create_task(
                self._observe_connection_statuses()
            )

    def select_topic(self, topic: str) -> None:
        if topic == self._topic:
            return
        self._topic = topic
        self.state_changed.emit()
        self.subscriptions_changed.emit()

    def refresh(self) -> None:
        """Backward-compatible alias for refreshing the cached snapshot."""
        self.refresh_snapshot()

    def refresh_snapshot(
        self,
        *,
        clear_invalid_selection: bool = True,
        require_snapshot_selection: bool = False,
    ) -> None:
        """Capture current state without reconnecting or mutating observations."""
        self._snapshot = self._build_current_snapshot(self._snapshot_query)
        snapshot_topics = {item.topic for item in self._snapshot.topics}
        if (
            clear_invalid_selection
            and self._topic
            and self._topic not in snapshot_topics
            and (
                require_snapshot_selection
                or self._topic not in self.topic_paths
            )
        ):
            self._topic = ""
        self.state_changed.emit()
        self.topics_changed.emit()

    def apply_snapshot_query(self, query: SnapshotQuery) -> None:
        if not isinstance(query, SnapshotQuery):
            raise TypeError("Snapshot query must be a SnapshotQuery value.")
        snapshot = self._build_current_snapshot(query)
        self._snapshot_query = query
        self._snapshot = snapshot
        if self._topic and self._topic not in self.topic_paths:
            self._topic = ""
        self.state_changed.emit()
        self.topics_changed.emit()
        self.subscriptions_changed.emit()

    def reset_snapshot_query(self) -> None:
        self.apply_snapshot_query(SnapshotQuery())

    def invalidate_event_history(self, *_args) -> None:
        self._event_history_generation += 1
        self.event_history_result = None
        self.event_history_error = None
        self.event_history_busy = False
        self.event_history_changed.emit()

    async def query_event_history(
        self, broker_id: UUID, topic_filter: str, after: datetime | None = None,
        before: datetime | None = None, cursor: str | None = None, limit: int = 100,
    ) -> None:
        self.invalidate_event_history()
        generation = self._event_history_generation
        self.event_history_busy = True
        self.event_history_changed.emit()
        try:
            result = await asyncio.to_thread(
                self._runtime.get_topic_history, broker_id, topic_filter,
                after=after, before=before, cursor=cursor, limit=limit,
            )
        except Exception as error:
            if generation == self._event_history_generation:
                self.event_history_error = (
                    str(error) if isinstance(error, ValueError)
                    else "Event history could not be read. Refresh to retry."
                )
        else:
            if generation == self._event_history_generation:
                self.event_history_result = result
        finally:
            if generation == self._event_history_generation:
                self.event_history_busy = False
                self.event_history_changed.emit()

    async def load_history_settings(self, broker_id: UUID) -> None:
        self._history_settings_generation += 1
        generation = self._history_settings_generation
        try:
            policy, status, usage = await asyncio.gather(
                asyncio.to_thread(self._runtime.get_history_retention_policy),
                asyncio.to_thread(self._runtime.get_history_recording_status, broker_id),
                asyncio.to_thread(self._runtime.get_history_usage, broker_id),
            )
        except Exception:
            if generation == self._history_settings_generation:
                self.history_settings_broker = broker_id
                self.history_settings_error = "History settings could not be loaded. Reload to retry."
                self.history_policy = None
                self.history_settings_changed.emit()
            return
        if generation != self._history_settings_generation:
            return
        self.history_settings_broker = broker_id
        self.history_policy, self.history_recording_status, self.history_usage = policy, status, usage
        self.history_settings_error = None
        self.history_settings_changed.emit()

    async def save_history_settings(
        self, broker_id: UUID, enabled: bool, policy: HistoryRetentionPolicy,
    ) -> None:
        async with self._operation("history-settings"):
            try:
                await asyncio.to_thread(self._runtime.update_history_retention_policy, policy)
                await asyncio.to_thread(self._runtime.set_history_recording, broker_id, enabled)
            except Exception:
                await self.load_history_settings(broker_id)
                self.history_settings_error = (
                    "History settings could not be fully applied. Current saved values were reloaded."
                )
                self.history_settings_changed.emit()
                return
            await self.load_history_settings(broker_id)

    async def load_stored_observations(
        self,
        broker_id: UUID | None = None,
    ) -> None:
        selected = broker_id or self._stored_observations_broker_id
        async with self._operation("stored-observations"):
            query = MessageFilter(
                broker_id=selected,
                topic_filter=self._stored_observation_filter.topic_filter,
                after=self._stored_observation_filter.after,
                before=self._stored_observation_filter.before,
                order=self._stored_observation_filter.order,
                limit=self._stored_observation_filter.limit,
            )
            self._stored_observation_error = None
            try:
                policy, usage, broker_usage, topics, results = await asyncio.gather(
                    asyncio.to_thread(self._runtime.get_retention_policy),
                    asyncio.to_thread(
                        self._runtime.get_observation_storage_summary,
                        None,
                    ),
                    asyncio.to_thread(
                        self._runtime.get_observation_storage_summary,
                        selected,
                    ),
                    asyncio.to_thread(self._runtime.list_persisted_topics, selected),
                    asyncio.to_thread(
                        self._runtime.query_stored_observations,
                        query,
                    ),
                )
            except Exception as error:
                self._set_stored_observation_error(error)
                raise
            self._stored_observations_broker_id = selected
            self._stored_observation_filter = query
            self._retention_policy = policy
            self._cache_usage = usage
            self._broker_cache_usage = broker_usage
            self._persisted_topics = topics
            self._stored_observation_results = results
            self._selected_stored_observation = None
            self.stored_observations_changed.emit()

    async def query_stored_observations(
        self,
        broker_id: UUID,
        topic_filter: str = "#",
        after: datetime | None = None,
        before: datetime | None = None,
        order: OrderType = OrderType.RECEIVED_DESC,
        limit: int = 50,
    ) -> tuple[TopicMessage, ...]:
        if after is not None and before is not None and after > before:
            error = ValueError("The after date-time must not be later than before.")
            self._set_stored_observation_error(error)
            raise error
        if limit < 1:
            error = ValueError("The result limit must be at least 1.")
            self._set_stored_observation_error(error)
            raise error
        message_filter = MessageFilter(
            broker_id=broker_id,
            topic_filter=topic_filter.strip() or "#",
            after=after,
            before=before,
            order=order,
            limit=limit,
        )
        async with self._operation("stored-observations"):
            self._stored_observation_error = None
            self._selected_stored_observation = None
            try:
                results = await asyncio.to_thread(
                    self._runtime.query_stored_observations,
                    message_filter,
                )
            except Exception as error:
                self._stored_observation_results = ()
                self._set_stored_observation_error(error)
                raise
            self._stored_observations_broker_id = broker_id
            self._stored_observation_filter = message_filter
            self._stored_observation_results = results
            self.stored_observations_changed.emit()
            return results

    async def inspect_stored_observation(self, message_id: UUID) -> TopicMessage:
        async with self._operation("stored-observations"):
            self._stored_observation_error = None
            try:
                message = await asyncio.to_thread(
                    self._runtime.get_message,
                    message_id,
                )
            except Exception as error:
                self._selected_stored_observation = None
                self._set_stored_observation_error(error)
                raise
            self._selected_stored_observation = message
            self.stored_observations_changed.emit()
            return message

    async def preview_retention_policy(
        self,
        policy: ObservationRetentionPolicy,
    ) -> RetentionPolicyPreview:
        async with self._operation("stored-observations"):
            return await asyncio.to_thread(
                self._runtime.preview_retention_policy,
                policy,
            )

    async def confirm_retention_policy(
        self,
        preview: RetentionPolicyPreview,
    ) -> RetentionPolicyApplicationResult:
        async with self._operation("stored-observations"):
            result = await asyncio.to_thread(
                self._runtime.confirm_retention_policy,
                preview,
            )
            self._retention_policy = result.policy
            await self._reload_stored_observation_data()
            self.refresh_snapshot(require_snapshot_selection=True)
            removed = result.enforcement.deleted_count
            brokers = len(
                {item.broker_id for item in result.enforcement.deleted_entries}
            )
            self.log_message.emit(
                "Retention policy updated; removed "
                f"{removed} observations ({size_label(result.enforcement.deleted_bytes)}) "
                f"across {brokers} brokers."
            )
            self.stored_observations_changed.emit()
            return result

    async def preview_cache_deletion(
        self,
        scope: str,
        *,
        broker_id: UUID | None = None,
        topics: tuple[str, ...] = (),
    ) -> ObservationDeletionPreview:
        selected = broker_id or self._stored_observations_broker_id
        async with self._operation("stored-observations"):
            if scope == "all_brokers":
                return await asyncio.to_thread(self._runtime.preview_all_cache)
            if scope == "unsubscribed":
                return await asyncio.to_thread(
                    self._runtime.preview_unsubscribed_cache,
                    selected,
                )
            if scope == "selected_topics":
                if not topics:
                    return ObservationDeletionPreview(
                        selected,
                        (),
                        "selected_topics",
                    )
                return await asyncio.to_thread(
                    self._runtime.preview_clear_cache,
                    selected,
                    topics,
                )
            if scope == "broker":
                return await asyncio.to_thread(
                    self._runtime.preview_clear_cache,
                    selected,
                )
            raise ValueError(f"Unknown cache deletion scope: {scope}")

    async def confirm_cache_deletion(
        self,
        preview: ObservationDeletionPreview,
    ) -> ObservationDeletionResult:
        async with self._operation("stored-observations"):
            result = await asyncio.to_thread(
                self._runtime.confirm_cache_deletion_detailed,
                preview,
            )
            await self._reload_stored_observation_data()
            self.refresh_snapshot(require_snapshot_selection=True)
            broker_label = (
                "all brokers"
                if len(preview.broker_ids) != 1
                else self._runtime.get_broker(preview.broker_ids[0]).name
            )
            self.log_message.emit(
                f"Deleted {result.deleted_count} of {result.previewed_count} "
                f"previewed observations from {broker_label}; "
                f"{result.skipped_count} changed after preview."
            )
            if result.is_partial:
                self.log_message.emit("Partial deletion detected.")
            self.stored_observations_changed.emit()
            return result

    async def _reload_stored_observation_data(self) -> None:
        query = MessageFilter(
            broker_id=self._stored_observations_broker_id,
            topic_filter=self._stored_observation_filter.topic_filter,
            after=self._stored_observation_filter.after,
            before=self._stored_observation_filter.before,
            order=self._stored_observation_filter.order,
            limit=self._stored_observation_filter.limit,
        )
        (
            self._cache_usage,
            self._broker_cache_usage,
            self._persisted_topics,
            self._stored_observation_results,
        ) = await asyncio.gather(
            asyncio.to_thread(
                self._runtime.get_observation_storage_summary,
                None,
            ),
            asyncio.to_thread(
                self._runtime.get_observation_storage_summary,
                self._stored_observations_broker_id,
            ),
            asyncio.to_thread(
                self._runtime.list_persisted_topics,
                self._stored_observations_broker_id,
            ),
            asyncio.to_thread(
                self._runtime.query_stored_observations,
                query,
            ),
        )
        self._stored_observation_filter = query
        self._stored_observation_error = None
        selected_id = (
            self._selected_stored_observation.observation_id
            if self._selected_stored_observation is not None
            else None
        )
        self._selected_stored_observation = next(
            (
                item
                for item in self._stored_observation_results
                if item.observation_id == selected_id
            ),
            None,
        )

    def _set_stored_observation_error(self, error: BaseException) -> None:
        self._stored_observation_error = str(error)
        self.stored_observations_changed.emit()

    async def add_subscription(self, subscription: Subscription) -> None:
        async with self._operation("subscription"):
            await self._runtime.add_subscription(
                self.active_broker_profile.id,
                subscription,
            )
            self.log_message.emit(f"Added subscription: {subscription.topic_filter}")
            await self._refresh_stored_observations_after_subscription_change()
            self.refresh_snapshot()
            self.subscriptions_changed.emit()

    async def remove_subscription(self, subscription: Subscription) -> None:
        async with self._operation("subscription"):
            cleanup = await self._runtime.remove_subscription(
                self.active_broker_profile.id,
                subscription,
            )
            self.log_message.emit(f"Removed subscription: {subscription.topic_filter}")
            if cleanup is not None:
                self.log_message.emit(
                    "Automatic unsubscribed cleanup completed; removed "
                    f"{cleanup.deleted_count} observations "
                    f"({size_label(cleanup.deleted_bytes)})."
                )
            await self._refresh_stored_observations_after_subscription_change()
            if self._topic and not any(
                mqtt_filter_matches(item.topic_filter, self._topic)
                for item in self.subscriptions
            ):
                self._topic = ""
            self.refresh_snapshot()
            self.subscriptions_changed.emit()

    async def update_subscription(
        self, original_filter: str, subscription: Subscription
    ) -> None:
        async with self._operation("subscription"):
            cleanup = await self._runtime.update_subscription(
                self.active_broker_profile.id,
                original_filter,
                subscription,
            )
            self.log_message.emit(
                f"Updated subscription: {original_filter} -> {subscription.topic_filter}"
            )
            if cleanup is not None:
                self.log_message.emit(
                    "Automatic unsubscribed cleanup completed; removed "
                    f"{cleanup.deleted_count} observations "
                    f"({size_label(cleanup.deleted_bytes)})."
                )
            await self._refresh_stored_observations_after_subscription_change()
            if self._topic == original_filter:
                self._topic = subscription.topic_filter
            self.refresh_snapshot()
            self.subscriptions_changed.emit()

    async def _refresh_stored_observations_after_subscription_change(
        self,
    ) -> None:
        if (
            self._retention_policy is None
            or not self._retention_policy.auto_remove_unsubscribed
        ):
            return
        await self._reload_stored_observation_data()
        self.stored_observations_changed.emit()

    async def reconnect_to_broker(self) -> None:
        async with self._operation("connection"):
            self.log_message.emit("Reconnect requested")
            await self._runtime.reconnect()
            self.refresh_snapshot()

    async def reconnect_and_observe(
        self,
        query: SnapshotQuery | None = None,
    ) -> None:
        selected_query = query or self._snapshot_query
        async with self._operation("connection"):
            self.log_message.emit("Reconnect and observe requested")
            self._preserve_snapshot_during_observation = True
            try:
                snapshot = await self._snapshot_service.observe(
                    self.active_broker_profile.id,
                    topic_filter=selected_query.topic_filter,
                    max_age_seconds=selected_query.max_age_seconds,
                    result_limit=selected_query.result_limit,
                    payload_limit_bytes=selected_query.payload_limit_bytes,
                )
            finally:
                self._preserve_snapshot_during_observation = False
            self._snapshot_query = selected_query
            self._snapshot = snapshot
            if self._topic and self._topic not in self.topic_paths:
                self._topic = ""
            self._connection_status = snapshot.connection_status
            self.state_changed.emit()
            self.topics_changed.emit()
            self.subscriptions_changed.emit()
            self.connection_changed.emit()

    async def connect_to_broker(self) -> None:
        async with self._operation("connection"):
            self.log_message.emit("Connect requested")
            await self._runtime.connect()


    async def disconnect_from_broker(self) -> None:
        async with self._operation("connection"):
            self.log_message.emit("Disconnect requested")
            await self._runtime.disconnect()

    async def update_mqtt_config(self, mqtt_config: MqttConfig) -> None:
        """Apply broker settings before retaining them in application settings."""
        await self.update_broker_profile(
            self.active_broker_profile.id,
            mqtt_config,
        )

    async def update_broker_profile(
        self,
        profile_id: UUID,
        mqtt_config: MqttConfig,
        profile_name: str | None = None,
    ) -> None:
        """Backward-compatible alias for activating a broker profile."""
        await self.activate_broker_profile(
            profile_id,
            mqtt_config,
            profile_name,
        )

    async def activate_broker_profile(
        self,
        profile_id: UUID,
        mqtt_config: MqttConfig,
        profile_name: str | None = None,
    ) -> None:
        """Select and save a profile, then attempt to connect it."""
        async with self._operation("broker"):
            profile = self._runtime.get_broker(profile_id)
            profile_changed = profile.id != self.active_broker_profile.id
            self.log_message.emit(
                f"Connecting to MQTT broker: {mqtt_config.host}:{mqtt_config.port}"
            )
            try:
                await self._runtime.activate_broker(
                    profile_id,
                    mqtt_config,
                    profile_name,
                )
            except Exception as error:
                if self.active_broker_profile.id == profile_id:
                    await self._refresh_after_broker_selection(profile_changed)
                    self.log_message.emit(
                        f"Broker selected but connection failed: {error}"
                    )
                else:
                    self.log_message.emit(f"Broker update failed: {error}")
                raise
            await self._refresh_after_broker_selection(profile_changed)
            self.log_message.emit(
                f"Updated MQTT broker: {mqtt_config.host}:{mqtt_config.port}"
            )

    async def _refresh_after_broker_selection(
        self,
        profile_changed: bool,
    ) -> None:
        if profile_changed:
            await self._restart_observer_tasks()
            self._topic = ""
            self._health_history_result = FailureHistoryResult((), None, 0)
        self._health_report_result = None
        self.health_changed.emit()
        self.refresh_snapshot()
        self.subscriptions_changed.emit()
        self.configuration_changed.emit()

    def save_broker_profile(
        self,
        profile_id: UUID,
        mqtt_config: MqttConfig,
        profile_name: str | None = None,
    ) -> BrokerSummary:
        """Persist broker settings without changing the MQTT connection."""
        profile = self._runtime.update_broker(
            profile_id,
            mqtt_config,
            profile_name,
        )
        if profile.id == self.active_broker_profile.id:
            self.refresh_snapshot()
        self.configuration_changed.emit()
        self.log_message.emit(f"Saved broker profile: {profile.name}")
        return profile

    def create_broker_profile(
        self,
        name: str,
        mqtt_config: MqttConfig,
    ) -> BrokerSummary:
        """Create a selectable broker profile without changing connections."""
        profile = self._runtime.create_broker(name, mqtt_config)
        self.configuration_changed.emit()
        self.log_message.emit(f"Created broker profile: {profile.name}")
        return profile

    async def delete_broker_profile(self, profile_id: UUID) -> None:
        """Delete a profile, switching away first when it is active."""
        profile = self._runtime.get_broker(profile_id)
        active_profile_id = self.active_broker_profile.id
        replacement = next(
            (
                item
                for item in self.broker_profiles
                if item.id != profile_id
            ),
            None,
        )
        if active_profile_id == profile_id and replacement is not None:
            self.log_message.emit(
                "Connecting to MQTT broker: "
                f"{replacement.config.host}:{replacement.config.port}"
            )
        async with self._operation("broker"):
            await self._runtime.delete_broker(profile_id)
            self._health_checkpoints.pop(profile_id, None)
            if active_profile_id == profile_id:
                self._topic = ""
                self.refresh_snapshot()
                self.subscriptions_changed.emit()
                self.configuration_changed.emit()
                active = self.active_broker_profile
                self.log_message.emit(
                    f"Updated MQTT broker: {active.config.host}:{active.config.port}"
                )
            self.configuration_changed.emit()
            self.log_message.emit(f"Deleted broker profile: {profile.name}")

    async def publish_message(
        self,
        topic: str,
        payload: str,
        encoding: str = "utf-8",
    ) -> None:
        topic = topic.strip()
        if not topic:
            raise ValueError("A publish topic is required.")
        if encoding == "utf-8":
            payload_bytes = payload.encode("utf-8")
        elif encoding == "base64":
            try:
                payload_bytes = b64decode(payload, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError("Payload is not valid base64.") from error
        else:
            raise ValueError("Encoding must be UTF-8 or base64.")
        async with self._operation("publish"):
            await self._runtime.publish(
                self.active_broker_profile.id,
                topic,
                payload_bytes,
            )
            self.log_message.emit(f"Published message: {topic}")

    def report_operation_error(self, title: str, error: BaseException) -> None:
        message = str(error)
        self.log_message.emit(f"{title}: {message}")
        self.operation_failed.emit(title, message)

    @asynccontextmanager
    async def _operation(self, name: str) -> AsyncIterator[None]:
        exclusive_operations = {
            "connection",
            "broker",
            "stored-observations",
            "support-bundle",
        }
        if name in self._busy_operations:
            raise RuntimeError(f"The {name} operation is already in progress.")
        if (
            name in exclusive_operations
            and self._busy_operations.intersection(exclusive_operations)
        ):
            raise RuntimeError(
                "A reconnect, broker change, stored-observation, or support-bundle "
                "operation is "
                "already in progress. Wait for it to finish before starting "
                "another exclusive action."
            )
        self._busy_operations.add(name)
        self.operation_state_changed.emit()
        try:
            yield
        finally:
            self._busy_operations.discard(name)
            self.operation_state_changed.emit()

    async def _observe_messages(self) -> None:
        async for message in self._runtime.messages():
            update_interval = float(self._runtime.topic_update_interval)
            if update_interval > 0:
                await asyncio.sleep(update_interval)
            messages = (message,)
            messages += self._runtime.drain_pending_messages()

            latest = messages[-1]
            if len(messages) == 1:
                self.log_message.emit(
                    f"Received {latest.topic} (QoS {latest.qos}, "
                    f"retained {'yes' if latest.retain else 'no'})"
                )
            else:
                self.log_message.emit(
                    f"Received {len(messages)} MQTT messages "
                    f"(latest: {latest.topic})"
                )

            dropped = int(self.dropped_message_count)
            if dropped > self._reported_dropped_messages:
                newly_dropped = dropped - self._reported_dropped_messages
                self.log_message.emit(
                    f"Dropped {newly_dropped} MQTT messages during admission "
                    f"({dropped} total)"
                )
                self._reported_dropped_messages = dropped

            if not self._preserve_snapshot_during_observation:
                self.refresh_snapshot()

    async def _observe_connection_statuses(self) -> None:
        async for status in self._runtime.connection_statuses():
            self._connection_status = self._status_text(status)
            if not self._preserve_snapshot_during_observation:
                self.refresh_snapshot()
            self.connection_changed.emit()
            self.log_message.emit(f"Connection {self._connection_status}")

    @staticmethod
    def _status_text(status: object) -> str:
        value = getattr(status, "value", status)
        return str(value).lower()

    def _build_current_snapshot(self, query: SnapshotQuery) -> BrokerSnapshot:
        return self._snapshot_service.build_current(
            self.active_broker_profile.id,
            topic_filter=query.topic_filter,
            max_age_seconds=query.max_age_seconds,
            result_limit=query.result_limit,
            payload_limit_bytes=query.payload_limit_bytes,
        )
