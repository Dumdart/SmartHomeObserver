import argparse
from collections.abc import Callable
from functools import partial

from topicgate.cli._common import IntegrationDependenciesFactory
from topicgate.cli.platform_commands.integration import (
    PlatformFactory,
    integration_install,
    integration_repair,
    integration_status,
    integration_uninstall,
)
from topicgate.infrastructure.integrations.claude import ClaudeIntegration
from topicgate.infrastructure.integrations.codex import CodexIntegration
from topicgate.infrastructure.integrations.copilot import CopilotIntegration
from topicgate.infrastructure.integrations.cursor import CursorIntegration


def _platforms() -> tuple[tuple[str, str, PlatformFactory], ...]:
    return (
        ("codex", "Codex", CodexIntegration),
        ("claude", "Claude Code", ClaudeIntegration),
        ("cursor", "Cursor", CursorIntegration),
        ("copilot", "GitHub Copilot CLI", CopilotIntegration),
    )


def _platform_parser(
    parser: argparse.ArgumentParser,
    handler: Callable[..., int],
    dependencies_factory: IntegrationDependenciesFactory,
    *,
    include_mode: bool,
) -> None:
    platforms = parser.add_subparsers(dest="integration_platform", required=True)
    for name, display_name, platform_factory in _platforms():
        platform_parser = platforms.add_parser(
            name,
            help=f"Manage the {display_name} integration",
        )
        if include_mode:
            platform_parser.add_argument(
                "--mode",
                choices=("read-only", "control"),
                default=None,
                help="MCP capability mode; install defaults to read-only",
            )
        platform_parser.set_defaults(
            handler=partial(
                handler,
                dependencies_factory=dependencies_factory,
                platform_factory=platform_factory,
            )
        )


def configure_integration_commands(
    parser: argparse.ArgumentParser,
    dependencies_factory: IntegrationDependenciesFactory,
) -> None:
    commands = parser.add_subparsers(dest="integration_command", required=True)

    install_parser = commands.add_parser(
        "install", help="Install or update an integration"
    )
    _platform_parser(
        install_parser,
        integration_install,
        dependencies_factory,
        include_mode=True,
    )

    status_parser = commands.add_parser("status", help="Inspect an integration")
    _platform_parser(
        status_parser,
        integration_status,
        dependencies_factory,
        include_mode=False,
    )

    repair_parser = commands.add_parser(
        "repair", help="Repair an integration without changing its mode"
    )
    _platform_parser(
        repair_parser,
        integration_repair,
        dependencies_factory,
        include_mode=True,
    )

    uninstall_parser = commands.add_parser(
        "uninstall", help="Remove an integration"
    )
    _platform_parser(
        uninstall_parser,
        integration_uninstall,
        dependencies_factory,
        include_mode=False,
    )
