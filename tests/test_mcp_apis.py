import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP

from topicgate.app.services.broker_inspection_service import BrokerInspectionService
from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.mqtt_observation import MqttObservation as TopicState
from topicgate.core.models.subscription import Subscription
from topicgate.mcp.api.broker_api import BrokerAPI
from topicgate.mcp.api.connection_api import ConnectionAPI
from topicgate.mcp.api.mcp_api import McpApiContainer
from topicgate.mcp.api.publish_api import PublishAPI
from topicgate.mcp.api.snapshot_api import SnapshotAPI
from topicgate.mcp.api.subscription_api import SubscriptionAPI
from topicgate.mcp.api.topic_api import TopicAPI
from topicgate.mcp.capabilities import McpMode
from topicgate.mcp.server import Server, parse_mode


def mcp_runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.active_broker = SimpleNamespace(id=uuid4(), name="Primary")
    runtime.list_brokers.return_value = (runtime.active_broker,)
    runtime.connection_status = ConnectionStatus.DISCONNECTED
    runtime.dropped_message_count = 2
    runtime.topic_update_interval = 0.1
    runtime.get_connection_status.return_value = ConnectionStatus.DISCONNECTED
    runtime.get_dropped_message_count.return_value = 2
    runtime.get_topic_update_interval.return_value = 0.1
    runtime.list_subscriptions.return_value = ()
    runtime.list_topics.return_value = ()
    runtime.get_topic_state.return_value = None
    runtime.add_subscription = AsyncMock()
    runtime.update_subscription = AsyncMock()
    runtime.remove_subscription = AsyncMock()
    runtime.connect = AsyncMock()
    runtime.disconnect = AsyncMock()
    runtime.reconnect = AsyncMock()
    runtime.publish = AsyncMock()
    runtime.activate_broker = AsyncMock(return_value=runtime.active_broker)
    return runtime


def resolver(runtime: MagicMock) -> BrokerResolver:
    return BrokerResolver(runtime)


def broker_api(runtime: MagicMock) -> BrokerAPI:
    selected_resolver = resolver(runtime)
    return BrokerAPI(
        runtime,
        selected_resolver,
        BrokerInspectionService(runtime, MagicMock(), selected_resolver),
    )


async def test_non_broker_apis_register_described_tools() -> None:
    async def scenario() -> None:
        runtime = mcp_runtime()
        selected_resolver = resolver(runtime)
        mcp = FastMCP("test")
        McpApiContainer(
            [
                BrokerAPI(
                    runtime,
                    selected_resolver,
                    BrokerInspectionService(runtime, MagicMock(), selected_resolver),
                ),
                ConnectionAPI(runtime, selected_resolver),
                PublishAPI(runtime, selected_resolver),
                SubscriptionAPI(runtime, selected_resolver),
                TopicAPI(runtime, selected_resolver),
            ],
            control_enabled=True,
        ).register(mcp)

        async with Client(mcp) as client:
            tools = await client.list_tools()

        assert {item.name for item in tools} == {
            "activate_broker",
            "create_broker",
            "add_subscription",
            "connect",
            "disconnect",
            "get_connection_status",
            "inspect_broker",
            "get_topic_state",
            "list_brokers",
            "list_subscriptions",
            "list_topics",
            "publish",
            "reconnect",
            "remove_subscription",
            "update_subscription",
        }
        for item in tools:
            assert item.description is not None
            assert "Side effects:" in item.description
            assert "Required state:" in item.description
            assert "Identifiers:" in item.description
            assert "Failures:" in item.description

    await scenario()


async def test_read_only_server_hides_every_control_capability() -> None:
    runtime = mcp_runtime()
    dependencies = SimpleNamespace(
        runtime=runtime,
        broker_resolver=resolver(runtime),
        snapshot_service=MagicMock(),
        health_query_service=MagicMock(),
        health_wait_service=MagicMock(),
        expectation_management_service=MagicMock(),
        support_bundle_exporter=MagicMock(),
        service_items=(),
    )

    with patch(
        "topicgate.mcp.server.AppDependencies",
        return_value=dependencies,
    ):
        server = Server(McpMode.READ_ONLY)

    async with Client(server.mcp) as client:
        tools = await client.list_tools()

    assert {item.name for item in tools} == {
        "get_broker_snapshot",
        "get_connection_status",
        "get_topic_state",
        "inspect_broker",
        "list_brokers",
        "list_subscriptions",
        "list_topics",
        "query_failure_history",
        "get_support_bundle",
        "list_health_expectations",
    }
    assert all(item.annotations.readOnlyHint is True for item in tools)


async def test_control_server_keeps_support_bundle_read_only() -> None:
    runtime = mcp_runtime()
    dependencies = SimpleNamespace(
        runtime=runtime,
        broker_resolver=resolver(runtime),
        snapshot_service=MagicMock(),
        health_query_service=MagicMock(),
        health_wait_service=MagicMock(),
        expectation_management_service=MagicMock(),
        support_bundle_exporter=MagicMock(),
        service_items=(),
    )

    with patch(
        "topicgate.mcp.server.AppDependencies",
        return_value=dependencies,
    ):
        server = Server(McpMode.CONTROL)

    async with Client(server.mcp) as client:
        tools = {item.name: item for item in await client.list_tools()}

    support_bundle = tools["get_support_bundle"]
    assert support_bundle.annotations.readOnlyHint is True
    assert support_bundle.annotations.openWorldHint is False


