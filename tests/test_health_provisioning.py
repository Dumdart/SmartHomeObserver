import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from fastmcp import Client, FastMCP
import pytest

from topicgate.app.app_dependencies import AppDependencies
from topicgate.app.services.broker_health_wait_service import BrokerHealthWaitService
from topicgate.app.services.control_operation_service import (
    ControlOperationConflict,
    ControlOperationService,
)
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.health import EqualCondition, HealthStatus, TopicTarget
from topicgate.core.models.health import FreshnessCondition
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.topic_message import TopicMessage
from topicgate.mcp.api.broker_api import BrokerAPI
from topicgate.mcp.api.expectation_api import ExpectationAPI
from topicgate.mcp.api.health_api import HealthAPI
from topicgate.mcp.api.subscription_api import SubscriptionAPI
from topicgate.mcp.requests.broker_creation import CreateBrokerRequest
from topicgate.mcp.requests.expectation_requests import CreateExpectationRequest


class FakeObserver:
    def __init__(self, config, subscriptions, **kwargs):
        self.subscriptions = tuple(subscriptions)
        self.connection_status = ConnectionStatus.DISCONNECTED
        self.observation_started_at = None
        self.dropped_message_count = 0
        self.recording_failure_count = 0
        self.subscription_failure_count = 0
        self.subscription_rejected_count = 0
        self.activations = 0

    async def stop(self):
        self.connection_status = ConnectionStatus.DISCONNECTED

    async def update_broker(self, config, subscriptions=None):
        self.activations += 1
        self.subscriptions = tuple(subscriptions or ())
        self.connection_status = ConnectionStatus.CONNECTED
        self.observation_started_at = datetime.now(timezone.utc)

    async def add_subscription(self, subscription):
        if any(
            item.topic_filter == subscription.topic_filter
            for item in self.subscriptions
        ):
            raise ValueError("Duplicate filter")
        self.subscriptions += (subscription,)


@pytest.fixture
def dependencies(tmp_path, credential_store, monkeypatch):
    monkeypatch.setattr(
        "topicgate.app.app_dependencies.ObserverMqttRepository", FakeObserver
    )
    deps = AppDependencies(tmp_path, credential_store)
    yield deps
    deps.topic_messages.close()
    deps._db_context.dispose()


def apis(deps):
    return (
        BrokerAPI(deps.runtime, deps.broker_resolver, MagicMock()),
        ExpectationAPI(
            deps.expectation_management_service, deps.broker_resolver, deps.runtime
        ),
        HealthAPI(
            deps.health_query_service, deps.broker_resolver, deps.health_wait_service
        ),
        SubscriptionAPI(deps.runtime, deps.broker_resolver),
    )


def broker_rule():
    return {
        "target": {"kind": "broker"},
        "condition": {
            "kind": "equal",
            "expected": {"encoding": "text", "value": "connected"},
        },
        "name": "Connected",
        "description": "The broker connection is established",
    }


async def test_protocol_workflow_and_persistence(dependencies):
    deps = dependencies
    mcp = FastMCP("workflow")
    for api in apis(deps):
        api.register(mcp, control_enabled=True)
    calls = 0
    async with Client(mcp) as client:

        async def call(name, args):
            nonlocal calls
            calls += 1
            return (await client.call_tool(name, args)).data

        saved = await call(
            "create_broker", {"request": {"name": "Lab", "host": "localhost"}}
        )
        broker = str(saved["broker_id"])
        await call("activate_broker", {"broker_id": broker})
        await call(
            "add_subscription", {"broker_id": broker, "topic_filter": "devices/#"}
        )
        rule = await call(
            "create_health_expectation", {"broker": broker, "request": broker_rule()}
        )
        result = await call("wait_for_broker_health", {"broker": broker})
        assert result["outcome"] == "satisfied"
        assert result["final_report"]["aggregate_status"] == "healthy"
        assert result["evaluation_count"] == 1
        assert calls == 5
        assert deps.runtime.active_repo.activations == 1
        assert "password" not in saved
        size = len(json.dumps(result, default=str).encode())
        print(
            f"workflow: calls={calls}, activations=1, wait_reconnects=0, response_bytes={size}, elapsed={result['elapsed_seconds']}"
        )
        reused = await call(
            "create_broker", {"request": {"name": " lab ", "host": "localhost"}}
        )
        assert reused["reused"] and str(reused["broker_id"]) == broker
        page = await call("list_health_expectations", {"broker": broker})
        assert str(page["items"][0]["expectation_id"]) == str(rule["expectation_id"])
    from topicgate.infrastructure.repository.health_expectation_repository import (
        HealthExpectationRepository,
    )
    from topicgate.infrastructure.database.database_context import DatabaseContext

    reopened = DatabaseContext(deps._db_context.url)
    try:
        assert (
            len(HealthExpectationRepository(reopened).list_for_broker(UUID(broker)))
            == 1
        )
    finally:
        reopened.dispose()


