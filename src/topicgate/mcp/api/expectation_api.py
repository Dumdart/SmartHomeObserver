from uuid import UUID, uuid4

from fastmcp import FastMCP
from fastmcp.tools import tool

from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.app.services.expectation_management_service import (
    ExpectationManagementService,
)
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.models.health import HealthExpectation
from topicgate.core.models.health import BrokerTarget
from topicgate.core.models.health.condition import (
    EqualCondition,
    InRangeCondition,
    OutSideCondition,
)
from topicgate.mcp.api.mcp_api import MCPApi
from topicgate.mcp.requests.expectation_requests import (
    CreateExpectationRequest,
    UpdateExpectationRequest,
    condition_model,
    definition,
    target_model,
)


class ExpectationAPI(MCPApi):
    def __init__(
        self,
        management: ExpectationManagementService,
        resolver: BrokerResolver,
        runtime: TopicGateRuntime,
    ) -> None:
        self._management = management
        self._resolver = resolver
        self._runtime = runtime

    def register(self, mcp: FastMCP, *, control_enabled: bool = False) -> None:
        mcp.add_tool(self.list_health_expectations)
        if control_enabled:
            mcp.add_tool(self.create_health_expectation)
            mcp.add_tool(self.update_health_expectation)
            mcp.add_tool(self.delete_health_expectation)

    @tool(annotations={"readOnlyHint": True})
    def list_health_expectations(
        self, broker: UUID | str, limit: int = 50, cursor: int = 0
    ) -> dict:
        """Read a bounded page of definitions without evaluating or persisting.

        Side effects: None. Required state: A saved profile.
        Identifiers: broker is a UUID or unique name; cursor is a zero-based offset.
        Failures: Invalid bounds or selectors. Restart pagination after edits.
        """
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(cursor) is not int
            or cursor < 0
        ):
            raise ValueError("limit must be 1..100 and cursor a nonnegative integer.")
        resolved = self._resolver.resolve(broker)
        items = sorted(
            self._management.list_expectations(resolved.id),
            key=lambda item: item.expectation_id.hex,
        )
        page = items[cursor : cursor + limit]
        return {
            "broker_id": resolved.id,
            "items": [definition(item) for item in page],
            "returned_count": len(page),
            "next_cursor": cursor + limit if cursor + limit < len(items) else None,
        }

    @tool()
    def create_health_expectation(
        self, broker: UUID | str, request: CreateExpectationRequest
    ) -> dict:
        """Create an expectation after a covering subscription exists.

        Side effects: Persists a definition. Required state: Saved broker and topic coverage.
        Identifiers: broker accepts UUID or unique name; returns a new expectation UUID.
        Failures: Invalid condition/target, missing coverage, lease or storage errors.
        Retry: Read definitions and compare before retrying an uncertain write.
        """
        with self._runtime.control_operation("create health expectation"):
            resolved = self._resolver.resolve(broker)
            target = target_model(request.target, resolved.id)
            item = HealthExpectation(
                uuid4(),
                1,
                request.enabled,
                request.severity,
                target,
                condition_model(request.condition, target),
                request.actions,
                request.name,
                request.description,
            )
            return definition(
                self._management.create_expectation(item, broker_id=resolved.id)
            )

    @tool()
    def update_health_expectation(
        self,
        broker: UUID | str,
        expectation_id: UUID,
        changes: UpdateExpectationRequest,
    ) -> dict:
        """Apply only explicit fields to a broker-scoped expectation.

        Side effects: Persists edits; behavior changes close the previous revision's episode.
        Required state: Saved expectation. Identifiers: UUID plus broker UUID or unique name.
        Failures: Wrong broker, invalid changes, missing coverage, lease or storage errors.
        """
        with self._runtime.control_operation("update health expectation"):
            resolved = self._resolver.resolve(broker)
            current = self._management.get_expectation(resolved.id, expectation_id)
            target = (
                current.target
                if changes.target is None
                else target_model(changes.target, resolved.id)
            )
            condition = (
                current.condition
                if changes.condition is None
                else condition_model(changes.condition, target)
            )
            values = (
                (condition.expected_value,)
                if isinstance(condition, EqualCondition)
                else condition.expected_values
                if isinstance(condition, (InRangeCondition, OutSideCondition))
                else ()
            )
            expected_type = str if isinstance(target, BrokerTarget) else bytes
            if any(type(value) is not expected_type for value in values):
                raise ValueError(
                    "Update the condition encoding when changing target kind."
                )
            item = self._management.edit_expectation(
                expectation_id,
                broker_id=resolved.id,
                is_enabled=changes.enabled,
                new_target=target,
                new_condition=condition,
                new_severity=changes.severity,
                new_actions=changes.actions,
                name=changes.name,
                description=changes.description,
            )
            return definition(item)

    @tool()
    def delete_health_expectation(
        self, broker: UUID | str, expectation_id: UUID
    ) -> dict:
        """Delete a broker-scoped definition while retaining historical failures.

        Side effects: Closes the active episode and deletes the definition.
        Required state: Saved expectation. Identifiers: expectation UUID and broker selector.
        Failures: Missing or wrong-broker ID, lease conflicts, storage errors.
        """
        with self._runtime.control_operation("delete health expectation"):
            resolved = self._resolver.resolve(broker)
            self._management.delete_expectation(expectation_id, broker_id=resolved.id)
            return {
                "broker_id": resolved.id,
                "expectation_id": expectation_id,
                "deleted": True,
            }