def test_mcp_mode_defaults_to_read_only_and_requires_explicit_control() -> None:
    assert parse_mode([]) is McpMode.READ_ONLY
    assert parse_mode(["--mode", "read-only"]) is McpMode.READ_ONLY
    assert parse_mode(["--mode", "control"]) is McpMode.CONTROL


async def test_legacy_inspection_tools_expose_legacy_metadata() -> None:
    runtime = mcp_runtime()
    selected_resolver = resolver(runtime)
    mcp = FastMCP("test")
    McpApiContainer(
        [
            TopicAPI(runtime, selected_resolver),
            SnapshotAPI(MagicMock()),
        ]
    ).register(mcp)

    async with Client(mcp) as client:
        tools = {item.name: item for item in await client.list_tools()}

    assert tools["get_broker_snapshot"].meta["legacy"] is True
    assert tools["get_topic_state"].meta["legacy"] is True
    assert tools["list_topics"].meta["legacy"] is True


async def test_subscription_api_maps_flat_arguments_to_domain_models() -> None:
    async def scenario() -> None:
        runtime = mcp_runtime()
        broker_id = runtime.active_broker.id
        original = Subscription("home/#", qos=1, id=7)
        runtime.list_subscriptions.return_value = (original,)
        api = SubscriptionAPI(runtime, resolver(runtime))

        await api.add_subscription(broker_id, "devices/+", qos=2)
        await api.update_subscription(
            broker_id,
            "devices/+",
            "devices/#",
            retain_as_published=True,
            retain_handling=1,
        )
        await api.remove_subscription(broker_id, "home/#")

        runtime.add_subscription.assert_awaited_once_with(
            broker_id,
            Subscription("devices/+", qos=2),
        )
        runtime.update_subscription.assert_awaited_once_with(
            broker_id,
            "devices/+",
            Subscription(
                "devices/#",
                retain_as_published=True,
                retain_handling=1,
            ),
        )
        runtime.remove_subscription.assert_awaited_once_with(broker_id, original)

    await scenario()


async def test_remove_subscription_rejects_an_unknown_filter() -> None:
    async def scenario() -> None:
        runtime = mcp_runtime()

        with pytest.raises(ValueError, match="Unknown subscription filter"):
            await SubscriptionAPI(runtime, resolver(runtime)).remove_subscription(
                runtime.active_broker.id,
                "missing/#",
            )

        runtime.remove_subscription.assert_not_awaited()

    await scenario()


def test_topic_api_returns_text_and_binary_safe_payload_views() -> None:
    runtime = mcp_runtime()
    broker_id = runtime.active_broker.id
    received_at = datetime.now(timezone.utc)
    api = TopicAPI(runtime, resolver(runtime))

    runtime.get_topic_state.return_value = TopicState(
        "status",
        "home/status",
        b"online",
        1,
        True,
        received_at,
    )
    text_result = api.get_topic_state(broker_id, "home/status")

    assert text_result is not None
    assert text_result.payload_text == "online"
    assert text_result.payload_base64 == "b25saW5l"
    assert text_result.received_at == received_at

    runtime.get_topic_state.return_value = TopicState(
        "image",
        "camera/image",
        b"\xff\x00",
        0,
        False,
        received_at,
    )
    binary_result = api.get_topic_state(broker_id, "camera/image")

    assert binary_result is not None
    assert binary_result.payload_text is None
    assert binary_result.payload_base64 == "/wA="


async def test_connection_api_reports_status_and_delegates_commands() -> None:
    async def scenario() -> None:
        runtime = mcp_runtime()
        runtime.connection_status = ConnectionStatus.CONNECTED
        runtime.get_connection_status.return_value = ConnectionStatus.CONNECTED
        api = ConnectionAPI(runtime, resolver(runtime))

        result = api.get_connection_status()
        await api.connect()
        await api.disconnect()
        await api.reconnect()

        assert result.broker_id == runtime.active_broker.id
        assert result.status == "connected"
        assert result.dropped_message_count == 2
        runtime.connect.assert_awaited_once_with()
        runtime.disconnect.assert_awaited_once_with()
        runtime.reconnect.assert_awaited_once_with()

    await scenario()


async def test_publish_api_supports_utf8_and_base64_payloads() -> None:
    async def scenario() -> None:
        runtime = mcp_runtime()
        broker_id = runtime.active_broker.id
        api = PublishAPI(runtime, resolver(runtime))

        await api.publish(broker_id, "home/set", "p\u00e5", "utf-8")
        await api.publish(broker_id, "camera/set", "/wA=", "base64")

        assert runtime.publish.await_args_list[0].args == (
            broker_id,
            "home/set",
            "p\u00e5".encode("utf-8"),
        )
        assert runtime.publish.await_args_list[1].args == (
            broker_id,
            "camera/set",
            b"\xff\x00",
        )

        with pytest.raises(ValueError, match="not valid base64"):
            await api.publish(broker_id, "camera/set", "%%%", "base64")

        with pytest.raises(ValueError, match="Unsupported payload encoding"):
            await api.publish(
                broker_id,
                "home/set",
                "on",
                "ascii",
            )

    await scenario()