def test_creation_credentials_conflict_and_validation(dependencies):
    api = apis(dependencies)[0]
    saved = api.create_broker(
        CreateBrokerRequest(name="Auth", host="localhost", username="user")
    )
    assert saved["status"] == "needs_credentials"
    assert saved["password_configured"] is False
    with pytest.raises(ValueError, match="conflicts"):
        api.create_broker(CreateBrokerRequest(name="auth", host="different"))
    for extra in (
        {"password": "not-accepted"},
        {"credential_ref": "not-supported"},
        {"port": 0},
        {"port": True},
        {"name": " "},
        {"host": "mqtt://user@host"},
    ):
        with pytest.raises(ValueError):
            CreateBrokerRequest.model_validate(
                {"name": "Lab", "host": "localhost", **extra}
            )


@pytest.mark.parametrize("topic", ["", "devices/+", "devices/#", "bad\x00topic"])
def test_invalid_exact_topic_raises(topic):
    with pytest.raises(ValueError):
        TopicTarget(uuid4(), topic)


async def test_expectation_protocol_validation_and_scoping(dependencies):
    deps = dependencies
    mcp = FastMCP("validation")
    for api in apis(deps):
        api.register(mcp, control_enabled=True)
    broker = str(deps.runtime.active_broker.id)
    async with Client(mcp) as client:
        request = broker_rule()
        request["target"] = {"kind": "topic", "topic": "devices/status"}
        request["condition"]["expected"]["encoding"] = "utf8"
        result = await client.call_tool(
            "create_health_expectation",
            {"broker": broker, "request": request},
            raise_on_error=False,
        )
        assert result.is_error
        await client.call_tool(
            "add_subscription", {"broker_id": broker, "topic_filter": "devices/#"}
        )
        created = (
            await client.call_tool(
                "create_health_expectation", {"broker": broker, "request": request}
            )
        ).data
        item_id = UUID(str(created["expectation_id"]))
        assert (
            deps.health_expectation_repo.get(item_id).condition.expected_value
            == b"connected"
        )
        other = apis(deps)[0].create_broker(
            CreateBrokerRequest(name="Other", host="localhost")
        )
        for name, args in (
            ("update_health_expectation", {"changes": {"enabled": False}}),
            ("delete_health_expectation", {}),
        ):
            result = await client.call_tool(
                name,
                {
                    "broker": str(other["broker_id"]),
                    "expectation_id": str(item_id),
                    **args,
                },
                raise_on_error=False,
            )
            assert result.is_error
        for condition in (
            {"kind": "equal", "expected": {"encoding": "base64", "value": "!"}},
            {"kind": "numeric_range", "minimum": "NaN", "maximum": "2"},
            {"kind": "numeric_range", "minimum": "3", "maximum": "2"},
            {"kind": "freshness", "max_age_seconds": 0},
        ):
            result = await client.call_tool(
                "create_health_expectation",
                {"broker": broker, "request": {**request, "condition": condition}},
                raise_on_error=False,
            )
            assert result.is_error
        updated = (
            await client.call_tool(
                "update_health_expectation",
                {
                    "broker": broker,
                    "expectation_id": str(item_id),
                    "changes": {"enabled": False},
                },
            )
        ).data
        assert updated["revision"] == 1 and not updated["enabled"]


class Clock:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = lambda: None

    async def sleep(self, seconds):
        if seconds:
            self.now += seconds
            self.on_sleep()
        await asyncio.sleep(0)

    def utc(self):
        return datetime(2026, 9, 7, tzinfo=timezone.utc) + timedelta(seconds=self.now)


