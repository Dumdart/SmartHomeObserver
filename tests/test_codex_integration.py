import json
from pathlib import Path
import subprocess

from topicgate.app.models.integration import (
    IntegrationActionKind,
    IntegrationSpec,
    McpMode,
)
from topicgate.infrastructure.integrations.codex import CodexIntegration


class FakeCodex:
    def __init__(self) -> None:
        self.marketplace = False
        self.plugin_version: str | None = None
        self.plugin_enabled = True
        self.servers: dict[str, dict] = {}
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, arguments):
        command = tuple(arguments)
        self.commands.append(command)
        operation = command[1:]
        stdout = ""
        if operation == ("plugin", "marketplace", "list"):
            stdout = "MARKETPLACE ROOT\n"
            if self.marketplace:
                stdout += "topicgate C:/marketplaces/topicgate\n"
        elif operation == ("plugin", "list"):
            if self.plugin_version:
                stdout = (
                    "PLUGIN STATUS VERSION SOURCE\n"
                    "topicgate@topicgate installed, "
                    f"{'enabled' if self.plugin_enabled else 'disabled'} "
                    f"{self.plugin_version} C:/topicgate\n"
                )
        elif operation == ("mcp", "list", "--json"):
            stdout = json.dumps(list(self.servers.values()))
        elif operation[:3] == ("plugin", "marketplace", "add"):
            self.marketplace = True
        elif operation[:3] == ("plugin", "marketplace", "upgrade"):
            pass
        elif operation[:2] == ("plugin", "add"):
            self.plugin_version = "1.5.2"
            self.plugin_enabled = True
        elif operation[:2] == ("plugin", "remove"):
            self.plugin_version = None
        elif operation[:2] == ("mcp", "remove"):
            self.servers.pop(operation[2], None)
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
            self.servers[name] = {
                "name": name,
                "transport": {
                    "type": "stdio",
                    "command": operation[separator + 1],
                    "args": list(operation[separator + 2 :]),
                    "env": environment,
                },
            }
        else:
            raise AssertionError(f"Unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout, "")


def desired(mode: McpMode = McpMode.CONTROL) -> IntegrationSpec:
    return IntegrationSpec(
        platform="codex",
        mode=mode,
        package_version="1.5.2",
        server_name="topicgate",
        command=str(Path("C:/TopicGate/topicgate.exe")),
        arguments=("--mode", mode.value),
        environment=(("TOPICGATE_DATA_DIR", "C:/TopicGate/data"),),
    )


def test_plan_repairs_versions_and_converges_duplicate_servers() -> None:
    host = FakeCodex()
    host.marketplace = True
    host.plugin_version = "1.4.0"
    host.servers = {
        "topicgate": {
            "name": "topicgate",
            "transport": {
                "type": "stdio",
                "command": "topicgate",
                "args": ["--mode", "read-only"],
            },
        },
        "topicgate-control": {
            "name": "topicgate-control",
            "transport": {
                "type": "stdio",
                "command": "topicgate",
                "args": ["--mode", "control"],
            },
        },
    }
    integration = CodexIntegration(executable="codex", runner=host)

    plan = integration.plan(desired())

    assert tuple(action.kind for action in plan.actions) == (
        IntegrationActionKind.REFRESH_MARKETPLACE,
        IntegrationActionKind.INSTALL_PLUGIN,
        IntegrationActionKind.REMOVE_SERVER,
        IntegrationActionKind.REMOVE_SERVER,
        IntegrationActionKind.CONFIGURE_SERVER,
    )


def test_apply_installs_plugin_and_leaves_one_exact_mcp_server() -> None:
    host = FakeCodex()
    integration = CodexIntegration(executable="codex", runner=host)

    result = integration.apply(integration.plan(desired()))

    assert result.changed
    assert host.plugin_version == "1.5.2"
    assert tuple(host.servers) == ("topicgate",)
    server = result.state.servers[0]
    assert server.mode is McpMode.CONTROL
    assert server.environment == (("TOPICGATE_DATA_DIR", "C:/TopicGate/data"),)
    assert not integration.plan(desired()).actions


def test_apply_reenables_a_disabled_plugin() -> None:
    host = FakeCodex()
    host.marketplace = True
    host.plugin_version = "1.5.2"
    host.plugin_enabled = False
    specification = desired()
    host.servers["topicgate"] = {
        "name": "topicgate",
        "transport": {
            "type": "stdio",
            "command": specification.command,
            "args": list(specification.arguments),
            "env": dict(specification.environment),
        },
    }
    integration = CodexIntegration(executable="codex", runner=host)

    plan = integration.plan(specification)

    assert plan.current.plugin_installed
    assert plan.current.plugin_enabled is False
    assert plan.current.issues == ("TopicGate plugin is disabled.",)
    assert tuple(action.kind for action in plan.actions) == (
        IntegrationActionKind.INSTALL_PLUGIN,
    )

    result = integration.apply(plan)

    assert result.state.plugin_enabled is True
    assert result.messages[0] == "Enabled the TopicGate plugin."
    assert not integration.plan(specification).actions


def test_remove_deletes_plugin_and_both_known_server_names() -> None:
    host = FakeCodex()
    host.marketplace = True
    host.plugin_version = "1.5.2"
    for name in ("topicgate", "topicgate-control"):
        host.servers[name] = {
            "name": name,
            "transport": {
                "type": "stdio",
                "command": "topicgate",
                "args": ["--mode", "control"],
            },
        }
    integration = CodexIntegration(executable="codex", runner=host)

    result = integration.remove()

    assert result.changed
    assert host.plugin_version is None
    assert host.servers == {}
