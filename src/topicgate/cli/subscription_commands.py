"""Subscription commands for the optional TopicGate CLI."""

import argparse
from functools import partial

from topicgate.cli._common import DependenciesFactory, print_error
from topicgate.core.models.subscription import Subscription


def list_subscriptions(
    args: argparse.Namespace,
    *,
    dependencies_factory: DependenciesFactory,
) -> int:
    dependencies = dependencies_factory()

    try:
        profile = dependencies.broker_profiles.get_profile_by_name(args.name)
    except (KeyError, ValueError) as error:
        print_error(error)
        return 1

    for subscription in profile.workspace.subscriptions:
        print(
            subscription.topic_filter,
            subscription.qos,
            subscription.retain_as_published,
            subscription.retain_handling,
            sep="\t",
        )
    return 0


def add_subscription(
    args: argparse.Namespace,
    *,
    dependencies_factory: DependenciesFactory,
) -> int:
    dependencies = dependencies_factory()

    try:
        profile = dependencies.broker_profiles.get_profile_by_name(args.name)
        subscription = dependencies.broker_profiles.add_subscription(
            profile.id,
            Subscription(
                topic_filter=args.topic,
                qos=args.qos,
                retain_as_published=args.retain_as_published,
                retain_handling=args.retain_handling,
            ),
        )
    except ValueError as error:
        if "already exists" in str(error):
            print(f"Subscription already exists: {args.topic}")
            return 0
        print_error(error)
        return 1
    except KeyError as error:
        print_error(error)
        return 1

    print(f"Subscription added: {subscription}")
    return 0


def remove_subscription(
    args: argparse.Namespace,
    *,
    dependencies_factory: DependenciesFactory,
) -> int:
    dependencies = dependencies_factory()

    try:
        profile = dependencies.broker_profiles.get_profile_by_name(args.name)
        subscription = dependencies.broker_profiles.remove_subscription(
            profile.id,
            args.topic,
        )
    except (KeyError, ValueError) as error:
        print_error(error)
        return 1

    print(f"Subscription removed: {subscription}")
    return 0


def configure_subscription_commands(
    parser: argparse.ArgumentParser,
    dependencies_factory: DependenciesFactory,
) -> None:
    commands = parser.add_subparsers(dest="subscription_command", required=True)

    list_parser = commands.add_parser(
        "list", help="List subscriptions for a profile"
    )
    list_parser.add_argument("--name", help="Profile name", required=True)
    list_parser.set_defaults(
        handler=partial(list_subscriptions, dependencies_factory=dependencies_factory)
    )

    add_parser = commands.add_parser("add", help="Add a subscription")
    add_parser.add_argument("--name", help="Profile name", required=True)
    add_parser.add_argument("--topic", help="Topic to subscribe to", required=True)
    add_parser.add_argument(
        "--qos", type=int, choices=(0, 1, 2), default=1, help="QoS level"
    )
    add_parser.add_argument(
        "--retain-as-published", action="store_true", help="Retain as published"
    )
    add_parser.add_argument(
        "--retain-handling",
        type=int,
        choices=(0, 1, 2),
        default=0,
        help="Retain handling",
    )
    add_parser.set_defaults(
        handler=partial(add_subscription, dependencies_factory=dependencies_factory)
    )

    remove_parser = commands.add_parser("remove", help="Remove a subscription")
    remove_parser.add_argument("--name", help="Profile name", required=True)
    remove_parser.add_argument(
        "--topic", help="Topic to unsubscribe from", required=True
    )
    remove_parser.set_defaults(
        handler=partial(remove_subscription, dependencies_factory=dependencies_factory)
    )
