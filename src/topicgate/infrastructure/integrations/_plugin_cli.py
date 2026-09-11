from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from pathlib import Path
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


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class PluginCliIntegration(ABC):
    """Shared convergence flow for plugin-capable command-line hosts."""

    marketplace_name = "topicgate"
    marketplace_source = "Dumdart/TopicGate"
    plugin_selector = "topicgate@topicgate"
    server_names = ("topicgate", "topicgate-control")
    manages_plugin = True

    def __init__(
        self,
        *,
        executable: str | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._executable = executable or shutil.which(self.executable_name)
        self._runner = runner or self._run_command

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def executable_name(self) -> str: ...

    @abstractmethod
    def inspect(self) -> PlatformIntegrationState: ...

    def plan(self, desired: IntegrationSpec) -> IntegrationPlan:
        current = self.inspect()
        if not current.available:
            raise RuntimeError(current.issues[0])

        actions: list[IntegrationAction] = []
        plugin_outdated = self._base_version(
            current.plugin_version
        ) != self._base_version(desired.package_version)
        if not current.marketplace_configured:
            actions.append(
                IntegrationAction(
                    IntegrationActionKind.ADD_MARKETPLACE,
                    self.marketplace_source,
                )
            )
        elif self.manages_plugin and plugin_outdated:
            actions.append(
                IntegrationAction(
                    IntegrationActionKind.REFRESH_MARKETPLACE,
                    self.marketplace_name,
                )
            )

        if self.manages_plugin and plugin_outdated:
            actions.append(
                IntegrationAction(
                    IntegrationActionKind.INSTALL_PLUGIN,
                    self.plugin_selector,
                )
            )

        server_by_name = {server.name: server for server in current.servers}
        legacy_server = server_by_name.get("topicgate-control")
        if legacy_server is not None and self._can_remove_server(legacy_server):
            actions.append(
                IntegrationAction(
                    IntegrationActionKind.REMOVE_SERVER,
                    legacy_server.name,
                )
            )

        configured_server = server_by_name.get(desired.server_name)
        if not self._matches(configured_server, desired):
            if configured_server is not None and self._can_remove_server(
                configured_server
            ):
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
                self._add_marketplace(action.target)
                messages.append("Configured the TopicGate marketplace.")
            elif action.kind is IntegrationActionKind.REFRESH_MARKETPLACE:
                self._refresh_marketplace(action.target)
                messages.append("Refreshed the TopicGate marketplace.")
            elif action.kind is IntegrationActionKind.INSTALL_PLUGIN:
                updating = plan.current.plugin_installed
                self._install_plugin(
                    action.target,
                    updating=updating,
                )
                verb = "Updated" if updating else "Installed"
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
            raise RuntimeError(
                f"{self.name} integration did not converge after configuration."
            )
        messages.extend(self._completion_messages())
        return IntegrationResult(state, changed=True, messages=tuple(messages))

    def remove(self) -> IntegrationResult:
        current = self.inspect()
        if not current.available:
            raise RuntimeError(current.issues[0])

        changed = any(
            server.name in self.server_names and self._can_remove_server(server)
            for server in current.servers
        ) or (self.manages_plugin and current.plugin_installed)
        messages: list[str] = []
        for server in current.servers:
            if server.name in self.server_names and self._can_remove_server(server):
                self._remove_server(server.name)
                messages.append(f"Removed MCP server {server.name}.")
        if self.manages_plugin and current.plugin_installed:
            self._remove_plugin()
            messages.append("Removed the TopicGate plugin.")

        state = self.inspect()
        removable_servers = tuple(
            server for server in state.servers if self._can_remove_server(server)
        )
        if removable_servers or (self.manages_plugin and state.plugin_installed):
            raise RuntimeError(f"{self.name} integration is still present after removal.")
        if not messages:
            messages.append(f"{self.name} integration is not installed.")
        else:
            messages.extend(self._removal_messages())
        return IntegrationResult(
            state,
            changed=changed,
            messages=tuple(messages),
        )

    def _execute(self, arguments: Sequence[str]) -> str:
        if self._executable is None:
            raise RuntimeError(f"{self.executable_name} CLI was not found on PATH.")
        completed = self._runner((self._executable, *arguments))
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Unknown error").strip()
            raise RuntimeError(f"{self.name} command failed: {detail}")
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
    def _normal_path(value: str) -> str:
        return str(Path(value).expanduser().resolve()).casefold()

    @staticmethod
    def _base_version(value: str | None) -> str | None:
        return value.split("+", 1)[0] if value else None

    def _matches(
        self,
        server: McpServerState | None,
        desired: IntegrationSpec,
    ) -> bool:
        if server is None:
            return False
        return (
            self._normal_path(server.command) == self._normal_path(desired.command)
            and server.arguments == desired.arguments
            and server.environment == tuple(sorted(desired.environment))
        )

    @staticmethod
    def _can_remove_server(server: McpServerState) -> bool:
        return server.source != "plugin"

    @abstractmethod
    def _add_marketplace(self, source: str) -> None: ...

    @abstractmethod
    def _refresh_marketplace(self, name: str) -> None: ...

    @abstractmethod
    def _install_plugin(self, selector: str, *, updating: bool) -> None: ...

    @abstractmethod
    def _remove_plugin(self) -> None: ...

    @abstractmethod
    def _configure_server(self, desired: IntegrationSpec) -> None: ...

    @abstractmethod
    def _remove_server(self, name: str) -> None: ...

    @abstractmethod
    def _completion_messages(self) -> tuple[str, ...]: ...

    @abstractmethod
    def _removal_messages(self) -> tuple[str, ...]: ...
