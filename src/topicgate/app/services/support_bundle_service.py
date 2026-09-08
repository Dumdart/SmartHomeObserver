import platform
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from topicgate.app.models.broker_snapshot import BrokerSnapshot, SnapshotTopicState
from topicgate.app.models.expectation_health_report import ExpectationHealthReport
from topicgate.app.services.broker_snapshot_service import BrokerSnapshotService
from topicgate.app.services.health_query_service import HealthQueryService
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.broker_profile_summary import BrokerProfileSummary
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.support_bundle import (
    SUPPORT_BUNDLE_SCHEMA_VERSION,
    RedactionManifest,
    RedactionPolicy,
    SupportApplication,
    SupportBroker,
    SupportBundle,
    SupportBundleOptions,
    SupportDiagnosticCheck,
    SupportFinding,
    SupportHealth,
    SupportPayload,
    SupportResultLimit,
    SupportSubscription,
    SupportTopicState,
    SupportTopicStateWithPayload,
)


Clock = Callable[[], datetime]
IdentifierFactory = Callable[[], UUID]
PreflightReader = Callable[[], tuple[object, ...]]
BrokerReader = Callable[[], tuple[BrokerProfileSummary, ...]]


@dataclass(frozen=True)
class _BrokerSource:
    id: UUID
    name: str
    port: int
    use_tls: bool


@dataclass(frozen=True)
class _BrokerCapture:
    broker: _BrokerSource
    subscriptions: tuple[Subscription, ...]
    subscription_total: int
    snapshot: BrokerSnapshot | None
    health: ExpectationHealthReport | None
    limitations: tuple[str, ...]


class _AliasRegistry:
    def __init__(self, prefix: str, values: set[str]) -> None:
        self._aliases = {
            value: f"topic-{prefix}-{index:03d}"
            for index, value in enumerate(sorted(values), start=1)
        }

    def get(self, value: str) -> str:
        return self._aliases[value]

    def __len__(self) -> int:
        return len(self._aliases)


