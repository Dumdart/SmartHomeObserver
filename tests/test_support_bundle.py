import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import pytest

from topicgate.app.app_dependencies import AppDependencies
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
from topicgate.app.models.expectation_health_report import (
    ExpectationHealthFinding,
    ExpectationHealthReport,
)
from topicgate.app.services.support_bundle_export_service import (
    SupportBundleExporter,
)
from topicgate.app.services.support_bundle_service import SupportBundleService
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.broker_profile_summary import BrokerProfileSummary
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.health import HealthStatus
from topicgate.core.models.mqtt_observation import ObservationSource
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.support_bundle import (
    SUPPORT_BUNDLE_SCHEMA_VERSION,
    SupportBundleOptions,
    SupportTopicState,
    SupportTopicStateWithPayload,
)
from topicgate.infrastructure.support_bundle_archive import (
    SupportBundleArchiveWriter,
)
from topicgate.mcp.api.support_bundle_api import SupportBundleAPI
from zipfile import ZipFile


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
BROKER_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
BUNDLE_IDS = (
    UUID("11111111-1111-1111-1111-111111111111"),
    UUID("22222222-2222-2222-2222-222222222222"),
)
REAL_TOPIC = "private/home/bedroom/temperature"
SECRET_PAYLOAD = "secret-payload-value"
SECRET_USERNAME = "private-user"
SECRET_PASSWORD = "correct-horse-battery-staple"
SECRET_HOST = "private-broker.internal"
SECRET_PATH = "C:/Users/Private/topicgate.db"
KEYRING_IDENTIFIER = f"TopicGate MQTT/profile_{BROKER_ID}"


def test_default_bundle_structurally_excludes_sensitive_values_and_payloads() -> None:
    exporter, snapshots = _exporter()

    artifacts = exporter.export()

    combined = artifacts.json + artifacts.markdown + artifacts.manifest
    for secret in (
        str(BROKER_ID),
        REAL_TOPIC,
        SECRET_PAYLOAD,
        SECRET_USERNAME,
        SECRET_PASSWORD,
        SECRET_HOST,
        SECRET_PATH,
        KEYRING_IDENTIFIER,
    ):
        assert secret not in combined

    document = json.loads(artifacts.json)
    broker = document["brokers"][0]
    topic = broker["topics"][0]
    finding = broker["health"]["findings"][0]
    assert document["schema_version"] == SUPPORT_BUNDLE_SCHEMA_VERSION
    assert document["payloads_included"] is False
    assert "payload" not in topic
    assert "evidence_summary" not in finding
    assert topic["topic_alias"] == broker["subscriptions"][0]["topic_alias"]
    assert finding["target"] == topic["topic_alias"]
    assert snapshots.build_resolved_current.call_args.kwargs[
        "payload_limit_bytes"
    ] == 0

    manifest = json.loads(artifacts.manifest)
    policies = {item["category"]: item for item in manifest["policies"]}
    assert policies["credentials"]["strategy"] == "structurally_excluded"
    assert policies["credential_store_identifiers"]["strategy"] == (
        "structurally_excluded"
    )
    assert policies["topics"]["strategy"] == "per_bundle_alias"
    assert policies["payloads_and_evidence"]["strategy"] == (
        "structurally_excluded"
    )


def test_payload_opt_in_uses_bounded_payload_and_evidence_models() -> None:
    exporter, snapshots = _exporter()

    artifacts = exporter.export(
        SupportBundleOptions(
            include_payloads=True,
            payload_limit_bytes=8,
            evidence_limit=6,
        )
    )

    document = json.loads(artifacts.json)
    topic = document["brokers"][0]["topics"][0]
    finding = document["brokers"][0]["health"]["findings"][0]
    assert topic["payload"]["value"] == SECRET_PAYLOAD
    assert finding["evidence_summary"] == "actual"
    assert finding["evidence_truncated"] is True
    assert snapshots.build_resolved_current.call_args.kwargs[
        "payload_limit_bytes"
    ] == 8
    assert "secret-payload-value" in artifacts.markdown
    assert json.loads(artifacts.manifest)["policies"][5]["strategy"] == (
        "bounded_included"
    )


