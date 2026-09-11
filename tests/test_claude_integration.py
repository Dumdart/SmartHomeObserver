import json
from pathlib import Path
import subprocess

from topicgate.app.models.integration import IntegrationSpec, McpMode
from topicgate.infrastructure.integrations.claude import ClaudeIntegration


class FakeClaude:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.marketplace = False
        self.plugin_version: str | None = None
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, arguments):
        command = tuple(arguments)
        self.commands.append(command)
        operation = command[1:]
        stdout = ""
        if operation == ("plugin", "marketplace", "list", "--json"):
            stdout = json.dumps(
                [{"name": "topicgate"}] if self.marketplace else []
            )
        elif operation == ("plugin", "list", "--json"):
            stdout = json.dumps(
                [
                    {
                        "name": "topicgate",
                        "marketplace": "topicgate",
                        "version": self.plugin_version,
                    }
                ]
                if self.plugin_version
                else []
            )
        elif operation[:3] == ("plugin", "marketplace", "add"):
            self.marketplace = True
        elif operation[:3] == ("plugin", "marketplace", "update"):
            pass
        elif operation[:2] in (("plugin", "install"), ("plugin", "update")):
            self.plugin_version = "1.5.2"
        elif operation[:2] == ("plugin", "uninstall"):
            self.plugin_version = None
        elif operation[:2] == ("mcp", "remove"):
            payload = self._configuration()
            payload.setdefault("mcpServers", {}).pop(operation[-1], None)
            self._write(payload)
        elif operation[:2] == ("mcp", "add"):
            separator = operation.index("--")
            name = operation[separator - 1]
            environment = {}
            index = 2
            while index < separator - 1:
                if operation[index] == "--env":
                    key, value = operation[index + 1].split("=", 1)
                    environment[key] = value
                    index += 2
                else:
                    index += 1
            payload = self._configuration()
            payload.setdefault("mcpServers", {})[name] = {
                "type": "stdio",
                "command": operation[separator + 1],
                "args": list(operation[separator + 2 :]),
                "env": environment,
            }
            self._write(payload)
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    def _configuration(self) -> dict:
        if not self.config_path.exists():
            return {}
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _write(self, payload: dict) -> None:
        self.config_path.write_text(json.dumps(payload), encoding="utf-8")


def desired(mode: McpMode = McpMode.CONTROL) -> IntegrationSpec:
    return IntegrationSpec(
        platform="claude",
        mode=mode,
        package_version="1.5.2",
        server_name="topicgate",
        command="C:/TopicGate/topicgate.exe",
        arguments=("--mode", mode.value),
        environment=(("TOPICGATE_DATA_DIR", "C:/TopicGate/data"),),
    )


def test_read_only_reuses_plugin_server_and_updates_plugin(tmp_path: Path) -> None:
    config_path = tmp_path / ".claude.json"
    host = FakeClaude(config_path)
    host.marketplace = True
    host.plugin_version = "1.4.0"
    integration = ClaudeIntegration(
        executable="claude",
        runner=host,
        config_path=config_path,
    )

    result = integration.apply(integration.plan(desired(McpMode.READ_ONLY)))

    assert result.state.plugin_version == "1.5.2"
    assert result.state.servers[0].source == "plugin"
    assert not config_path.exists()
    assert not integration.plan(desired(McpMode.READ_ONLY)).actions


def test_control_creates_one_user_scoped_override(tmp_path: Path) -> None:
    config_path = tmp_path / ".claude.json"
    host = FakeClaude(config_path)
    host.marketplace = True
    host.plugin_version = "1.5.2"
    integration = ClaudeIntegration(
        executable="claude",
        runner=host,
        config_path=config_path,
    )

    result = integration.apply(integration.plan(desired()))

    assert len(result.state.servers) == 1
    assert result.state.servers[0].source == "user"
    assert result.state.servers[0].mode is McpMode.CONTROL
    assert not integration.plan(desired()).actions
    assert any(
        command[1:7] == (
            "mcp",
            "add",
            "--transport",
            "stdio",
            "--scope",
            "user",
        )
        for command in host.commands
    )


def test_uninstall_removes_user_override_and_plugin(tmp_path: Path) -> None:
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "topicgate": {
                        "command": "topicgate",
                        "args": ["--mode", "control"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    host = FakeClaude(config_path)
    host.marketplace = True
    host.plugin_version = "1.5.2"
    integration = ClaudeIntegration(
        executable="claude",
        runner=host,
        config_path=config_path,
    )

    result = integration.remove()

    assert result.changed
    assert not result.state.plugin_installed
    assert not result.state.servers
    assert host.marketplace
