import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from demo.zigbee2mqtt_scenario import publisher
from topicgate.app.services.health_expectation_service import (
    HealthExpectationService,
)
from topicgate.core.interfaces import DiagnosticPack, PackReference
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.health import ConditionEvaluationContext, HealthStatus
from topicgate.core.models.observation_status import ObservationStatus
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.topic_message import TopicMessage
from topicgate.infrastructure.diagnostic_packs import load_zigbee2mqtt_pack
from topicgate.processors.action_dispatcher import ActionDispatcher
from topicgate.processors.health_action_registry import HealthActionRegistry
from topicgate.processors.transition_tracker import TransitionTracker

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


class PublishResult:
    def wait_for_publish(self, timeout=None):
        return None


class RecordingClient:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.messages.append((topic, payload, qos, retain))
        return PublishResult()


class PackExpectationReader:
    def __init__(self, expectations):
        self._expectations = expectations

    def list_for_broker(self, broker_id):
        return tuple(
            item
            for item in self._expectations
            if item.target.broker_id == broker_id
        )

    def list_for_topic(self, broker_id, topic):
        return tuple(
            item
            for item in self._expectations
            if item.target.broker_id == broker_id and item.target.topic == topic
        )


def test_pack_has_a_versioned_reference_and_stable_expectations() -> None:
    pack = load_zigbee2mqtt_pack()
    broker_id = uuid4()

    first = pack.build_expectations(broker_id)
    second = pack.build_expectations(broker_id)

    assert isinstance(pack, DiagnosticPack)
    assert pack.reference == PackReference("zigbee2mqtt", "1.0.0")
    assert first == second
    assert len(first) == 7
    assert all(item.target.broker_id == broker_id for item in first)


def test_pack_evaluates_the_deterministic_zigbee2mqtt_scenario() -> None:
    client = RecordingClient()
    publisher.publish_initial_state(client)
    publisher.publish_healthy_state(client)
    client.publish(
        "zigbee2mqtt/basement_freezer",
        publisher.encode({"temperature": -18.7}),
        qos=1,
        retain=True,
    )
    messages = {topic: payload for topic, payload, _, _ in client.messages}
    broker_id = uuid4()
    current_topics = tuple(
        CurrentTopic(
            _message(
                broker_id,
                topic,
                payload,
                received_at=(
                    NOW - timedelta(minutes=5)
                    if topic == "zigbee2mqtt/attic_sensor"
                    else NOW
                ),
            ),
            ObservationStatus.LIVE,
        )
        for topic, payload in messages.items()
    )
    expectations = load_zigbee2mqtt_pack().build_expectations(broker_id)
    repository = PackExpectationReader(expectations)
    transaction_manager = MagicMock()
    transaction_manager.transaction.side_effect = lambda: nullcontext(object())
    evaluator = HealthExpectationService(
        repository,
        MagicMock(get=MagicMock(return_value=None)),
        MagicMock(),
        transaction_manager,
        TransitionTracker(),
        ActionDispatcher(HealthActionRegistry({})),
        subscriptions_reader=lambda _: (Subscription("zigbee2mqtt/#"),),
        broker_metadata_reader=lambda _: SimpleNamespace(
            connection_status=ConnectionStatus.CONNECTED,
            observation_started_at=NOW - timedelta(minutes=10),
            dropped_message_count=0,
            recording_failure_count=0,
            subscription_failure_count=0,
            subscription_rejected_count=0,
        ),
        current_topics_reader=lambda _: current_topics,
    )

    report = evaluator.evaluate_broker(broker_id, evaluated_at=NOW)

    statuses = {
        expectation.name: finding.status
        for expectation, finding in zip(
            expectations,
            report.topic_findings,
            strict=True,
        )
    }
    assert statuses == {
        "Attic sensor is fresh": HealthStatus.PROBLEM,
        "Basement freezer temperature is in range": HealthStatus.HEALTHY,
        "Garage sensor is available": HealthStatus.PROBLEM,
        "Kitchen sensor is available": HealthStatus.HEALTHY,
        "Kitchen temperature is in range": HealthStatus.HEALTHY,
        "Nursery sensor topic is present": HealthStatus.PROBLEM,
        "Zigbee2MQTT bridge is online": HealthStatus.HEALTHY,
    }


def test_json_adapter_returns_unknown_for_malformed_or_unsupported_payloads() -> None:
    condition = next(
        item.condition
        for item in load_zigbee2mqtt_pack().build_expectations(uuid4())
        if item.name == "Zigbee2MQTT bridge is online"
    )

    for payload in (
        b"not-json",
        b"[]",
        b"{}",
        b'{"state":true}',
        b'{"state":NaN}',
    ):
        result = condition.evaluate(
            ConditionEvaluationContext(payload, NOW, NOW)
        )

        assert result.status is HealthStatus.UNKNOWN
        assert result.failure_code == "UNSUPPORTED_PACK_PAYLOAD"
        assert result.evidence_complete is False


def _message(
    broker_id,
    topic: str,
    payload: str,
    *,
    received_at: datetime,
) -> TopicMessage:
    encoded = payload.encode("utf-8")
    json.loads(payload)
    return TopicMessage(
        broker_id=broker_id,
        topic=topic,
        payload=encoded,
        qos=1,
        retain=False,
        received_at=received_at,
        payload_size=len(encoded),
        message_count=1,
        observation_id=uuid4(),
    )