def test_topic_aliases_are_stable_within_and_distinct_between_bundles() -> None:
    identifiers = iter(BUNDLE_IDS)
    exporter, _ = _exporter(identifier_factory=lambda: next(identifiers))

    first = json.loads(exporter.export().json)
    second = json.loads(exporter.export().json)

    first_broker = first["brokers"][0]
    second_broker = second["brokers"][0]
    first_alias = first_broker["topics"][0]["topic_alias"]
    assert first_alias == first_broker["subscriptions"][0]["topic_alias"]
    assert first_alias == first_broker["health"]["findings"][0]["target"]
    assert first_alias != second_broker["topics"][0]["topic_alias"]


@pytest.mark.parametrize(
    "arguments",
    (
        {"include_payloads": "yes"},
        {"broker_limit": 0},
        {"broker_limit": 101},
        {"subscription_limit": 0},
        {"subscription_limit": 1_001},
        {"topic_limit": 0},
        {"topic_limit": 1_001},
        {"health_limit": 0},
        {"health_limit": 201},
        {"evidence_limit": -1},
        {"evidence_limit": 2_001},
        {"payload_limit_bytes": -1},
        {"payload_limit_bytes": 16_385},
    ),
)
def test_bundle_options_reject_unbounded_values(arguments) -> None:
    with pytest.raises(ValueError):
        SupportBundleOptions(**arguments)


def test_default_and_opt_in_topics_have_distinct_structural_types() -> None:
    service, _ = _service()
    identifiers = iter(BUNDLE_IDS)
    service._identifier_factory = lambda: next(identifiers)

    default_topic = service.generate_support_bundle().brokers[0].topics[0]
    payload_topic = service.generate_support_bundle(
        SupportBundleOptions(include_payloads=True)
    ).brokers[0].topics[0]

    assert type(default_topic) is SupportTopicState
    assert type(payload_topic) is SupportTopicStateWithPayload
    assert not hasattr(default_topic, "payload")


def test_bundle_reports_omitted_brokers_and_subscriptions() -> None:
    service, _ = _service()
    first = service._runtime.list_brokers.return_value[0]
    second = BrokerSummary(
        id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        name="Second private broker",
        config=MqttConfig("second.internal", 1883, "user", "password"),
        password_configured=True,
    )
    service._runtime.list_brokers.return_value = (first, second)
    service._runtime.list_subscriptions.return_value = (
        Subscription(REAL_TOPIC),
        Subscription("another/private/topic"),
    )

    bundle = service.generate_support_bundle(
        SupportBundleOptions(broker_limit=1, subscription_limit=1)
    )

    assert bundle.broker_results.total == 2
    assert bundle.broker_results.returned == 1
    assert bundle.broker_results.omitted == 1
    assert bundle.limitations == ("broker_results_omitted",)
    broker = bundle.brokers[0]
    assert broker.subscription_results.total == 2
    assert broker.subscription_results.returned == 1
    assert broker.subscription_results.omitted == 1
    assert "subscription_results_omitted" in broker.limitations


def test_safe_broker_reader_avoids_runtime_profile_hydration() -> None:
    service, _ = _service()
    service._broker_reader = lambda: (
        BrokerProfileSummary(
            BROKER_ID,
            "Private home",
            SECRET_HOST,
            8883,
            SECRET_USERNAME,
            True,
        ),
    )
    service._runtime.list_brokers.side_effect = AssertionError(
        "Full broker profiles must not be hydrated"
    )

    bundle = service.generate_support_bundle()

    assert bundle.brokers[0].port == 8883
    assert bundle.brokers[0].use_tls is True
    service._runtime.list_brokers.assert_not_called()


def test_wired_export_never_queries_the_credential_store(
    tmp_path: Path,
    credential_store,
) -> None:
    credentials = MagicMock(wraps=credential_store)
    dependencies = AppDependencies(tmp_path, credentials)
    credentials.reset_mock()

    try:
        artifacts = dependencies.support_bundle_exporter.export()
    finally:
        dependencies.topic_messages.close()
        dependencies._db_context.dispose()

    assert json.loads(artifacts.json)["brokers"]
    credentials.get_password.assert_not_called()
    credentials.set_password.assert_not_called()
    credentials.delete_password.assert_not_called()