async def configured_wait(deps):
    broker = deps.runtime.active_broker.id
    await deps.runtime.activate_broker(broker)
    rule = apis(deps)[1].create_health_expectation(
        broker, CreateExpectationRequest.model_validate(broker_rule())
    )
    clock = Clock()
    service = BrokerHealthWaitService(
        deps.runtime,
        deps.expectation_management_service,
        deps.health_sink,
        deps.health_query_service,
        monotonic=lambda: clock.now,
        sleep=clock.sleep,
        utc_now=clock.utc,
    )
    return service, clock, broker, UUID(str(rule["expectation_id"]))


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "disabled",
        "dropped",
        "recording",
        "stability",
        "disconnect",
        "changed",
        "skipped",
    ],
)
async def test_wait_never_claims_success_without_required_evidence(
    dependencies, problem
):
    deps = dependencies
    service, clock, broker, identity = await configured_wait(deps)
    if problem in {"missing", "disabled"}:
        if problem == "disabled":
            deps.expectation_management_service.disable_expectation(identity)
        with pytest.raises(ValueError, match="enabled"):
            await service.wait(
                broker,
                required_expectation_ids=(
                    uuid4() if problem == "missing" else identity,
                ),
            )
        return
    if problem == "dropped":
        deps.runtime.active_repo.dropped_message_count = 1
    elif problem == "recording":
        deps.runtime.active_repo.recording_failure_count = 1
    elif problem == "skipped":
        deps.health_expectation_repo.update(
            replace(
                deps.health_expectation_repo.get(identity),
                condition=EqualCondition(b"connected"),
            )
        )
    elif problem == "stability":
        clock.on_sleep = lambda: setattr(
            deps.runtime.active_repo, "dropped_message_count", int(clock.now == 1)
        )
    elif problem == "disconnect":
        clock.on_sleep = lambda: setattr(
            deps.runtime.active_repo, "connection_status", ConnectionStatus.DISCONNECTED
        )
    elif problem == "changed":
        clock.on_sleep = lambda: deps.health_expectation_repo.update(
            replace(deps.health_expectation_repo.get(identity), enabled=False)
        )
    result = await service.wait(broker, timeout_seconds=3, stable_for_seconds=2)
    expected = {"disconnect": "disconnected", "changed": "configuration_changed"}.get(
        problem, "timed_out"
    )
    assert result["outcome"] == expected
    assert deps.runtime.active_repo.activations == 1


async def test_wait_cancellation_releases_cross_process_lease(dependencies):
    deps = dependencies
    service, clock, broker, _ = await configured_wait(deps)
    entered = asyncio.Event()

    async def sleep(seconds):
        if seconds:
            entered.set()
            await asyncio.Future()

    service._sleep = sleep
    other = ControlOperationService(deps._db_context, "other")
    task = asyncio.create_task(service.wait(broker, stable_for_seconds=5))
    await entered.wait()
    with pytest.raises(ControlOperationConflict):
        with other.operation("conflict"):
            pass
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fresh = ControlOperationService(deps._db_context, "fresh")
    with fresh.operation("after cancellation"):
        pass


async def test_wait_full_scope_before_truncation_and_subset(dependencies):
    deps = dependencies
    service, clock, broker, identity = await configured_wait(deps)
    original = deps.health_expectation_repo.get(identity)
    for _ in range(201):
        deps.health_expectation_repo.create(replace(original, expectation_id=uuid4()))
    bad = replace(
        original, expectation_id=uuid4(), condition=EqualCondition("disconnected")
    )
    deps.health_expectation_repo.create(bad)
    result = await service.wait(broker, timeout_seconds=1, limit=1)
    assert result["outcome"] == "timed_out"
    assert result["evaluated_count"] == 203
    assert result["final_report"].omitted_count == 202
    assert len(result["final_report"].checkpoint.entries) == 200
    assert result["final_report"].checkpoint.omitted_count == 3
    assert result["final_report"].checkpoint.complete is False
    assert result["final_report"].delta is None
    result = await service.wait(broker, required_expectation_ids=(identity,), limit=1)
    assert result["outcome"] == "satisfied"
    assert result["scope"] == "required_subset"
    assert result["final_report"].aggregate_status == HealthStatus.PROBLEM
    failures = deps.expectation_failure_repo.get_all_states()
    assert len(failures) == 1 and failures[0].occurrence_count == 1


@pytest.mark.parametrize(
    "change",
    [
        {"timeout_seconds": float("nan")},
        {"stable_for_seconds": 61},
        {"poll_interval_seconds": 0},
        {"limit": 0},
    ],
)
async def test_wait_rejects_bounds_before_lease(dependencies, change):
    service = dependencies.health_wait_service
    with pytest.raises(ValueError):
        await service.wait(dependencies.runtime.active_broker.id, **change)


