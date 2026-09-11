from pathlib import Path

from topicgate.app.services.integration_service import IntegrationService
from topicgate.app.services.mcp_setup_service import McpSetupService
from topicgate.paths import prepare_database_path


class IntegrationDependencies:
    """Build integration-only dependencies without opening the TopicGate DB."""

    def __init__(self, data_dir: Path | None = None) -> None:
        database_path = prepare_database_path(data_dir)
        information = McpSetupService.resolve_information(
            database_path.parent,
            database_path,
        )
        self.integration_service = IntegrationService(information)
