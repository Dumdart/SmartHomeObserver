from collections.abc import Callable
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import shutil
import sys
from uuid import UUID

from sqlalchemy import text

from topicgate.app.models.mcp_setup import McpPreflightCheck, McpSetupInformation
from topicgate.app.services.broker_snapshot_service import BrokerSnapshotService
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.broker_profile_summary import BrokerProfileSummary
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.subscription import Subscription
from topicgate.infrastructure.credentials.credential_store import CredentialStore
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.migrations import EXPECTED_SCHEMA_REVISION


class McpSetupService:
    """Provide desktop MCP configuration and non-mutating diagnostics."""

    def __init__(
        self,
        runtime: TopicGateRuntime,
        snapshot_service: BrokerSnapshotService,
        database: DatabaseContext,
        credential_store: CredentialStore,
        data_path: Path,
        database_path: Path,
        *,
        broker_reader: Callable[[], tuple[BrokerProfileSummary, ...]] | None = None,
        active_broker_reader: Callable[[], BrokerProfileSummary] | None = None,
        subscriptions_reader: (
            Callable[[UUID], tuple[Subscription, ...]] | None
        ) = None,
    ) -> None:
        self._runtime = runtime
        self._snapshots = snapshot_service
        self._database = database
        self._credential_store = credential_store
        self._broker_reader = broker_reader
        self._active_broker_reader = active_broker_reader
        self._subscriptions_reader = subscriptions_reader
        executable = shutil.which("topicgate")
        if executable:
            command = str(Path(executable).resolve())
            prefix: tuple[str, ...] = ()
        else:
            command = str(Path(sys.executable).resolve())
            prefix = ("-m", "topicgate")
        try:
            installed_version = version("topicgate")
        except PackageNotFoundError:
            installed_version = "development"
        self.information = McpSetupInformation(
            version=installed_version,
            executable_path=Path(command),
            data_path=data_path.resolve(),
            database_path=database_path.resolve(),
            command=command,
            command_prefix_arguments=prefix,
        )

    def configuration(self, mode: str = "read-only") -> str:
        if mode not in {"read-only", "control"}:
            raise ValueError("MCP mode must be read-only or control.")
        server = {
            "type": "stdio",
            "command": self.information.command,
            "args": [
                *self.information.command_prefix_arguments,
                "--mode",
                mode,
            ],
            "env": {"TOPICGATE_DATA_DIR": str(self.information.data_path)},
        }
        rendered = json.dumps({"mcpServers": {"topicgate": server}}, indent=2)
        return rendered.replace(
            f'        "--mode",\n        "{mode}"',
            f'        "--mode", "{mode}"',
        )

    def preflight(self) -> tuple[McpPreflightCheck, ...]:
        checks = [self._database_check(), self._migration_check()]
        checks.append(self._credential_check())
        brokers = (
            self._runtime.list_brokers()
            if self._broker_reader is None
            else self._broker_reader()
        )
        checks.append(
            McpPreflightCheck(
                "Broker profiles",
                "pass" if brokers else "fail",
                f"{len(brokers)} configured profile(s)."
                if brokers
                else "Create a broker profile in TopicGate Desktop.",
            )
        )
        usable = sum(
            len(
                self._runtime.list_subscriptions(broker.id)
                if self._subscriptions_reader is None
                else self._subscriptions_reader(broker.id)
            )
            for broker in brokers
        )
        checks.append(
            McpPreflightCheck(
                "Subscriptions",
                "pass" if usable else "fail",
                f"{usable} usable subscription(s)."
                if usable
                else "Add at least one subscription in TopicGate Desktop.",
            )
        )
        try:
            active_broker = (
                self._runtime.active_broker
                if self._active_broker_reader is None
                else self._active_broker_reader()
            )
            self._snapshots.build_resolved_current(
                self._snapshot_broker(active_broker)
            )
        except Exception as error:
            checks.append(
                McpPreflightCheck(
                    "Snapshot service",
                    "fail",
                    f"Snapshot unavailable: {error}",
                )
            )
        else:
            checks.append(
                McpPreflightCheck(
                    "Snapshot service",
                    "pass",
                    "A local broker snapshot can be built while disconnected.",
                )
            )
        dashboard_available = all(
            importlib.util.find_spec(module) is not None
            for module in ("fastmcp", "prefab_ui")
        )
        checks.append(
            McpPreflightCheck(
                "Dashboard dependencies",
                "pass" if dashboard_available else "warning",
                "Optional dashboard dependencies are available."
                if dashboard_available
                else "Optional only: install TopicGate with the 'apps' extra for dashboards.",
            )
        )
        return tuple(checks)

    @staticmethod
    def _snapshot_broker(
        broker: BrokerSummary | BrokerProfileSummary,
    ) -> BrokerSummary:
        if isinstance(broker, BrokerSummary):
            return broker
        return BrokerSummary(
            broker.id,
            broker.name,
            MqttConfig("", broker.port, "", "", broker.use_tls),
            False,
        )

    def _database_check(self) -> McpPreflightCheck:
        try:
            with self._database.session() as session:
                session.execute(text("SELECT 1")).scalar_one()
                journal_mode = session.execute(
                    text("PRAGMA journal_mode")
                ).scalar_one()
                busy_timeout = session.execute(
                    text("PRAGMA busy_timeout")
                ).scalar_one()
        except Exception as error:
            return McpPreflightCheck(
                "Database accessibility",
                "fail",
                f"Cannot open the TopicGate database: {error}",
            )
        if str(journal_mode).lower() != "wal" or int(busy_timeout) < 5000:
            return McpPreflightCheck(
                "Database accessibility",
                "fail",
                "SQLite coordination is incomplete; restart TopicGate to enable WAL and busy timeout.",
            )
        return McpPreflightCheck(
            "Database accessibility",
            "pass",
            "SQLite is accessible with WAL and a 5-second busy timeout.",
        )

    def _migration_check(self) -> McpPreflightCheck:
        try:
            with self._database.session() as session:
                revision = session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
        except Exception as error:
            return McpPreflightCheck(
                "Database migrations",
                "fail",
                f"Migration state cannot be read: {error}",
            )
        if revision != EXPECTED_SCHEMA_REVISION:
            return McpPreflightCheck(
                "Database migrations",
                "fail",
                f"Expected schema {EXPECTED_SCHEMA_REVISION}, found {revision}; restart TopicGate to migrate.",
            )
        return McpPreflightCheck(
            "Database migrations",
            "pass",
            f"Schema {revision} is current.",
        )

    def _credential_check(self) -> McpPreflightCheck:
        required = ("get_password", "set_password", "delete_password")
        available = all(callable(getattr(self._credential_store, name, None)) for name in required)
        return McpPreflightCheck(
            "Credential store",
            "pass" if available else "fail",
            "Operating-system credential store interface is available."
            if available
            else "Configure a supported keyring backend before storing broker credentials.",
        )