@pytest.mark.parametrize(
    "evidence", ["missing", "stale", "truncated", "retained", "freshness"]
)
async def test_topic_evidence_and_time_only_transitions(dependencies, evidence):
    deps = dependencies
    service, clock, broker, identity = await configured_wait(deps)
    await deps.runtime.add_subscription(broker, Subscription("devices/#"))
    deps.expectation_management_service.edit_expectation(
        identity,
        new_target=TopicTarget(broker, "devices/status"),
        new_condition=FreshnessCondition(0.5)
        if evidence == "freshness"
        else EqualCondition(b"online"),
    )
    if evidence != "missing":
        deps.topic_messages.record_message(
            TopicMessage(
                broker,
                "devices/status",
                b"online",
                1,
                evidence == "retained",
                clock.utc() - timedelta(seconds=1000 if evidence == "stale" else 0),
                6,
                1,
                uuid4(),
                is_truncated=evidence == "truncated",
            )
        )
    result = await service.wait(
        broker,
        timeout_seconds=2,
        stable_for_seconds=2 if evidence == "freshness" else 0,
    )
    assert result["outcome"] == ("satisfied" if evidence == "retained" else "timed_out")
    if evidence == "freshness":
        assert result["final_report"].aggregate_status == HealthStatus.PROBLEM


async def test_wait_deadline_covers_evaluation(dependencies, monkeypatch):
    deps = dependencies
    service, clock, broker, _ = await configured_wait(deps)
    original = deps.health_sink.evaluate_broker

    def slow(*args, **kwargs):
        clock.now += 2
        return original(*args, **kwargs)

    monkeypatch.setattr(deps.health_sink, "evaluate_broker", slow)
    result = await service.wait(broker, timeout_seconds=1)
    assert result["outcome"] == "timed_out"
    assert result["final_report"] is None
    assert result["domain_status"] == HealthStatus.UNKNOWN
    assert not result["evidence_complete"]


async def test_empty_expectations_and_inactive_mutation(dependencies):
    deps = dependencies
    broker = deps.runtime.active_broker.id
    await deps.runtime.activate_broker(broker)
    with pytest.raises(ValueError, match="enabled"):
        await deps.health_wait_service.wait(broker)
    other = apis(deps)[0].create_broker(
        CreateBrokerRequest(name="Inactive", host="localhost")
    )
    with pytest.raises(ValueError):
        await deps.runtime.add_subscription(
            other["broker_id"], Subscription("devices/#")
        )
    assert deps.runtime.list_subscriptions(other["broker_id"]) == ()


async def test_management_participates_in_cross_process_lease(dependencies):
    deps = dependencies
    service, clock, broker, identity = await configured_wait(deps)
    other = ControlOperationService(deps._db_context, "desktop")
    with other.operation("desktop configuration"):
        with pytest.raises(ControlOperationConflict):
            deps.expectation_management_service.disable_expectation(identity)
    with pytest.raises(ControlOperationConflict, match="Restart"):
        deps.expectation_management_service.disable_expectation(identity)


async def test_lease_conflict_is_actionable_through_masked_protocol(dependencies):
    from topicgate.mcp.middleware import ErrorHandlingMiddleware

    deps = dependencies
    other = ControlOperationService(deps._db_context, "desktop")
    mcp = FastMCP(
        "lease errors", mask_error_details=True, middleware=[ErrorHandlingMiddleware()]
    )
    apis(deps)[1].register(mcp, control_enabled=True)
    async with Client(mcp) as client:
        with other.operation("configuration"):
            result = await client.call_tool(
                "create_health_expectation",
                {
                    "broker": str(deps.runtime.active_broker.id),
                    "request": broker_rule(),
                },
                raise_on_error=False,
            )
    assert result.is_error
    assert "Retry after it finishes" in result.content[0].text


async def test_wait_detects_generation_interference(dependencies):
    from sqlalchemy import text

    deps = dependencies
    service, clock, broker, _ = await configured_wait(deps)

    def change_generation():
        with deps._db_context.transaction() as session:
            session.execute(
                text(
                    "UPDATE control_operation_state SET generation = generation + 1 WHERE id = 1"
                )
            )

    clock.on_sleep = change_generation
    result = await service.wait(broker, stable_for_seconds=1)
    assert result["outcome"] == "configuration_changed"


async def test_unknown_broker_is_actionable_through_masked_protocol(dependencies):
    from topicgate.mcp.middleware import ErrorHandlingMiddleware

    mcp = FastMCP(
        "selector errors",
        mask_error_details=True,
        middleware=[ErrorHandlingMiddleware()],
    )
    apis(dependencies)[1].register(mcp)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_health_expectations",
            {
                "broker": "Missing profile",
            },
            raise_on_error=False,
        )
    assert result.is_error and "Unknown broker name" in result.content[0].text
