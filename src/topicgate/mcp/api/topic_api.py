from base64 import b64encode
from datetime import datetime
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.tools import tool

from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.app.models.topic_history import TopicHistoryResult
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.models.mqtt_observation import MqttObservation
from topicgate.mcp.api.mcp_api import MCPApi
from topicgate.mcp.models import TopicStateResult


class TopicAPI(MCPApi):
    def __init__(
        self,
        runtime: TopicGateRuntime,
        resolver: BrokerResolver,
    ):
        self._runtime = runtime
        self._resolver = resolver

    def register(self, mcp: FastMCP, *, control_enabled: bool = False) -> None:
        mcp.add_tool(self.list_topics)
        mcp.add_tool(self.get_topic_state)
        mcp.add_tool(self.get_topic_history)

    @tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def get_topic_history(
        self, broker: UUID | str, topic_filter: str,
        after: datetime | None = None, before: datetime | None = None,
        cursor: str | None = None, limit: int = 100,
    ) -> TopicHistoryResult:
        """Read a bounded page of TopicGate-observed event history.

        Side effects: None; does not connect, activate, flush, or enable recording.
        Required state: A saved broker; recording is opt-in. Existing retained
        events remain readable while disconnected or recording is disabled.
        Identifiers: broker accepts UUID or unique name; topic_filter uses MQTT
        wildcards. after/before are exclusive timezone-aware receive-time bounds.
        Failures: Unknown/ambiguous broker, invalid filter/time/limit, malformed
        or mismatched cursor, and storage read errors. limit is 1–500.
        Follow next_cursor even after an empty page. Refresh without a cursor
        starts a new committed snapshot. Inspect limitations, recording counters,
        retention, and truncation; this is not authoritative broker history.
        """
        resolved = self._resolver.resolve(broker)
        return self._runtime.get_topic_history(
            resolved.id, topic_filter, after=after, before=before, cursor=cursor, limit=limit,
        )

    @tool(
        annotations={"readOnlyHint": True, "openWorldHint": True},
        meta={"legacy": True},
    )
    def list_topics(
        self,
        broker: UUID | str | None = None,
    ) -> tuple[str, ...]:
        """List current topic names from the unified repository-backed view.

        Side effects: None; this does not activate, connect, or refresh a broker.
        Required state: Omit broker only when an active profile exists; results are
        limited to state already observed or restored by TopicGate.
        Identifiers: broker accepts a UUID or unique case-insensitive name.
        Failures: Fails for no active profile or unknown or ambiguous profiles.
        """
        resolved = self._resolver.resolve_or_active(broker)
        return self._runtime.list_topics(resolved.id)

    @tool(
        annotations={"readOnlyHint": True, "openWorldHint": True},
        meta={"legacy": True},
    )
    def get_topic_state(
        self,
        broker_id: UUID | str,
        topic: str,
    ) -> TopicStateResult | None:
        """Read one topic from the unified repository-backed current view.

        Side effects: None; this does not activate, connect, or refresh a broker.
        Required state: The profile must exist; an unobserved topic returns null.
        Identifiers: broker_id accepts a UUID or unique case-insensitive name;
        topic is an exact MQTT topic, not a wildcard filter.
        Failures: Fails for unknown or ambiguous profiles or state read errors.
        """
        resolved = self._resolver.resolve(broker_id)
        state = self._runtime.get_topic_state(resolved.id, topic)
        return None if state is None else self._to_result(state)

    @staticmethod
    def _to_result(state: MqttObservation) -> TopicStateResult:
        try:
            payload_text = state.payload.decode("utf-8")
        except UnicodeDecodeError:
            payload_text = None
        return TopicStateResult(
            name=state.name,
            topic=state.topic,
            payload_text=payload_text,
            payload_base64=b64encode(state.payload).decode("ascii"),
            qos=state.qos,
            retain=state.retain,
            received_at=state.recieved_at,
            message_count=state.message_count,
            payload_size=state.payload_size or len(state.payload),
        )
