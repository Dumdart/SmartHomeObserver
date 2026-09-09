import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
import sys

from fastmcp import FastMCP

from topicgate.app.app_dependencies import AppDependencies
from topicgate.app.services.broker_inspection_service import BrokerInspectionService
from topicgate.app.services.service_container import ServiceContainer
from topicgate.mcp.api.broker_api import BrokerAPI
from topicgate.mcp.api.connection_api import ConnectionAPI
from topicgate.mcp.api.dashboard_api import DashboardAPI
from topicgate.mcp.api.health_api import HealthAPI
from topicgate.mcp.api.expectation_api import ExpectationAPI
from topicgate.mcp.api.mcp_api import McpApiContainer
from topicgate.mcp.api.publish_api import PublishAPI
from topicgate.mcp.api.snapshot_api import SnapshotAPI
from topicgate.mcp.api.subscription_api import SubscriptionAPI
from topicgate.mcp.api.support_bundle_api import SupportBundleAPI
from topicgate.mcp.api.topic_api import TopicAPI
from topicgate.mcp.capabilities import McpMode
from topicgate.mcp.instructions import UNTRUSTED_MQTT_DATA_INSTRUCTIONS
from topicgate.mcp.middleware import ErrorHandlingMiddleware, LoggingMiddleware

logger = logging.getLogger(__name__)

_SNAPSHOT_INSTRUCTIONS = """Use inspect_broker as the primary read-only broker
inspection tool. It combines profile identity, connection health, subscriptions,
cache usage, and, when requested, a bounded snapshot. get_broker_snapshot,
list_topics, and get_topic_state remain available as legacy compatibility tools.

Snapshots return TopicGate's latest observed state: the last values TopicGate
received and retained in memory or persistence. They are not authoritative broker
history or proof that a value is still current; received_at is when TopicGate saw
the message.

Snapshot results can be stale or partial. Without max_age_seconds, old cached values
may be returned. With max_age_seconds, stale values are omitted and counted in
freshness and result metadata. Always inspect completeness.is_complete,
completeness.limitations, freshness, results, and payload truncation fields. Empty,
limited, or disconnected snapshots can be valid.

Broker selectors accept a UUID or a unique profile name. Names are trimmed and
matched case-insensitively. Unknown names fail; ambiguous names fail rather than
selecting arbitrarily. Call list_brokers and retry with the broker UUID when needed.

list_health_expectations reads bounded definitions without evaluating or persisting.
query_failure_history is passive and available in every mode. Its response is
bounded; inspect returned_count and next_cursor.

get_support_bundle returns redacted, bounded diagnostics in JSON or Markdown in
every mode. It never writes a host file and categorically excludes MQTT payloads.
Always keep the accompanying redaction manifest and report warnings, omissions,
and truncation metadata when sharing the result."""

_SNAPSHOT_INSTRUCTIONS += "\n\n" + UNTRUSTED_MQTT_DATA_INSTRUCTIONS
_SNAPSHOT_INSTRUCTIONS += """\n\nget_topic_history passively reads append-only
TopicGate-observed event history, not authoritative broker history. Recording is
opt-in per broker. Disabled recording still allows reading retained events.
Use the returned opaque next_cursor with the same broker, filter and time bounds;
even an empty page can have a continuation. A new query without a cursor starts
a new committed snapshot. Inspect recording counters, retention, limitations,
and storage/rendering truncation before drawing completeness conclusions."""

READ_ONLY_SERVER_INSTRUCTIONS = """This server is running in read-only mode. Only
passive inspection tools are available; MQTT activation, connection control,
subscription mutation, observation refresh, dashboard broker switching, and
publishing are disabled.

""" + _SNAPSHOT_INSTRUCTIONS

