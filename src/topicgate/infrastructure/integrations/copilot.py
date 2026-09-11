from __future__ import annotations

import json
import re
from typing import Any

from topicgate.app.models.integration import (
    IntegrationSpec,
    McpMode,
    McpServerState,
    PlatformIntegrationState,
)
from topicgate.infrastructure.integrations._plugin_cli import PluginCliIntegration


class CopilotIntegration(PluginCliIntegration):
    """GitHub Copilot CLI plugin and MCP integration."""

    @property
    def name(self) -> str:
        return "copilot"

    @property
    def executable_name(self) -> str:
        return "copilot"

    def inspect(self) -> PlatformIntegrationState:
        if self._executable is None:
            return PlatformIntegrationState(
                platform=self.name,
                available=False,
                issues=("GitHub Copilot CLI was not found on PATH.",),
            )

        marketplace_output = self._execute(("plugin", "marketplace", "list"))
        plugin_output = self._execute(("plugin", "list"))
        mcp_output = self._execute(("mcp", "list", "--json"))
        plugin_version = self._plugin_version(plugin_output)
        servers = self._mcp_servers(mcp_output)
        issues: list[str] = []
        if not plugin_version:
            issues.append("TopicGate plugin is not installed.")
        if not servers:
            issues.append("TopicGate MCP server is not configured.")
        if len(servers) > 1:
            issues.append("Multiple TopicGate MCP servers are configured.")
        return PlatformIntegrationState(
            platform=self.name,
            available=True,
            marketplace_configured=bool(
                re.search(r"(?im)^\s*[^\w]*topicgate\b", marketplace_output)
            ),
            plugin_installed=plugin_version is not None,
            plugin_version=plugin_version,
            servers=servers,
            issues=tuple(issues),
        )

    def _matches(
        self,
        server: McpServerState | None,
        desired: IntegrationSpec,
    ) -> bool:
        if server is not None and server.source == "plugin":
            return desired.mode is McpMode.READ_ONLY
        return super()._matches(server, desired)

    def _add_marketplace(self, source: str) -> None:
        self._execute(("plugin", "marketplace", "add", source))

    def _refresh_marketplace(self, name: str) -> None:
        self._execute(("plugin", "marketplace", "update", name))

    def _install_plugin(self, selector: str, *, updating: bool) -> None:
        operation = "update" if updating else "install"
        self._execute(("plugin", operation, selector))

    def _remove_plugin(self) -> None:
        self._execute(("plugin", "uninstall", self.plugin_selector))

    def _configure_server(self, desired: IntegrationSpec) -> None:
        arguments = ["mcp", "add"]
        for key, value in desired.environment:
            arguments.extend(("--env", f"{key}={value}"))
        arguments.extend(
            (desired.server_name, "--", desired.command, *desired.arguments)
        )
        self._execute(tuple(arguments))

    def _remove_server(self, name: str) -> None:
        self._execute(("mcp", "remove", name))

    def _completion_messages(self) -> tuple[str, ...]:
        return ("Restart GitHub Copilot CLI to load the integration.",)

    def _removal_messages(self) -> tuple[str, ...]:
        return ("Restart GitHub Copilot CLI to unload the integration.",)

    @staticmethod
    def _plugin_version(output: str) -> str | None:
        match = re.search(
            r"(?im)^\s*[^\w]*topicgate@topicgate\s+\(v?([^\s)]+)\)",
            output,
        )
        return match.group(1) if match else None

    @classmethod
    def _mcp_servers(cls, output: str) -> tuple[McpServerState, ...]:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Copilot returned invalid MCP configuration JSON: {error}"
            ) from error
        configured = payload.get("mcpServers", {})
        if not isinstance(configured, dict):
            raise RuntimeError("Copilot returned an unsupported MCP configuration shape.")
        return tuple(
            cls._server_state(name, value)
            for name, value in configured.items()
            if name in cls.server_names and isinstance(value, dict)
        )

    @staticmethod
    def _server_state(name: str, value: dict[str, Any]) -> McpServerState:
        environment = value.get("env") or {}
        return McpServerState(
            name=name,
            command=str(value.get("command", "")),
            arguments=tuple(str(item) for item in value.get("args", [])),
            environment=tuple(
                sorted((str(key), str(item)) for key, item in environment.items())
            ),
            source=str(value.get("source")) if value.get("source") else None,
        )
