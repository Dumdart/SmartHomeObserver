import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastmcp import Client, FastMCP

from topicgate.app.models.expectation_health_report import (
    ExpectationHealthReport,
    FindingCheckpoint,
)
from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.core.models.health import HealthStatus
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
    assert "checkpoint" in tools["get_health_report"].inputSchema["properties"]
    assert {"checkpoint", "delta"} <= tools["get_health_report"].outputSchema[
        "properties"
    ].keys()
    assert "round-trip" in tools["get_health_report"].description.lower()


async def test_health_api_round_trips_checkpoint_through_mcp() -> None:
    api, query, broker = _api()
    checkpoint = FindingCheckpoint(1, broker.id, (), True, 0)
    query.get_health_report.return_value = ExpectationHealthReport(
        broker_id=broker.id,
        evaluated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        aggregate_status=HealthStatus.HEALTHY,
        evidence_complete=True,
        observation_status=HealthStatus.HEALTHY,
        observation_findings=(),
        expectation_findings=(),
        active_failure_count=0,
        returned_count=0,
        omitted_count=0,
        checkpoint=checkpoint,
        delta=None,
    )
    mcp = FastMCP("test")
    api.register(mcp, control_enabled=True)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_health_report",
            {
                "broker": "primary",
                "checkpoint": {
                    "version": 1,
                    "broker_id": str(broker.id),
                    "entries": [],
                    "complete": True,
                    "omitted_count": 0,
                },
            },
        )

    forwarded = query.get_health_report.call_args.kwargs["checkpoint"]
    assert forwarded == checkpoint
    payload = json.loads(result.content[0].text)
    assert payload["checkpoint"]["version"] == 1
    assert payload["delta"] is None


def test_health_api_resolves_broker_and_forwards_bounds() -> None:
    api, query, broker = _api()
    checkpoint = FindingCheckpoint(1, broker.id, (), True, 0)

    api.get_health_report(
        "primary",
        stale_after_seconds=20,
        limit=25,
        checkpoint=checkpoint,
    )
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
        checkpoint=checkpoint,
    )
    assert query.query_failure_history.call_args.kwargs["broker_id"] == broker.id
    assert query.query_failure_history.call_args.kwargs["topic"] == "devices/status"

