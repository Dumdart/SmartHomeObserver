from dataclasses import replace

from topicgate.app.models.integration import (
    IntegrationActionKind,
    IntegrationResult,
    IntegrationSpec,
    McpMode,
    PlatformIntegrationState,
)
from topicgate.app.models.mcp_setup import McpSetupInformation
from topicgate.app.ports.integration_platform import IntegrationPlatform


class IntegrationService:
    """Converge supported agent hosts on one TopicGate integration."""

    def __init__(self, information: McpSetupInformation):
        self._information = information

    def install(
        self,
        platform: IntegrationPlatform,
        mode: McpMode = McpMode.READ_ONLY,
    ) -> IntegrationResult:
        plan = platform.plan(self._specification(platform, mode))
        if not plan.actions:
            return IntegrationResult(
                state=plan.current,
                changed=False,
                messages=(f"{platform.name} integration is already configured.",),
            )
        return platform.apply(plan)

    def uninstall(self, platform: IntegrationPlatform) -> IntegrationResult:
        return platform.remove()

    def status(self, platform: IntegrationPlatform) -> PlatformIntegrationState:
        state = platform.inspect()
        if not state.available:
            return state

        configured_modes = {
            server.mode for server in state.servers if server.mode is not None
        }
        mode = (
            configured_modes.pop()
            if len(configured_modes) == 1
            else McpMode.READ_ONLY
        )
        plan = platform.plan(self._specification(platform, mode))
        issues = list(state.issues)
        action_kinds = {action.kind for action in plan.actions}
        if IntegrationActionKind.ADD_MARKETPLACE in action_kinds:
            issues.append("TopicGate marketplace is not configured.")
        if (
            state.plugin_installed
            and IntegrationActionKind.INSTALL_PLUGIN in action_kinds
        ):
            issues.append(
                "TopicGate plugin version does not match the installed "
                f"TopicGate package {self._information.version}."
            )
        if IntegrationActionKind.CONFIGURE_SERVER in action_kinds:
            issues.append(
                "TopicGate MCP configuration does not match the resolved "
                "executable, mode, or data directory."
            )
        return replace(state, issues=tuple(dict.fromkeys(issues)))

    def repair(
        self,
        platform: IntegrationPlatform,
        mode: McpMode | None = None,
    ) -> IntegrationResult:
        if mode is None:
            state = platform.inspect()
            configured_modes = {
                server.mode for server in state.servers if server.mode is not None
            }
            mode = (
                configured_modes.pop()
                if len(configured_modes) == 1
                else McpMode.READ_ONLY
            )
        return self.install(platform, mode)

    def _specification(
        self,
        platform: IntegrationPlatform,
        mode: McpMode,
    ) -> IntegrationSpec:
        return IntegrationSpec(
            platform=platform.name,
            mode=mode,
            package_version=self._information.version,
            server_name="topicgate",
            command=self._information.command,
            arguments=(
                *self._information.command_prefix_arguments,
                "--mode",
                mode.value,
            ),
            environment=(("TOPICGATE_DATA_DIR", str(self._information.data_path)),),
        )
