from dataclasses import dataclass
from enum import StrEnum


class McpMode(StrEnum):
    READ_ONLY = "read-only"
    CONTROL = "control"


@dataclass(frozen=True)
class McpServerState:
    name: str
    command: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    source: str | None = None

    @property
    def mode(self) -> McpMode | None:
        value: str | None = None
        for index, argument in enumerate(self.arguments):
            if argument == "--mode":
                value = (
                    self.arguments[index + 1]
                    if index + 1 < len(self.arguments)
                    else None
                )
            elif argument.startswith("--mode="):
                value = argument.removeprefix("--mode=")
        try:
            return McpMode(value)
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class PlatformIntegrationState:
    platform: str
    available: bool
    marketplace_configured: bool = False
    plugin_installed: bool = False
    plugin_version: str | None = None
    servers: tuple[McpServerState, ...] = ()
    issues: tuple[str, ...] = ()
    plugin_enabled: bool | None = None


class IntegrationActionKind(StrEnum):
    ADD_MARKETPLACE = "add_marketplace"
    REFRESH_MARKETPLACE = "refresh_marketplace"
    INSTALL_PLUGIN = "install_plugin"
    REMOVE_SERVER = "remove_server"
    CONFIGURE_SERVER = "configure_server"


@dataclass(frozen=True)
class IntegrationAction:
    kind: IntegrationActionKind
    target: str


@dataclass(frozen=True)
class IntegrationSpec:
    platform: str
    mode: McpMode
    package_version: str
    server_name: str
    command: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class IntegrationPlan:
    desired: IntegrationSpec
    current: PlatformIntegrationState
    actions: tuple[IntegrationAction, ...]


@dataclass(frozen=True)
class IntegrationResult:
    state: PlatformIntegrationState
    changed: bool
    messages: tuple[str, ...] = ()
