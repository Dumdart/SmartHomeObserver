import argparse
from collections.abc import Callable

from topicgate.app.models.integration import (
    IntegrationResult,
    McpMode,
    PlatformIntegrationState,
)
from topicgate.app.ports.integration_platform import IntegrationPlatform
from topicgate.cli._common import IntegrationDependenciesFactory, print_error


PlatformFactory = Callable[[], IntegrationPlatform]


def _mode(value: str | None, *, default: McpMode | None) -> McpMode | None:
    return McpMode(value) if value is not None else default


def _print_state(state: PlatformIntegrationState) -> None:
    display_name = state.platform.replace("-", " ").title()
    availability = "available" if state.available else "unavailable"
    print(f"{display_name} CLI: {availability}")
    if state.platform == "cursor" and not state.plugin_installed:
        plugin = "managed interactively by Cursor"
    else:
        plugin = (
            f"installed ({state.plugin_version})"
            if state.plugin_installed
            else "not installed"
        )
    print(f"TopicGate plugin: {plugin}")
    if state.servers:
        for server in state.servers:
            mode = server.mode.value if server.mode is not None else "unknown mode"
            print(f"MCP server: {server.name} ({mode})")
    else:
        print("MCP server: not configured")
    for issue in state.issues:
        print(f"Issue: {issue}")


def _run(operation: Callable[[], IntegrationResult]) -> int:
    try:
        result = operation()
    except (OSError, RuntimeError, ValueError) as error:
        print_error(error)
        return 1
    for message in result.messages:
        print(message)
    return 0


def integration_install(
    args: argparse.Namespace,
    *,
    dependencies_factory: IntegrationDependenciesFactory,
    platform_factory: PlatformFactory,
) -> int:
    dependencies = dependencies_factory()
    platform = platform_factory()
    mode = _mode(args.mode, default=McpMode.READ_ONLY)
    return _run(lambda: dependencies.integration_service.install(platform, mode))


def integration_uninstall(
    _args: argparse.Namespace,
    *,
    dependencies_factory: IntegrationDependenciesFactory,
    platform_factory: PlatformFactory,
) -> int:
    dependencies = dependencies_factory()
    platform = platform_factory()
    return _run(lambda: dependencies.integration_service.uninstall(platform))


def integration_status(
    _args: argparse.Namespace,
    *,
    dependencies_factory: IntegrationDependenciesFactory,
    platform_factory: PlatformFactory,
) -> int:
    dependencies = dependencies_factory()
    platform = platform_factory()
    try:
        state = dependencies.integration_service.status(platform)
    except (OSError, RuntimeError, ValueError) as error:
        print_error(error)
        return 1
    _print_state(state)
    return 0 if state.available and not state.issues else 1


def integration_repair(
    args: argparse.Namespace,
    *,
    dependencies_factory: IntegrationDependenciesFactory,
    platform_factory: PlatformFactory,
) -> int:
    dependencies = dependencies_factory()
    platform = platform_factory()
    mode = _mode(args.mode, default=None)
    return _run(lambda: dependencies.integration_service.repair(platform, mode))
