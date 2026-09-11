from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from topicgate.app.models.integration import (
    IntegrationSpec,
    McpServerState,
    PlatformIntegrationState,
)
from topicgate.infrastructure.integrations._plugin_cli import PluginCliIntegration


class CursorIntegration(PluginCliIntegration):
    """Cursor marketplace and user MCP integration.

    Cursor Agent does not currently expose non-interactive plugin installation.
    The adapter therefore maintains the marketplace and MCP server; plugin skills
    remain an explicit installation from Cursor's /plugin interface.
    """

    marketplace_source = "https://github.com/Dumdart/TopicGate"
    manages_plugin = False

    def __init__(self, *, config_path: Path | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config_path = config_path or Path.home() / ".cursor" / "mcp.json"

    @property
    def name(self) -> str:
        return "cursor"

    @property
    def executable_name(self) -> str:
        return "cursor-agent"

    def inspect(self) -> PlatformIntegrationState:
        if self._executable is None:
            return PlatformIntegrationState(
                platform=self.name,
                available=False,
                issues=("Cursor Agent CLI was not found on PATH.",),
            )

        output = self._execute(
            ("plugin", "marketplace", "list", "--format", "json")
        )
        try:
            marketplaces = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Cursor returned invalid marketplace JSON: {error}"
            ) from error
        marketplace_configured = (
            any(
                isinstance(item, dict)
                and (
                    item.get("name") == self.marketplace_name
                    or str(item.get("gitUrl", "")).rstrip("/").casefold()
                    == self.marketplace_source.rstrip("/").casefold()
                )
                for item in marketplaces
            )
            if isinstance(marketplaces, list)
            else False
        )
        servers = self._configured_servers()
        issues: list[str] = []
        if not servers:
            issues.append("TopicGate MCP server is not configured.")
        if len(servers) > 1:
            issues.append("Multiple TopicGate MCP servers are configured.")
        return PlatformIntegrationState(
            platform=self.name,
            available=True,
            marketplace_configured=marketplace_configured,
            plugin_installed=False,
            servers=servers,
            issues=tuple(issues),
        )

    def _add_marketplace(self, source: str) -> None:
        self._execute(("plugin", "marketplace", "add", source))

    def _refresh_marketplace(self, name: str) -> None:
        self._execute(("plugin", "marketplace", "update", name))

    def _install_plugin(self, selector: str, *, updating: bool) -> None:
        raise RuntimeError("Cursor does not support non-interactive plugin installs.")

    def _remove_plugin(self) -> None:
        raise RuntimeError("Cursor does not support non-interactive plugin removal.")

    def _configure_server(self, desired: IntegrationSpec) -> None:
        payload = self._read_configuration()
        servers = payload.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise RuntimeError("Cursor MCP configuration has an invalid shape.")
        servers[desired.server_name] = {
            "type": "stdio",
            "command": desired.command,
            "args": list(desired.arguments),
            "env": dict(desired.environment),
        }
        self._write_configuration(payload)

    def _remove_server(self, name: str) -> None:
        payload = self._read_configuration()
        servers = payload.get("mcpServers", {})
        if isinstance(servers, dict):
            servers.pop(name, None)
        self._write_configuration(payload)

    def _completion_messages(self) -> tuple[str, ...]:
        return (
            "Restart Cursor to load the MCP integration.",
            "Install or update TopicGate skills from Cursor's /plugin interface.",
        )

    def _removal_messages(self) -> tuple[str, ...]:
        return (
            "Restart Cursor to unload the MCP integration.",
            "Remove the TopicGate plugin separately from Cursor's /plugin interface.",
        )

    def _configured_servers(self) -> tuple[McpServerState, ...]:
        payload = self._read_configuration()
        configured = payload.get("mcpServers", {})
        if not isinstance(configured, dict):
            raise RuntimeError("Cursor MCP configuration has an invalid shape.")
        return tuple(
            self._server_state(name, value)
            for name, value in configured.items()
            if name in self.server_names and isinstance(value, dict)
        )

    def _read_configuration(self) -> dict[str, Any]:
        if not self._config_path.exists():
            return {}
        try:
            payload = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Cannot read Cursor MCP configuration {self._config_path}: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise RuntimeError("Cursor MCP configuration has an invalid shape.")
        return payload

    def _write_configuration(self, payload: dict[str, Any]) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_mode = stat.S_IMODE(self._config_path.stat().st_mode)
        except FileNotFoundError:
            file_mode = 0o600

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._config_path.parent,
                prefix=f".{self._config_path.name}.",
                suffix=".topicgate.tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(json.dumps(payload, indent=2) + "\n")
            os.chmod(temporary_path, file_mode)
            os.replace(temporary_path, self._config_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

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