class SupportBundleService:
    """Build bounded support data without exposing credential-bearing models."""

    def __init__(
        self,
        runtime: TopicGateRuntime,
        snapshot_service: BrokerSnapshotService,
        *,
        health_query_service: HealthQueryService | None = None,
        preflight_reader: PreflightReader | None = None,
        broker_reader: BrokerReader | None = None,
        version: str = "development",
        clock: Clock | None = None,
        identifier_factory: IdentifierFactory = uuid4,
    ) -> None:
        self._runtime = runtime
        self._snapshots = snapshot_service
        self._health = health_query_service
        self._preflight_reader = preflight_reader
        self._broker_reader = broker_reader
        self._version = version
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._identifier_factory = identifier_factory

    def generate_support_bundle(
        self,
        options: SupportBundleOptions | None = None,
    ) -> SupportBundle:
        selected = options or SupportBundleOptions()
        bundle_uuid = self._identifier_factory()
        bundle_id = f"support-{bundle_uuid.hex}"
        alias_prefix = bundle_uuid.hex[:8]
        all_brokers = tuple(
            sorted(
                self._safe_brokers(),
                key=lambda item: (item.name.casefold(), item.id.hex),
            )
        )
        selected_brokers = all_brokers[: selected.broker_limit]
        captures = tuple(
            self._capture_broker(broker, selected)
            for broker in selected_brokers
        )
        topic_aliases = _AliasRegistry(
            alias_prefix,
            self._topic_values(captures),
        )
        diagnostics, diagnostic_results, diagnostic_limitations = self._diagnostics()
        brokers = tuple(
            self._broker(
                capture,
                broker_alias=f"broker-{alias_prefix}-{index:03d}",
                topic_aliases=topic_aliases,
                options=selected,
            )
            for index, capture in enumerate(captures, start=1)
        )
        return SupportBundle(
            schema_version=SUPPORT_BUNDLE_SCHEMA_VERSION,
            bundle_id=bundle_id,
            generated_at=_as_utc(self._clock()),
            application=SupportApplication(
                version=self._version,
                python_version=platform.python_version(),
                operating_system=f"{platform.system()} {platform.release()}".strip(),
            ),
            payloads_included=selected.include_payloads,
            diagnostics=diagnostics,
            diagnostic_results=diagnostic_results,
            brokers=brokers,
            broker_results=SupportResultLimit(
                limit=selected.broker_limit,
                total=len(all_brokers),
                returned=len(captures),
                omitted=len(all_brokers) - len(captures),
            ),
            limitations=tuple(
                (
                    *diagnostic_limitations,
                    *(
                        ("broker_results_omitted",)
                        if len(all_brokers) > len(captures)
                        else ()
                    ),
                )
            ),
        )

    def redaction_manifest(self, bundle: SupportBundle) -> RedactionManifest:
        topic_aliases = {
            subscription.topic_alias
            for broker in bundle.brokers
            for subscription in broker.subscriptions
        }
        topic_aliases.update(
            topic.topic_alias
            for broker in bundle.brokers
            for topic in broker.topics
        )
        topic_aliases.update(
            finding.target
            for broker in bundle.brokers
            if broker.health is not None
            for finding in broker.health.findings
            if finding.target_kind == "topic"
        )
        finding_count = sum(
            len(broker.health.findings)
            for broker in bundle.brokers
            if broker.health is not None
        )
        topic_count = sum(len(broker.topics) for broker in bundle.brokers)
        return RedactionManifest(
            schema_version=bundle.schema_version,
            bundle_id=bundle.bundle_id,
            policies=(
                RedactionPolicy(
                    "credentials",
                    "structurally_excluded",
                    len(bundle.brokers),
                ),
                RedactionPolicy(
                    "credential_store_identifiers",
                    "structurally_excluded",
                    len(bundle.brokers),
                ),
                RedactionPolicy(
                    "broker_identifiers_and_names",
                    "per_bundle_alias",
                    len(bundle.brokers),
                ),
                RedactionPolicy(
                    "connection_hosts_and_usernames",
                    "structurally_excluded",
                    len(bundle.brokers),
                ),
                RedactionPolicy(
                    "topics",
                    "per_bundle_alias",
                    len(topic_aliases),
                ),
                RedactionPolicy(
                    "payloads_and_evidence",
                    "bounded_included"
                    if bundle.payloads_included
                    else "structurally_excluded",
                    topic_count + finding_count,
                ),
                RedactionPolicy(
                    "filesystem_paths",
                    "structurally_excluded",
                    len(bundle.diagnostics),
                ),
            ),
        )

    def _capture_broker(
        self,
        broker: _BrokerSource,
        options: SupportBundleOptions,
    ) -> _BrokerCapture:
        limitations: list[str] = []
        all_subscriptions = tuple(self._runtime.list_subscriptions(broker.id))
        subscriptions = all_subscriptions[: options.subscription_limit]
        if len(subscriptions) < len(all_subscriptions):
            limitations.append("subscription_results_omitted")
        try:
            snapshot = self._snapshots.build_resolved_current(
                BrokerSummary(
                    broker.id,
                    broker.name,
                    MqttConfig(
                        "",
                        broker.port,
                        "",
                        "",
                        broker.use_tls,
                    ),
                    False,
                ),
                result_limit=options.topic_limit,
                payload_limit_bytes=(
                    options.payload_limit_bytes if options.include_payloads else 0
                ),
            )
        except Exception:
            snapshot = None
            limitations.append("snapshot_unavailable")

        health = None
        if self._health is not None:
            try:
                health = self._health.get_health_report(
                    broker.id,
                    limit=options.health_limit,
                )
            except Exception:
                limitations.append("health_report_unavailable")
        return _BrokerCapture(
            broker,
            subscriptions,
            len(all_subscriptions),
            snapshot,
            health,
            tuple(limitations),
        )

    def _topic_values(self, captures: tuple[_BrokerCapture, ...]) -> set[str]:
        values = {
            subscription.topic_filter
            for capture in captures
            for subscription in capture.subscriptions
        }
        for capture in captures:
            if capture.snapshot is not None:
                values.update(item.topic for item in capture.snapshot.topics)
            if capture.health is not None:
                values.update(
                    item.target
                    for item in capture.health.expectation_findings
                    if item.target_kind == "topic"
                )
        return values

    def _broker(
        self,
        capture: _BrokerCapture,
        *,
        broker_alias: str,
        topic_aliases: _AliasRegistry,
        options: SupportBundleOptions,
    ) -> SupportBroker:
        broker = capture.broker
        subscriptions = tuple(
            SupportSubscription(
                topic_alias=topic_aliases.get(item.topic_filter),
                qos=item.qos,
                retain_as_published=item.retain_as_published,
                retain_handling=item.retain_handling,
            )
            for item in capture.subscriptions
        )
        snapshot = capture.snapshot
        if snapshot is None:
            return SupportBroker(
                broker_alias=broker_alias,
                port=broker.port,
                use_tls=broker.use_tls,
                connection_status="unknown",
                captured_at=_as_utc(self._clock()),
                subscriptions=subscriptions,
                subscription_results=SupportResultLimit(
                    options.subscription_limit,
                    capture.subscription_total,
                    len(subscriptions),
                    capture.subscription_total - len(subscriptions),
                ),
                topics=(),
                topic_results=SupportResultLimit(options.topic_limit, 0, 0, 0),
                dropped_message_count=0,
                health=self._health_projection(
                    capture.health,
                    broker_alias,
                    topic_aliases,
                    options,
                ),
                limitations=capture.limitations,
            )
        return SupportBroker(
            broker_alias=broker_alias,
            port=broker.port,
            use_tls=broker.use_tls,
            connection_status=snapshot.connection_status,
            captured_at=snapshot.captured_at,
            subscriptions=subscriptions,
            subscription_results=SupportResultLimit(
                options.subscription_limit,
                capture.subscription_total,
                len(subscriptions),
                capture.subscription_total - len(subscriptions),
            ),
            topics=tuple(
                self._topic(item, topic_aliases, options)
                for item in snapshot.topics
            ),
            topic_results=SupportResultLimit(
                limit=snapshot.results.limit,
                total=snapshot.results.total,
                returned=snapshot.results.returned,
                omitted=snapshot.results.omitted,
            ),
            dropped_message_count=snapshot.dropped_message_count,
            health=self._health_projection(
                capture.health,
                broker_alias,
                topic_aliases,
                options,
            ),
            limitations=tuple(
                (
                    *capture.limitations,
                    *(item.value for item in snapshot.completeness.limitations),
                )
            ),
        )

    @staticmethod
    def _topic(
        topic: SnapshotTopicState,
        aliases: _AliasRegistry,
        options: SupportBundleOptions,
    ) -> SupportTopicState | SupportTopicStateWithPayload:
        values = dict(
            topic_alias=aliases.get(topic.topic),
            qos=topic.qos,
            retain=topic.retain,
            received_at=topic.received_at,
            age_seconds=topic.age_seconds,
            message_count=topic.message_count,
            source=topic.source.value,
            status=topic.status.value,
        )
        if not options.include_payloads:
            return SupportTopicState(**values)
        payload = topic.payload
        return SupportTopicStateWithPayload(
            **values,
            payload=SupportPayload(
                encoding=payload.encoding.value,
                value=payload.value,
                original_size=payload.original_size,
                available_size=payload.available_size,
                rendered_size=payload.rendered_size,
                ingestion_truncated=payload.ingestion_truncated,
                rendering_truncated=payload.rendering_truncated,
            ),
        )

    @staticmethod
    def _health_projection(
        report: ExpectationHealthReport | None,
        broker_alias: str,
        topic_aliases: _AliasRegistry,
        options: SupportBundleOptions,
    ) -> SupportHealth | None:
        if report is None:
            return None
        raw_findings = (
            *(item for item in report.observation_findings),
            *(item for item in report.expectation_findings),
        )
        findings = []
        for index, finding in enumerate(raw_findings, start=1):
            target_kind = getattr(finding, "target_kind", "broker")
            raw_target = getattr(finding, "target", "broker")
            evidence = getattr(finding, "evidence_summary", None)
            failure_code = getattr(finding, "failure_code", None)
            if failure_code is None:
                code = getattr(finding, "code", None)
                failure_code = getattr(code, "value", code)
            bounded_evidence = None
            evidence_truncated = False
            if options.include_payloads and evidence is not None:
                bounded_evidence = evidence[: options.evidence_limit]
                evidence_truncated = (
                    len(evidence) > options.evidence_limit
                    or bool(getattr(finding, "evidence_truncated", False))
                )
            findings.append(
                SupportFinding(
                    finding_alias=f"finding-{index:03d}",
                    target=(
                        topic_aliases.get(raw_target)
                        if target_kind == "topic"
                        else broker_alias
                    ),
                    target_kind=target_kind,
                    status=finding.status.value,
                    failure_code=failure_code,
                    evidence_complete=bool(
                        getattr(finding, "evidence_complete", report.evidence_complete)
                    ),
                    evidence_summary=bounded_evidence,
                    evidence_truncated=evidence_truncated,
                )
            )
        return SupportHealth(
            evaluated_at=report.evaluated_at,
            aggregate_status=report.aggregate_status.value,
            observation_status=report.observation_status.value,
            evidence_complete=report.evidence_complete,
            active_failure_count=report.active_failure_count,
            findings=tuple(findings),
            returned_count=len(findings),
            omitted_count=report.omitted_count,
        )

    def _diagnostics(
        self,
    ) -> tuple[
        tuple[SupportDiagnosticCheck, ...],
        SupportResultLimit,
        tuple[str, ...],
    ]:
        if self._preflight_reader is None:
            return (), SupportResultLimit(50, 0, 0, 0), ()
        try:
            checks = tuple(self._preflight_reader())
        except Exception:
            return (
                (),
                SupportResultLimit(50, 0, 0, 0),
                ("preflight_unavailable",),
            )
        selected = checks[:50]
        return (
            tuple(
                SupportDiagnosticCheck(
                    name=str(getattr(check, "name", "Unknown check"))[:200],
                    status=str(getattr(check, "status", "unknown"))[:40],
                )
                for check in selected
            ),
            SupportResultLimit(
                50,
                len(checks),
                len(selected),
                len(checks) - len(selected),
            ),
            ("preflight_results_omitted",) if len(checks) > 50 else (),
        )

    def _safe_brokers(self) -> tuple[_BrokerSource, ...]:
        if self._broker_reader is not None:
            return tuple(
                _BrokerSource(item.id, item.name, item.port, item.use_tls)
                for item in self._broker_reader()
            )
        return tuple(
            self._source_from_runtime(item)
            for item in self._runtime.list_brokers()
        )

    @staticmethod
    def _source_from_runtime(broker: BrokerSummary) -> _BrokerSource:
        return _BrokerSource(
            broker.id,
            broker.name,
            broker.config.port,
            broker.config.use_tls,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