async def test_publish_tool_requires_explicit_inputs_and_safety_annotations() -> None:
    runtime = mcp_runtime()
    mcp = FastMCP("test")
    PublishAPI(runtime, resolver(runtime)).register(mcp, control_enabled=True)

    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert len(tools) == 1
    publish = tools[0]
    assert publish.name == "publish"
    assert set(publish.inputSchema["required"]) == {
        "broker_id",
        "topic",
        "payload",
        "payload_encoding",
    }
    assert publish.annotations.destructiveHint is True
    assert publish.annotations.openWorldHint is True


async def test_broker_scoped_apis_accept_profile_names() -> None:
    runtime = mcp_runtime()
    broker_id = runtime.active_broker.id
    runtime.list_topics.return_value = ("home/status",)
    runtime.list_subscriptions.return_value = (Subscription("home/#"),)

    selected_resolver = resolver(runtime)
    assert TopicAPI(runtime, selected_resolver).list_topics(" primary ") == (
        "home/status",
    )
    assert (
        TopicAPI(runtime, selected_resolver).get_topic_state(
            "PRIMARY", "home/status"
        )
        is None
    )
    status = ConnectionAPI(runtime, selected_resolver).get_connection_status("Primary")
    subscription_api = SubscriptionAPI(runtime, selected_resolver)
    subscriptions = subscription_api.list_subscriptions("Primary")
    await subscription_api.add_subscription("Primary", "devices/#")
    await broker_api(runtime).activate_broker("Primary")
    await PublishAPI(runtime, selected_resolver).publish(
        "Primary",
        "home/set",
        "on",
        "utf-8",
    )

    assert status.broker_id == broker_id
    assert subscriptions == (Subscription("home/#"),)
    runtime.list_topics.assert_called_with(broker_id)
    runtime.get_topic_state.assert_called_with(broker_id, "home/status")
    runtime.get_connection_status.assert_called_with(broker_id)
    runtime.list_subscriptions.assert_called_with(broker_id)
    runtime.add_subscription.assert_awaited_once_with(
        broker_id,
        Subscription("devices/#"),
    )
    runtime.activate_broker.assert_awaited_once_with(broker_id)
    runtime.publish.assert_awaited_once_with(broker_id, "home/set", b"on")


async def test_mcp_activation_reports_selected_broker_as_disconnected_on_failure() -> None:
    runtime = mcp_runtime()
    selected = SimpleNamespace(id=uuid4(), name="Offline")
    runtime.list_brokers.return_value = (runtime.active_broker, selected)

    async def fail_activation(broker_id):
        runtime.active_broker = selected
        raise ConnectionError("unavailable")

    runtime.activate_broker.side_effect = fail_activation

    with pytest.raises(
        ConnectionError,
        match="Broker 'Offline' is active but disconnected: unavailable",
    ):
        await broker_api(runtime).activate_broker(selected.id)

    assert runtime.active_broker.id == selected.id


async def test_legacy_mcp_call_shapes_remain_supported() -> None:
    runtime = mcp_runtime()
    selected_resolver = resolver(runtime)
    mcp = FastMCP("test")
    McpApiContainer(
        [
            ConnectionAPI(runtime, selected_resolver),
            SubscriptionAPI(runtime, selected_resolver),
            TopicAPI(runtime, selected_resolver),
        ]
    ).register(mcp)

    async with Client(mcp) as client:
        await client.call_tool("list_topics", {})
        await client.call_tool("get_connection_status", {})
        await client.call_tool(
            "get_topic_state",
            {
                "broker_id": str(runtime.active_broker.id),
                "topic": "home/status",
            },
        )
        await client.call_tool(
            "list_subscriptions",
            {"broker_id": str(runtime.active_broker.id)},
        )

    runtime.list_topics.assert_called_with(runtime.active_broker.id)
    runtime.get_topic_state.assert_called_with(
        runtime.active_broker.id,
        "home/status",
    )
    runtime.list_subscriptions.assert_called_with(runtime.active_broker.id)


async def test_server_lifespan_starts_disconnected_after_initial_connection_failure() -> None:
    async def scenario() -> None:
        server = Server.__new__(Server)
        server.services = MagicMock()
        server.services.start_services = AsyncMock(
            side_effect=ConnectionError("broker unavailable")
        )
        server.services.stop_services = AsyncMock()
        server.dependencies = MagicMock()
        server.dependencies.runtime.stop = AsyncMock()

        async with server._lifespan(MagicMock()):
            pass

        server.services.stop_services.assert_awaited_once_with()
        server.dependencies.runtime.stop.assert_awaited_once_with()

    await scenario()
