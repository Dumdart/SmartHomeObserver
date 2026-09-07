from uuid import UUID

from fastmcp import FastMCP
from fastmcp.tools import tool

from topicgate.app.models.broker_inspection import BrokerInspection
from topicgate.app.services.broker_inspection_service import BrokerInspectionService
from topicgate.app.services.broker_resolver import BrokerResolver
from topicgate.app.services.broker_snapshot_service import DEFAULT_SNAPSHOT_RESULT_LIMIT
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.payload_limits import MAX_RENDERED_PAYLOAD_BYTES
from topicgate.mcp.api.mcp_api import MCPApi
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.mcp.requests.broker_creation import CreateBrokerRequest


class BrokerAPI(MCPApi):
    def __init__(
        self,
        runtime: TopicGateRuntime,
        resolver: BrokerResolver,
        inspection_service: BrokerInspectionService,
    ):
        self._runtime = runtime
        self._resolver = resolver
        self._inspection_service = inspection_service

    def register(self, mcp: FastMCP, *, control_enabled: bool = False) -> None:
        mcp.add_tool(self.list_brokers)
        mcp.add_tool(self.inspect_broker)
        if control_enabled:
            mcp.add_tool(self.create_broker)
            mcp.add_tool(self.activate_broker)

    @tool()
    def create_broker(self, request: CreateBrokerRequest) -> dict:
        """Save or reuse an exact matching broker profile without connecting.

        Side effects: Persists a profile and initializes its runtime repository.
        Required state: Local storage. Passwords and credential references are not accepted.
        Identifiers: Normalized unique name; returns the persistent broker UUID.
        Failures: Conflicting name/settings, validation, lease, or storage errors.
        A username without stored credentials returns needs_credentials; configure
        this UUID in Desktop before activation. Anonymous creation is supported.
        """
        with self._runtime.control_operation("create or reuse broker profile"):
            matches = [
                item
                for item in self._runtime.list_brokers()
                if item.name.strip().casefold() == request.name.casefold()
            ]
            if len(matches) > 1:
                raise ValueError(
                    "Ambiguous profile name; resolve existing profiles by UUID."
                )
            reused = bool(matches)
            if matches:
                summary = matches[0]
                config = summary.config
                if (
                    config.host.casefold(),
                    config.port,
                    config.username,
                    config.use_tls,
                ) != (request.host, request.port, request.username, request.use_tls):
                    raise ValueError(
                        "Profile name conflicts with different settings; no changes made."
                    )
            else:
                summary = self._runtime.create_broker(
                    request.name,
                    MqttConfig(
                        request.host,
                        request.port,
                        request.username,
                        "",
                        request.use_tls,
                    ),
                )
            needs_credentials = (
                bool(summary.config.username) and not summary.password_configured
            )
            return {
                "broker_id": summary.id,
                "name": summary.name,
                "host": summary.config.host,
                "port": summary.config.port,
                "username": summary.config.username,
                "use_tls": summary.config.use_tls,
                "reused": reused,
                "password_configured": summary.password_configured,
                "status": "needs_credentials" if needs_credentials else "saved",
                "next_step": (
                    "Configure credentials for this UUID in TopicGate Desktop, then activate."
                    if needs_credentials
                    else "Activate this broker UUID explicitly."
                ),
                "connected": self._runtime.get_connection_status(summary.id)
                == ConnectionStatus.CONNECTED,
                "connection_side_effects": [],
            }

    @tool(annotations={"readOnlyHint": True})
    def list_brokers(self) -> tuple[BrokerSummary, ...]:
        """List persisted broker profiles.

        Side effects: None; this does not activate or connect a broker.
        Required state: The local TopicGate database must be available.
        Identifiers: Returned IDs are broker UUIDs; names are profile labels.
        Failures: Fails when persisted broker profiles cannot be read.
        """
        return self._runtime.list_brokers()

    @tool(annotations={"readOnlyHint": True})
    def inspect_broker(
        self,
        broker: UUID | str,
        include_snapshot: bool = False,
        snapshot_limit: int = DEFAULT_SNAPSHOT_RESULT_LIMIT,
        payload_limit_bytes: int = MAX_RENDERED_PAYLOAD_BYTES,
    ) -> BrokerInspection:
        """Inspect a broker profile with its passive runtime and stored state.

        Side effects: None; this does not activate, connect, wait, or refresh.
        Required state: The broker profile and local database must be available.
        Identifiers: broker accepts a UUID or unique case-insensitive name.
        Failures: Fails for invalid selectors or snapshot bounds and read errors.
        """
        return self._inspection_service.inspect(
            broker,
            include_snapshot=include_snapshot,
            snapshot_limit=snapshot_limit,
            payload_limit_bytes=payload_limit_bytes,
        )

    @tool()
    async def activate_broker(self, broker_id: UUID | str) -> BrokerSummary:
        """Make a broker profile active and connect it.

        Side effects: Disconnects the current client, changes the active profile,
        and connects to the selected MQTT broker.
        Required state: The selected profile and its credentials must be usable.
        Identifiers: broker_id accepts a UUID or unique case-insensitive name.
        Failures: Fails for unknown or ambiguous profiles and connection errors.
        """
        resolved = self._resolver.resolve(broker_id)
        return await self._runtime.activate_broker(resolved.id)
