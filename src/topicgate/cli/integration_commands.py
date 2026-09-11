import argparse
from collections.abc import Callable
from functools import partial

from topicgate.cli._common import DependenciesFactory
from topicgate.cli.platform_commands.codex_commands import (
    codex_install,
    codex_repair,
    codex_status,
    codex_uninstall,
)


def _platform_parser(
    parser: argparse.ArgumentParser,
    handler: Callable[..., int],
    dependencies_factory: DependenciesFactory,
    *,
    include_mode: bool,
) -> None:
    platforms = parser.add_subparsers(dest="integration_platform", required=True)
    codex_parser = platforms.add_parser("codex", help="Manage the Codex integration")
    if include_mode:
        codex_parser.add_argument(
            "--mode",
            choices=("read-only", "control"),
            default=None,
            help="MCP capability mode; install defaults to read-only",
        )
    codex_parser.set_defaults(
        handler=partial(handler, dependencies_factory=dependencies_factory)
    )


def configure_integration_commands(
    parser: argparse.ArgumentParser,
    dependencies_factory: DependenciesFactory,
) -> None:
    commands = parser.add_subparsers(dest="integration_command", required=True)

    install_parser = commands.add_parser(
        "install", help="Install or update an integration"
    )
    _platform_parser(
        install_parser,
        codex_install,
        dependencies_factory,
        include_mode=True,
    )

    status_parser = commands.add_parser("status", help="Inspect an integration")
    _platform_parser(
        status_parser,
        codex_status,
        dependencies_factory,
        include_mode=False,
    )

    repair_parser = commands.add_parser(
        "repair", help="Repair an integration without changing its mode"
    )
    _platform_parser(
        repair_parser,
        codex_repair,
        dependencies_factory,
        include_mode=True,
    )

    uninstall_parser = commands.add_parser(
        "uninstall", help="Remove an integration"
    )
    _platform_parser(
        uninstall_parser,
        codex_uninstall,
        dependencies_factory,
        include_mode=False,
    )
