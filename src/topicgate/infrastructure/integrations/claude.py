from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from topicgate.app.models.integration import (
    IntegrationSpec,
    McpMode,
    McpServerState,
    PlatformIntegrationState,
)
from topicgate.infrastructure.integrations._plugin_cli import PluginCliIntegration


class ClaudeIntegration(PluginCliIntegration):
    """Claude Code plugin and user-scoped MCP integration."""

    def __init__(self, *, config_path: Path | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config_path = config_path or Path.home() / ".claude.json"

    @property
    def name(self) -> str:
        return "claude"

    @property
    def executable_name(self) -> str:
        return "claude"

    def inspect(self) -> PlatformIntegrationState:
        if self._executable is None:
            return PlatformIntegrationState(
                platform=self.name,
                available=False,
                issues=("Claude Code CLI was not found on PATH.",),
            )

        marketplaces = self._json_command(
            ("plugin", "marketplace", "list", "--json")
        )
        plugins = self._json_command(("plugin", "list", "--json"))
        marketplace_configured = self._contains_named_record(
            marketplaces,
            self.marketplace_name,
        )
        plugin_version = self._plugin_version(plugins)
        servers = self._configured_servers()
        if plugin_version and not any(
            server.name == "topicgate" for server in servers
        ):
            servers += (
                McpServerState(
                    "topicgate",
                    "topicgate",
                    ("--mode", "read-only"),
                    source="plugin",
                ),
            )

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
            marketplace_configured=marketplace_configured,
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
        self._execute(("plugin", operation, selector, "--scope", "user"))

    def _remove_plugin(self) -> None:
        self._execute(
            ("plugin", "uninstall", self.plugin_selector, "--scope", "user")
        )

    def _configure_server(self, desired: IntegrationSpec) -> None:
        arguments = ["mcp", "add", "--transport", "stdio", "--scope", "user"]
        for key, value in desired.environment:
            arguments.extend(("--env", f"{key}={value}"))
        arguments.extend(
            (desired.server_name, "--", desired.command, *desired.arguments)
        )
        self._execute(tuple(arguments))

    def _remove_server(self, name: str) -> None:
        self._execute(("mcp", "remove", "--scope", "user", name))

    def _completion_messages(self) -> tuple[str, ...]:
        return ("Restart Claude Code or run /reload-plugins.",)

    def _removal_messages(self) -> tuple[str, ...]:
        return ("Restart Claude Code or run /reload-plugins.",)

    def _json_command(self, arguments: tuple[str, ...]) -> Any:
        output = self._execute(arguments)
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Claude returned invalid JSON for {' '.join(arguments)}: {error}"
            ) from error

    def _configured_servers(self) -> tuple[McpServerState, ...]:
        if not self._config_path.exists():
            return ()
        try:
            payload = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Cannot read Claude MCP configuration {self._config_path}: {error}"
            ) from error
        configured = payload.get("mcpServers", {})
        if not isinstance(configured, dict):
            raise RuntimeError("Claude user MCP configuration has an invalid shape.")
        return tuple(
            self._server_state(name, value)
            for name, value in configured.items()
            if name in self.server_names and isinstance(value, dict)
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
            source="user",
        )

    @classmethod
    def _plugin_version(cls, payload: Any) -> str | None:
        for record in cls._records(payload):
            name = str(
                record.get("id")
                or record.get("name")
                or record.get("plugin")
                or ""
            )
            marketplace = str(
                record.get("marketplace")
                or record.get("marketplaceName")
                or ""
            )
            if name not in {"topicgate", "topicgate@topicgate"}:
                continue
            if "@" not in name and marketplace not in {"", "topicgate"}:
                continue
            version = record.get("version") or record.get("installedVersion")
            return str(version) if version else None
        return None

    @classmethod
    def _contains_named_record(cls, payload: Any, expected: str) -> bool:
        return any(
            str(record.get("name") or record.get("id") or "") == expected
            for record in cls._records(payload)
        )

    @classmethod
    def _records(cls, payload: Any) -> tuple[dict[str, Any], ...]:
        if isinstance(payload, list):
            return tuple(item for item in payload if isinstance(item, dict))
        if not isinstance(payload, dict):
            return ()
        records: list[dict[str, Any]] = []
        for key in ("plugins", "marketplaces", "items", "installedPlugins"):
            value = payload.get(key)
            if isinstance(value, list):
                records.extend(item for item in value if isinstance(item, dict))
            elif isinstance(value, dict):
                records.extend(
                    {"name": name, **item}
                    for name, item in value.items()
                    if isinstance(item, dict)
                )
        if records:
            return tuple(records)
        return tuple(
            {"name": name, **item}
            for name, item in payload.items()
            if isinstance(item, dict)
        )