CONTROL_SERVER_INSTRUCTIONS = """This server is running in control mode. MQTT
state-changing tools are enabled; use them only when their documented side effects
are intended.

""" + _SNAPSHOT_INSTRUCTIONS + """

Use observe_broker_snapshot only when activation, reconnection, waiting, message
receipt, and persistence are intended. get_health_report runs a fresh local
expectation evaluation and is available only in control mode because evaluation
may persist health transitions and failure episodes. Its response is bounded;
inspect returned_count and omitted_count.

For authorized provisioning: create_broker, activate once, add subscriptions, create
expectations, then wait_for_broker_health once. Reuse authorization and returned UUIDs.
Use inspect_broker without snapshots for configuration comparisons. Creation supports
anonymous profiles; needs_credentials requires Desktop configuration before activation.
No password/credential_ref input is supported. Subscription mutations require the
selected broker active. Do not publish to manufacture healthy evidence.

wait_for_broker_health requires an already active connected broker and enabled rules.
It never reconnects, separates outcome from domain health, and reports full-scope
completeness and omitted findings. Scope subset success explicitly. Zero rules,
incomplete evidence, old healthy state or zero failures do not prove health.
Expected topic payloads use utf8/base64 bytes; broker status uses text. Resolve
names directly; list only for discovery/ambiguity. Keep failed/unknown findings first.
After uncertain writes, compare exact persisted settings/definitions before retrying.
Leave the selected broker active and explain switching once."""

# Check compatibility for integrations importing the original instruction constant.
SERVER_INSTRUCTIONS = READ_ONLY_SERVER_INSTRUCTIONS


def server_instructions(mode: McpMode) -> str:
    if mode.control_enabled:
        return CONTROL_SERVER_INSTRUCTIONS
    return READ_ONLY_SERVER_INSTRUCTIONS


class Server:
    def __init__(self, mode: McpMode = McpMode.READ_ONLY):
        self.mode = mode
        self.mcp = FastMCP(
            name="topicgate",
            instructions=server_instructions(mode),
            lifespan=self._lifespan,
            middleware=[ErrorHandlingMiddleware(), LoggingMiddleware()],
            mask_error_details=True,
        )

        self.dependencies = AppDependencies(control_owner="mcp")
        self.services = ServiceContainer(self.dependencies)

        runtime = self.dependencies.runtime
        self.resolver = self.dependencies.broker_resolver
        self.inspection_service = BrokerInspectionService(
            runtime,
            self.dependencies.snapshot_service,
            self.resolver,
        )

        self.mcp_container = McpApiContainer(
            [
                BrokerAPI(runtime, self.resolver, self.inspection_service),
                ConnectionAPI(runtime, self.resolver),
                PublishAPI(runtime, self.resolver),
                SubscriptionAPI(runtime, self.resolver),
                TopicAPI(runtime, self.resolver),
                SnapshotAPI(self.dependencies.snapshot_service),
                HealthAPI(
                    self.dependencies.health_query_service,
                    self.resolver,
                    self.dependencies.health_wait_service,
                ),
                SupportBundleAPI(self.dependencies.support_bundle_exporter),
                ExpectationAPI(
                    self.dependencies.expectation_management_service,
                    self.resolver,
                    runtime,
                ),
                DashboardAPI(runtime, self.dependencies.snapshot_service),
            ],
            control_enabled=mode.control_enabled,
        )
        self.mcp_container.register(self.mcp)

    def run(self) -> None:
        self.mcp.run()

    @asynccontextmanager
    async def _lifespan(self, _server: FastMCP) -> AsyncIterator[None]:
        startup_failed = False
        try:
            try:
                await self.services.start_services()
            except ConnectionError as error:
                startup_failed = True
                logger.warning(
                    "Initial MQTT connection failed; MCP started disconnected: %s",
                    error,
                )
            yield
        finally:
            try:
                await self.services.stop_services()
            finally:
                if startup_failed:
                    await self.dependencies.runtime.stop()


def parse_mode(argv: list[str] | None = None) -> McpMode:
    parser = argparse.ArgumentParser(description="Run the TopicGate MCP server.")
    parser.add_argument(
        "--mode",
        choices=tuple(McpMode),
        default=McpMode.READ_ONLY,
        type=McpMode,
        help=(
            "MCP capability mode. Defaults to read-only; control explicitly enables "
            "MQTT mutations and publishing."
        ),
    )
    return parser.parse_args(argv).mode


def run(argv: list[str] | None = None) -> int:
    server = Server(parse_mode(argv))

    try:
        server.run()
    except Exception as error:
        print(error, file=sys.stderr)
        return 1

    return 0
