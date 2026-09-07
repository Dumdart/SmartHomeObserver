from uuid import UUID

from fastmcp import FastMCP
from fastmcp.tools import tool

from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.models.subscription import Subscription
from topicgate.mcp.api.mcp_api import MCPApi


class SubscriptionAPI(MCPApi):
    def __init__(
        self,
        runtime: TopicGateRuntime,
        resolver: BrokerResolver,
    ):
        self._runtime = runtime
        self._resolver = resolver

    def register(self, mcp: FastMCP, *, control_enabled: bool = False) -> None:
        mcp.add_tool(self.list_subscriptions)
        if control_enabled:
            mcp.add_tool(self.add_subscription)
            mcp.add_tool(self.update_subscription)
            mcp.add_tool(self.remove_subscription)

    @tool(annotations={"readOnlyHint": True})
    def list_subscriptions(
        self,
        broker_id: UUID | str,
    ) -> tuple[Subscription, ...]:
        """List persisted subscriptions for a selected broker.

        Side effects: None; this does not activate or connect a broker.
        Required state: The broker profile must already exist locally.
        Identifiers: broker_id accepts a UUID or unique case-insensitive name.
        Failures: Fails for unknown or ambiguous profiles or persistence errors.
        """
        resolved = self._resolver.resolve(broker_id)
        return self._runtime.list_subscriptions(resolved.id)

    @tool(annotations={"openWorldHint": True})
    async def add_subscription(
        self,
        broker_id: UUID | str,
        topic_filter: str,
        qos: int = 1,
        retain_as_published: bool = False,
        retain_handling: int = 0,
    ) -> None:
        """Persist and apply an MQTT subscription for a selected broker.

        Side effects: Changes local subscription state and may subscribe over MQTT.
        Required state: The selected profile must exist and already be active.
        Identifiers: broker_id accepts a UUID or unique case-insensitive name;
        topic_filter accepts MQTT wildcards.
        Failures: Fails for invalid brokers, filters, QoS/retain values, duplicate
        subscriptions, persistence errors, or MQTT subscribe errors.
        """
        resolved = self._resolver.resolve(broker_id)
        await self._runtime.add_subscription(
            resolved.id,
            Subscription(
                topic_filter=topic_filter,
                qos=qos,
                retain_as_published=retain_as_published,
                retain_handling=retain_handling,
            ),
        )

    @tool(annotations={"openWorldHint": True})
    async def update_subscription(
        self,
        broker_id: UUID | str,
        original_filter: str,
        topic_filter: str,
        qos: int = 1,
        retain_as_published: bool = False,
        retain_handling: int = 0,
    ) -> None:
        """Replace a persisted MQTT subscription for a selected broker.

        Side effects: Changes local state and may unsubscribe/subscribe over MQTT.
        Required state: The selected profile must be active, and original_filter
        must identify an existing subscription.
        Identifiers: broker_id accepts a UUID or unique case-insensitive name;
        both filter arguments use MQTT subscription-filter syntax.
        Failures: Fails for invalid brokers or values, a missing original filter,
        persistence errors, or MQTT subscription errors.
        """
        resolved = self._resolver.resolve(broker_id)
        await self._runtime.update_subscription(
            resolved.id,
            original_filter,
            Subscription(
                topic_filter=topic_filter,
                qos=qos,
                retain_as_published=retain_as_published,
                retain_handling=retain_handling,
            ),
        )

    @tool(
        annotations={
            "destructiveHint": True,
            "openWorldHint": True,
        }
    )
    async def remove_subscription(
        self,
        broker_id: UUID | str,
        topic_filter: str,
    ) -> None:
        """Remove a persisted MQTT subscription from a selected broker.

        Side effects: Deletes local state and may unsubscribe from MQTT.
        Required state: The selected profile must be active, and topic_filter
        must exactly match an existing subscription.
        Identifiers: broker_id accepts a UUID or unique case-insensitive name;
        topic_filter is the exact persisted MQTT subscription filter.
        Failures: Fails for invalid brokers, unknown filters, persistence errors,
        or MQTT unsubscribe errors.
        """
        resolved = self._resolver.resolve(broker_id)
        subscription = next(
            (
                item
                for item in self._runtime.list_subscriptions(resolved.id)
                if item.topic_filter == topic_filter
            ),
            None,
        )
        if subscription is None:
            raise ValueError(f"Unknown subscription filter: {topic_filter}")
        await self._runtime.remove_subscription(resolved.id, subscription)
