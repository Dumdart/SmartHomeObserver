from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import re
import shutil
import subprocess

from topicgate.app.models.integration import (
    IntegrationAction,
    IntegrationActionKind,
    IntegrationPlan,
    IntegrationResult,
    IntegrationSpec,
    McpServerState,
    PlatformIntegrationState,
)
from topicgate.infrastructure.integrations._plugin_cli import _normal_path


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

_MARKETPLACE_NAME = "topicgate"
_MARKETPLACE_SOURCE = "Dumdart/TopicGate"
_PLUGIN_SELECTOR = "topicgate@topicgate"
_SERVER_NAMES = ("topicgate", "topicgate-control")


class CodexIntegration:
    """Codex-specific plugin and MCP configuration adapter."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._executable = executable or shutil.which("codex")
        self._runner = runner or self._run_command

    @property
    def name(self) -> str:
        return "codex"

    def inspect(self) -> PlatformIntegrationState:
        if self._executable is None:
            return PlatformIntegrationState(
                platform=self.name,
                available=False,
                issues=("Codex CLI was not found on PATH.",),
            )

        marketplace_output = self._execute(("plugin", "marketplace", "list"))
        plugin_output = self._execute(("plugin", "list"))
        mcp_output = self._execute(("mcp", "list", "--json"))

        marketplace_configured = any(
            line.split(maxsplit=1)[0] == _MARKETPLACE_NAME
            for line in marketplace_output.splitlines()
            if line.strip() and not line.startswith("MARKETPLACE")
        )
        plugin_version, plugin_enabled = self._plugin_state(plugin_output)
        servers = self._mcp_servers(mcp_output)
        issues: list[str] = []
        if not plugin_version:
            issues.append("TopicGate plugin is not installed.")
        elif not plugin_enabled:
            issues.append("TopicGate plugin is disabled.")
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
            plugin_enabled=plugin_enabled,
        )

    def plan(self, desired: IntegrationSpec) -> IntegrationPlan:
        current = self.inspect()
        if not current.available:
            raise RuntimeError(current.issues[0])

        actions: list[IntegrationAction] = []
        if not current.marketplace_configured:
            actions.append(
                IntegrationAction(
                    IntegrationActionKind.ADD_MARKETPLACE,
                    _MARKETPLACE_SOURCE,
                )
            )
        elif self._base_version(current.plugin_version) != self._base_version(
            desired.package_version
        ):
            actions.append(
                IntegrationAction(
                    IntegrationActionKind.REFRESH_MARKETPLACE,
                    _MARKETPLACE_NAME,
                )
            )

        plugin_outdated = self._base_version(
            current.plugin_version
        ) != self._base_version(desired.package_version)
        if plugin_outdated or current.plugin_enabled is False:
            actions.append(
                IntegrationAction(
                    IntegrationActionKind.INSTALL_PLUGIN,
                    _PLUGIN_SELECTOR,
                )
            )

        server_by_name = {server.name: server for server in current.servers}
        legacy_server = server_by_name.get("topicgate-control")
        if legacy_server is not None:
            actions.append(
                IntegrationAction(
                    IntegrationActionKind.REMOVE_SERVER,
                    legacy_server.name,
                )
            )

        configured_server = server_by_name.get(desired.server_name)
        if not self._matches(configured_server, desired):
            if configured_server is not None:
                actions.append(
                    IntegrationAction(
                        IntegrationActionKind.REMOVE_SERVER,
                        configured_server.name,
                    )
                )
            actions.append(
                IntegrationAction(
                    IntegrationActionKind.CONFIGURE_SERVER,
                    desired.server_name,
                )
            )

        return IntegrationPlan(desired, current, tuple(actions))

    def apply(self, plan: IntegrationPlan) -> IntegrationResult:
        messages: list[str] = []
        for action in plan.actions:
            if action.kind is IntegrationActionKind.ADD_MARKETPLACE:
                self._execute(("plugin", "marketplace", "add", action.target))
                messages.append("Configured the TopicGate marketplace.")
            elif action.kind is IntegrationActionKind.REFRESH_MARKETPLACE:
                self._execute(("plugin", "marketplace", "upgrade", action.target))
                messages.append("Refreshed the TopicGate marketplace.")
            elif action.kind is IntegrationActionKind.INSTALL_PLUGIN:
                self._execute(("plugin", "add", action.target))
                verb = (
                    "Enabled"
                    if plan.current.plugin_enabled is False
                    else "Installed"
                )
                messages.append(f"{verb} the TopicGate plugin.")
            elif action.kind is IntegrationActionKind.REMOVE_SERVER:
                self._remove_server(action.target)
                messages.append(f"Removed MCP server {action.target}.")
            elif action.kind is IntegrationActionKind.CONFIGURE_SERVER:
                self._configure_server(plan.desired)
                messages.append(
                    f"Configured one {plan.desired.mode.value} TopicGate MCP server."
                )

        state = self.inspect()
        if self.plan(plan.desired).actions:
            raise RuntimeError("Codex integration did not converge after configuration.")
        messages.append("Restart Codex and open a new task to load the integration.")
        return IntegrationResult(state, changed=True, messages=tuple(messages))

    def remove(self) -> IntegrationResult:
        current = self.inspect()
        if not current.available:
            raise RuntimeError(current.issues[0])

        messages: list[str] = []
        for server_name in _SERVER_NAMES:
            if any(server.name == server_name for server in current.servers):
                self._remove_server(server_name)
                messages.append(f"Removed MCP server {server_name}.")
        if current.plugin_installed:
            self._execute(("plugin", "remove", _PLUGIN_SELECTOR))
            messages.append("Removed the TopicGate plugin.")

        state = self.inspect()
        if state.plugin_installed or state.servers:
            raise RuntimeError("Codex integration is still present after removal.")
        if not messages:
            messages.append("Codex integration is not installed.")
        else:
            messages.append("Restart Codex to unload the integration.")
        changed = current.plugin_installed or bool(current.servers)
        return IntegrationResult(state, changed=changed, messages=tuple(messages))

    def _configure_server(self, desired: IntegrationSpec) -> None:
        arguments = ["mcp", "add"]
        for key, value in desired.environment:
            arguments.extend(("--env", f"{key}={value}"))
        arguments.extend(
            (
                desired.server_name,
                "--",
                desired.command,
                *desired.arguments,
            )
        )
        self._execute(tuple(arguments))

    def _remove_server(self, name: str) -> None:
        try:
            self._execute(("mcp", "remove", name))
        except RuntimeError as error:
            detail = str(error).lower()
            if "not found" not in detail and "no mcp server" not in detail:
                raise

    def _execute(self, arguments: Sequence[str]) -> str:
        if self._executable is None:
            raise RuntimeError("Codex CLI was not found on PATH.")
        completed = self._runner((self._executable, *arguments))
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Unknown error").strip()
            raise RuntimeError(f"Codex command failed: {detail}")
        return completed.stdout

    @staticmethod
    def _run_command(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    @staticmethod
    def _plugin_state(output: str) -> tuple[str | None, bool | None]:
        pattern = re.compile(
            rf"^\s*{re.escape(_PLUGIN_SELECTOR)}\s+installed,\s+"
            rf"(enabled|disabled)\s+(\S+)",
            re.MULTILINE,
        )
        match = pattern.search(output)
        if match is None:
            return None, None
        return match.group(2), match.group(1) == "enabled"

    @staticmethod
    def _mcp_servers(output: str) -> tuple[McpServerState, ...]:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Codex returned invalid MCP configuration JSON: {error}"
            ) from error
        if not isinstance(payload, list):
            raise RuntimeError("Codex returned an unsupported MCP configuration shape.")

        servers: list[McpServerState] = []
        for item in payload:
            if not isinstance(item, dict) or item.get("name") not in _SERVER_NAMES:
                continue
            transport = item.get("transport")
            if not isinstance(transport, dict) or transport.get("type") != "stdio":
                continue
            environment = transport.get("env") or {}
            servers.append(
                McpServerState(
                    name=item["name"],
                    command=str(transport.get("command", "")),
                    arguments=tuple(
                        str(value) for value in transport.get("args", [])
                    ),
                    environment=tuple(
                        sorted(
                            (str(key), str(value))
                            for key, value in environment.items()
                        )
                    ),
                )
            )
        return tuple(servers)

    @staticmethod
    def _matches(server: McpServerState | None, desired: IntegrationSpec) -> bool:
        if server is None:
            return False
        return (
            _normal_path(server.command) == _normal_path(desired.command)
            and server.arguments == desired.arguments
            and server.environment == tuple(sorted(desired.environment))
        )

    @staticmethod
    def _base_version(value: str | None) -> str | None:
        return value.split("+", 1)[0] if value else None
