from pathlib import Path
from unittest.mock import patch

from topicgate.app.integration_dependencies import IntegrationDependencies
from topicgate.app.models.mcp_setup import McpSetupInformation


def test_integration_dependencies_resolve_metadata_without_database_startup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "topicgate.db"
    information = McpSetupInformation(
        version="1.5.2",
        executable_path=Path("topicgate"),
        data_path=tmp_path,
        database_path=database_path,
        command="topicgate",
        command_prefix_arguments=(),
    )

    with patch(
        "topicgate.app.integration_dependencies.McpSetupService.resolve_information",
        return_value=information,
    ) as resolve_information:
        dependencies = IntegrationDependencies(tmp_path)

    resolve_information.assert_called_once_with(tmp_path, database_path)
    assert dependencies.integration_service._information is information