def test_archive_writer_packages_exporter_artifacts(tmp_path: Path) -> None:
    artifacts = _exporter()[0].export()
    destination = tmp_path / "topicgate-support-20260908-120000.zip"

    written = SupportBundleArchiveWriter().write(destination, artifacts)

    assert written == destination.resolve()
    with ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            "support-bundle.json",
            "README.md",
            "redaction-manifest.json",
        }
        assert archive.read("support-bundle.json").decode() == artifacts.json
        assert archive.read("README.md").decode() == artifacts.markdown
        assert archive.read("redaction-manifest.json").decode() == artifacts.manifest


def test_archive_writer_preserves_destination_when_atomic_replace_fails(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "support.zip"
    destination.write_bytes(b"existing archive")
    artifacts = _exporter()[0].export()

    with patch(
        "topicgate.infrastructure.support_bundle_archive.os.replace",
        side_effect=OSError("replace failed"),
    ), pytest.raises(OSError, match="replace failed"):
        SupportBundleArchiveWriter().write(destination, artifacts)

    assert destination.read_bytes() == b"existing archive"
    assert list(tmp_path.iterdir()) == [destination]


def test_manifest_preview_uses_the_same_central_redaction_policies() -> None:
    exporter, _ = _exporter()

    default = json.loads(exporter.preview_redaction_manifest())
    opted_in = json.loads(
        exporter.preview_redaction_manifest(
            SupportBundleOptions(include_payloads=True)
        )
    )

    default_policies = {
        item["category"]: item["strategy"] for item in default["policies"]
    }
    opted_in_policies = {
        item["category"]: item["strategy"] for item in opted_in["policies"]
    }
    assert default_policies["payloads_and_evidence"] == "structurally_excluded"
    assert opted_in_policies["payloads_and_evidence"] == "bounded_included"
    assert default_policies["credentials"] == opted_in_policies["credentials"]


def test_desktop_archive_and_mcp_preserve_shared_security_invariants(
    tmp_path: Path,
) -> None:
    service, snapshots = _service()
    snapshots.build_resolved_current.return_value = replace(
        _snapshot(),
        results=SnapshotResultLimit(1, 7, 1, 6, 0, 0, True),
        completeness=SnapshotCompleteness(
            False,
            (
                SnapshotLimitation.CURRENT_STATE_ONLY,
                SnapshotLimitation.RESULT_LIMIT_REACHED,
            ),
        ),
    )
    service._health.get_health_report.return_value = replace(
        _health_report(),
        returned_count=1,
        omitted_count=5,
    )
    exporter = SupportBundleExporter(service)
    desktop = exporter.export(
        SupportBundleOptions(topic_limit=1, health_limit=1)
    )
    mcp = SupportBundleAPI(exporter).get_support_bundle()
    destination = SupportBundleArchiveWriter().write(
        tmp_path / "topicgate-support-20260908-120000.zip",
        desktop,
    )

    with ZipFile(destination) as archive:
        archived = {
            name: archive.read(name).decode()
            for name in archive.namelist()
        }
    combined = "\n".join(
        (
            desktop.json,
            desktop.markdown,
            desktop.manifest,
            json.dumps(mcp),
            *archived.values(),
        )
    )
    for structurally_excluded in (
        str(BROKER_ID),
        REAL_TOPIC,
        SECRET_PAYLOAD,
        SECRET_USERNAME,
        SECRET_PASSWORD,
        SECRET_HOST,
        SECRET_PATH,
        KEYRING_IDENTIFIER,
    ):
        assert structurally_excluded not in combined

    desktop_json = json.loads(desktop.json)
    mcp_json = mcp["content"]
    assert isinstance(mcp_json, dict)
    desktop_topic = desktop_json["brokers"][0]["topics"][0]
    mcp_topic = mcp_json["brokers"][0]["topics"][0]
    assert "payload" not in desktop_topic
    assert "payload" not in mcp_topic
    assert desktop_topic["topic_alias"] == (
        desktop_json["brokers"][0]["subscriptions"][0]["topic_alias"]
    )
    assert mcp_topic["topic_alias"] == (
        mcp_json["brokers"][0]["subscriptions"][0]["topic_alias"]
    )
    assert desktop_json["brokers"][0]["topic_results"]["omitted"] == 6
    assert mcp_json["brokers"][0]["topic_results"]["omitted"] == 6
    assert desktop_json["brokers"][0]["health"]["omitted_count"] == 5
    assert mcp_json["brokers"][0]["health"]["omitted_count"] == 5
    assert any("current_state_only" in warning for warning in desktop.warnings)
    assert any("topics omitted by bounds: 6" in warning for warning in desktop.warnings)
    assert any(
        "health findings omitted by bounds: 5" in warning
        for warning in desktop.warnings
    )
    assert mcp["warnings"]
    assert json.loads(desktop.manifest)["policies"][5]["strategy"] == (
        "structurally_excluded"
    )
    assert mcp["redaction_manifest"]["policies"][5]["strategy"] == (
        "structurally_excluded"
    )
    assert archived["support-bundle.json"] == desktop.json
    assert archived["README.md"] == desktop.markdown
    assert archived["redaction-manifest.json"] == desktop.manifest


def _exporter(identifier_factory=lambda: BUNDLE_IDS[0]):
    service, snapshots = _service(identifier_factory=identifier_factory)
    return SupportBundleExporter(service), snapshots


def _service(identifier_factory=lambda: BUNDLE_IDS[0]):
    broker = BrokerSummary(
        id=BROKER_ID,
        name="Private home",
        config=MqttConfig(
            SECRET_HOST,
            8883,
            SECRET_USERNAME,
            SECRET_PASSWORD,
            True,
            id=42,
        ),
        password_configured=True,
    )
    runtime = MagicMock()
    runtime.list_brokers.return_value = (broker,)
    runtime.list_subscriptions.return_value = (Subscription(REAL_TOPIC, qos=2),)
    snapshots = MagicMock()
    snapshots.build_resolved_current.return_value = _snapshot()
    health = MagicMock()
    health.get_health_report.return_value = _health_report()
    preflight = lambda: (
        SimpleNamespace(
            name="Database accessibility",
            status="pass",
            detail=f"Database is available at {SECRET_PATH}",
        ),
        SimpleNamespace(
            name="Credential store",
            status="pass",
            detail=KEYRING_IDENTIFIER,
        ),
    )
    service = SupportBundleService(
        runtime,
        snapshots,
        health_query_service=health,
        preflight_reader=preflight,
        version="1.4.0",
        clock=lambda: NOW,
        identifier_factory=identifier_factory,
    )
    return service, snapshots


def _snapshot() -> BrokerSnapshot:
    payload = SECRET_PAYLOAD.encode()
    topic = SnapshotTopicState(
        topic=REAL_TOPIC,
        payload=SnapshotPayload(
            encoding=SnapshotPayloadEncoding.UTF8,
            value=SECRET_PAYLOAD,
            original_size=len(payload),
            available_size=len(payload),
            rendered_size=len(payload),
            ingestion_truncated=False,
            rendering_truncated=False,
            truncated=False,
        ),
        qos=1,
        retain=True,
        received_at=NOW,
        age_seconds=0,
        message_count=1,
        source=ObservationSource.LIVE,
        status=SnapshotTopicStatus.LIVE,
    )
    return BrokerSnapshot(
        broker=SnapshotBrokerIdentity(BROKER_ID, "Private home"),
        connection_status="connected",
        captured_at=NOW,
        connected_at=NOW,
        observation_started_at=NOW,
        observed_for_seconds=0,
        topic_filter="#",
        topics=(topic,),
        dropped_message_count=0,
        freshness=SnapshotFreshness(None, 0),
        results=SnapshotResultLimit(100, 1, 1, 0, 0, 0, False),
        settling=SnapshotSettling(0, 5, 0),
        completeness=SnapshotCompleteness(
            False,
            (SnapshotLimitation.CURRENT_STATE_ONLY,),
        ),
    )


def _health_report() -> ExpectationHealthReport:
    finding = ExpectationHealthFinding(
        expectation_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        expectation_revision=1,
        name=f"Check {REAL_TOPIC}",
        description=SECRET_PASSWORD,
        target_kind="topic",
        target=REAL_TOPIC,
        status=HealthStatus.PROBLEM,
        failure_code="OUT_OF_RANGE",
        evidence_summary=f"actual {SECRET_PAYLOAD}",
        evidence_complete=True,
        evidence_truncated=False,
    )
    return ExpectationHealthReport(
        broker_id=BROKER_ID,
        evaluated_at=NOW,
        aggregate_status=HealthStatus.PROBLEM,
        evidence_complete=True,
        observation_status=HealthStatus.HEALTHY,
        observation_findings=(),
        expectation_findings=(finding,),
        active_failure_count=1,
        returned_count=1,
        omitted_count=0,
    )
