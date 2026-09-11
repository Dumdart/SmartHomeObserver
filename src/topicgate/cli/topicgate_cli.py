"""Optional command-line entry point for TopicGate administration."""

import argparse
from collections.abc import Sequence

from topicgate.app.app_dependencies import AppDependencies
from topicgate.app.integration_dependencies import IntegrationDependencies
from topicgate.cli.integration_commands import configure_integration_commands
from topicgate.cli.profile_commands import configure_profile_commands
from topicgate.cli.subscription_commands import configure_subscription_commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="topicgate-cli",
        description="TopicGate administration commands.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    profile_parser = commands.add_parser("profile", help="Manage broker profiles")
    configure_profile_commands(profile_parser, AppDependencies)

    subscription_parser = commands.add_parser("sub", help="Manage subscriptions")
    configure_subscription_commands(subscription_parser, AppDependencies)

    integration_parser = commands.add_parser("integration", help="Manage integrations")
    configure_integration_commands(integration_parser, IntegrationDependencies)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
