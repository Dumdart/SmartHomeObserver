from datetime import datetime
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.tools import tool

from topicgate.app.models.expectation_health_report import (
    ExpectationHealthReport,
    FailureHistoryResult,
    FindingCheckpoint,
)
from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.app.services.health_expectation_service import (
    DEFAULT_STALE_AFTER_SECONDS,
)
from topicgate.app.services.health_query_service import (
    DEFAULT_HEALTH_RESULT_LIMIT,
    HealthQueryService,
)
from topicgate.mcp.api.mcp_api import MCPApi
from topicgate.app.services.broker_health_wait_service import BrokerHealthWaitService


class HealthAPI(MCPApi):
    def __init__(
        self,
        query_service: HealthQueryService,
        resolver: BrokerResolver,
        wait_service: BrokerHealthWaitService | None = None,
    ) -> None:
        self._query_service = query_service
        self._resolver = resolver
        self._wait_service = wait_service

    def register(self, mcp: FastMCP, *, control_enabled: bool = False) -> None:
        mcp.add_tool(self.query_failure_history)
        if control_enabled:
            mcp.add_tool(self.get_health_report)
            if self._wait_service is not None:
                mcp.add_tool(self.wait_for_broker_health)

    @tool()
    async def wait_for_broker_health(
        self,
        broker: UUID | str,
        required_expectation_ids: tuple[UUID, ...] | None = None,
        timeout_seconds: float = 30,
        poll_interval_seconds: float = 1,
        stable_for_seconds: float = 0,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        limit: int = 50,
    ) -> dict:
        """Wait once for complete healthy evidence on the active connected broker.

        Side effects: Evaluates and persists transitions, receives MQTT on the existing
        connection, and leaves the selected broker active. Never reconnects or publishes.
        Required state: Active connected broker and nonempty enabled expectations.
        Identifiers: broker UUID or unique name; optional broker-scoped expectation UUIDs.
        Failures: Invalid bounds/IDs, lease or storage errors; cancellation releases the lease.
        Outcomes: satisfied, timed_out, disconnected, configuration_changed. Inspect
        final_report for actual whole-broker health, completeness and omitted findings.
        """
        resolved = self._resolver.resolve(broker)
        if self._wait_service is None:
            raise RuntimeError("Health waiting is unavailable.")
        return await self._wait_service.wait(
            resolved.id,
            required_expectation_ids=required_expectation_ids,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            stable_for_seconds=stable_for_seconds,
            stale_after_seconds=stale_after_seconds,
            limit=limit,
        )

    @tool(annotations={"readOnlyHint": False})
    def get_health_report(
        self,
        broker: UUID | str,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        limit: int = DEFAULT_HEALTH_RESULT_LIMIT,
        checkpoint: FindingCheckpoint | None = None,
    ) -> ExpectationHealthReport:
        """Freshly evaluate bounded expectation health for a broker.

        Side effects: May persist health transitions and failure episodes locally;
        it never activates, connects, waits for, or publishes to MQTT.
        Required state: The broker profile and local health database must exist.
        Identifiers: broker accepts a UUID or unique case-insensitive profile name.
        Failures: Fails for unknown or ambiguous brokers, invalid bounds, or health
        evaluation and persistence errors. Round-trip the returned checkpoint on the
        next request to receive a bounded delta and replacement checkpoint. Checkpoint
        and delta completeness/omitted counts disclose whether their 200-item bounds
        prevented complete recovery or event reporting.
        """
        resolved = self._resolver.resolve(broker)
        return self._query_service.get_health_report(
            resolved.id,
            stale_after_seconds=stale_after_seconds,
            limit=limit,
            checkpoint=checkpoint,
        )

    @tool(annotations={"readOnlyHint": True})
    def query_failure_history(
        self,
        broker: UUID | str,
        topic: str | None = None,
        status: str = "all",
        after: datetime | None = None,
        before: datetime | None = None,
        cursor: int | None = None,
        limit: int = DEFAULT_HEALTH_RESULT_LIMIT,
    ) -> FailureHistoryResult:
        """Query a bounded page of persisted expectation failures.

        Side effects: None; this reads local failure history only.
        Required state: The broker profile and local health database must exist.
        Identifiers: broker accepts a UUID or unique case-insensitive profile name;
        topic is an exact MQTT topic and status is all, active, or recovered.
        Failures: Fails for unknown or ambiguous brokers, invalid time ranges,
        cursors, status values, or result limits.
        """
        resolved = self._resolver.resolve(broker)
        return self._query_service.query_failure_history(
            broker_id=resolved.id,
            topic=topic,
            status=status,
            after=after,
            before=before,
            cursor=cursor,
            limit=limit,
        )
