import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from topicgate.app.models.integration import IntegrationSpec, McpMode
from topicgate.infrastructure.integrations.cursor import CursorIntegration


class FakeCursor:
    def __init__(self) -> None:
        self.marketplace = False
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, arguments):
        command = tuple(arguments)
        self.commands.append(command)
        operation = command[1:]
        stdout = ""
        if operation == (
            "plugin",
            "marketplace",
            "list",
            "--format",
            "json",
        ):
            marketplaces = []
            if self.marketplace:
                marketplaces.append(
                    {
                        "name": "topicgate",
                        "gitUrl": "https://github.com/Dumdart/TopicGate",
                    }
                )
            stdout = json.dumps(marketplaces)
        elif operation[:3] == ("plugin", "marketplace", "add"):
            self.marketplace = True
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def desired() -> IntegrationSpec:
    return IntegrationSpec(
        platform="cursor",
        mode=McpMode.CONTROL,
        package_version="1.5.2",
        server_name="topicgate",
        command="C:/TopicGate/topicgate.exe",
        arguments=("--mode", "control"),
        environment=(("TOPICGATE_DATA_DIR", "C:/TopicGate/data"),),
    )


def test_install_preserves_other_servers_and_converges_topicgate(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "other"},
                    "topicgate-control": {
                        "command": "topicgate",
                        "args": ["--mode", "control"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    host = FakeCursor()
    integration = CursorIntegration(
        executable="cursor-agent",
        runner=host,
        config_path=config_path,
    )

    result = integration.apply(integration.plan(desired()))

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert host.marketplace
    assert "other" in payload["mcpServers"]
    assert "topicgate-control" not in payload["mcpServers"]
    assert payload["mcpServers"]["topicgate"]["args"] == ["--mode", "control"]
    assert result.state.servers[0].mode is McpMode.CONTROL
    assert not integration.plan(desired()).actions


def test_uninstall_removes_only_topicgate_mcp_entries(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "other"},
                    "topicgate": {"command": "topicgate"},
                }
            }
        ),
        encoding="utf-8",
    )
    host = FakeCursor()
    host.marketplace = True
    integration = CursorIntegration(
        executable="cursor-agent",
        runner=host,
        config_path=config_path,
    )

    result = integration.remove()

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert result.changed
    assert payload["mcpServers"] == {"other": {"command": "other"}}
    assert host.marketplace


@pytest.mark.skipif(os.name == "nt", reason="Unix permission semantics")
def test_install_preserves_existing_configuration_permissions(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text('{"mcpServers": {}}', encoding="utf-8")
    config_path.chmod(0o600)
    integration = CursorIntegration(
        executable="cursor-agent",
        runner=FakeCursor(),
        config_path=config_path,
    )

    integration._configure_server(desired())

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
