from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastmcp import Client, FastMCP

from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.mcp.api.health_api import HealthAPI


def _api():
    broker = SimpleNamespace(id=uuid4(), name="Primary")
    runtime = MagicMock()
    runtime.list_brokers.return_value = (broker,)
    query = MagicMock()
    return HealthAPI(query, BrokerResolver(runtime)), query, broker


async def test_health_api_registers_history_in_read_only_mode() -> None:
    api, _, _ = _api()
    mcp = FastMCP("test")
    api.register(mcp)

    async with Client(mcp) as client:
        tools = {item.name: item for item in await client.list_tools()}

    assert set(tools) == {"query_failure_history"}
    assert tools["query_failure_history"].annotations.readOnlyHint is True


async def test_health_api_registers_fresh_report_in_control_mode() -> None:
    api, _, _ = _api()
    mcp = FastMCP("test")
    api.register(mcp, control_enabled=True)

    async with Client(mcp) as client:
        tools = {item.name: item for item in await client.list_tools()}

    assert set(tools) == {"get_health_report", "query_failure_history"}
    assert tools["get_health_report"].annotations.readOnlyHint is False


def test_health_api_resolves_broker_and_forwards_bounds() -> None:
    api, query, broker = _api()

    api.get_health_report("primary", stale_after_seconds=20, limit=25)
    api.query_failure_history(
        "Primary",
        topic="devices/status",
        status="active",
        after=datetime(2026, 1, 1, tzinfo=timezone.utc),
        cursor=10,
        limit=25,
    )

    query.get_health_report.assert_called_once_with(
        broker.id,
        stale_after_seconds=20,
        limit=25,
    )
    assert query.query_failure_history.call_args.kwargs["broker_id"] == broker.id
    assert query.query_failure_history.call_args.kwargs["topic"] == "devices/status"

