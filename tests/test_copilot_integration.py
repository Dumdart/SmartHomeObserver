import json
import subprocess

from topicgate.app.models.integration import IntegrationSpec, McpMode
from topicgate.infrastructure.integrations.copilot import CopilotIntegration


class FakeCopilot:
    def __init__(self) -> None:
        self.marketplace = False
        self.plugin_version: str | None = None
        self.user_servers: dict[str, dict] = {}
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, arguments):
        command = tuple(arguments)
        self.commands.append(command)
        operation = command[1:]
        stdout = ""
        if operation == ("plugin", "marketplace", "list"):
            stdout = "Registered marketplaces:\n"
            if self.marketplace:
                stdout += "  • topicgate (GitHub: Dumdart/TopicGate)\n"
        elif operation == ("plugin", "list"):
            if self.plugin_version:
                stdout = (
                    "Installed plugins:\n"
                    f"  • topicgate@topicgate (v{self.plugin_version})\n"
                )
        elif operation == ("mcp", "list", "--json"):
            servers = dict(self.user_servers)
            if self.plugin_version and "topicgate" not in servers:
                servers["topicgate"] = {
                    "type": "stdio",
                    "command": "topicgate",
                    "args": ["--mode", "read-only"],
                    "source": "plugin",
                }
            stdout = json.dumps({"mcpServers": servers})
        elif operation[:3] == ("plugin", "marketplace", "add"):
            self.marketplace = True
        elif operation[:3] == ("plugin", "marketplace", "update"):
            pass
        elif operation[:2] in (("plugin", "install"), ("plugin", "update")):
            self.plugin_version = "1.5.2"
        elif operation[:2] == ("plugin", "uninstall"):
            self.plugin_version = None
        elif operation[:2] == ("mcp", "remove"):
            self.user_servers.pop(operation[2], None)
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
            self.user_servers[name] = {
                "type": "stdio",
                "command": operation[separator + 1],
                "args": list(operation[separator + 2 :]),
                "env": environment,
                "source": "user",
            }
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def desired(mode: McpMode = McpMode.CONTROL) -> IntegrationSpec:
    return IntegrationSpec(
        platform="copilot",
        mode=mode,
        package_version="1.5.2",
        server_name="topicgate",
        command="C:/TopicGate/topicgate.exe",
        arguments=("--mode", mode.value),
        environment=(("TOPICGATE_DATA_DIR", "C:/TopicGate/data"),),
    )


def test_read_only_reuses_the_plugin_server_and_updates_the_plugin() -> None:
    host = FakeCopilot()
    host.marketplace = True
    host.plugin_version = "1.4.0"
    integration = CopilotIntegration(executable="copilot", runner=host)

    result = integration.apply(integration.plan(desired(McpMode.READ_ONLY)))

    assert result.state.plugin_version == "1.5.2"
    assert result.state.servers[0].source == "plugin"
    assert not integration.plan(desired(McpMode.READ_ONLY)).actions
    assert ("copilot", "plugin", "update", "topicgate@topicgate") in host.commands
    assert not any(command[1:3] == ("mcp", "add") for command in host.commands)


def test_control_adds_one_user_override_for_the_plugin_server() -> None:
    host = FakeCopilot()
    host.marketplace = True
    host.plugin_version = "1.5.2"
    integration = CopilotIntegration(executable="copilot", runner=host)

    result = integration.apply(integration.plan(desired()))

    assert len(result.state.servers) == 1
    assert result.state.servers[0].source == "user"
    assert result.state.servers[0].mode is McpMode.CONTROL
    assert not integration.plan(desired()).actions


def test_uninstall_removes_user_override_and_plugin() -> None:
    host = FakeCopilot()
    host.marketplace = True
    host.plugin_version = "1.5.2"
    host.user_servers["topicgate"] = {
        "command": "topicgate",
        "args": ["--mode", "control"],
        "source": "user",
    }
    integration = CopilotIntegration(executable="copilot", runner=host)

    result = integration.remove()

    assert result.changed
    assert not result.state.plugin_installed
    assert not result.state.servers
    assert host.marketplace
