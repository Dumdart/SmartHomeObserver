"""Broker-profile commands for the optional TopicGate CLI."""

import argparse
import sys
from functools import partial
from getpass import getpass

from topicgate.cli._common import DependenciesFactory, print_error
from topicgate.core.config.mqtt_config import MqttConfig


def list_profiles(
    args: argparse.Namespace,
    *,
    dependencies_factory: DependenciesFactory,
) -> int:
    dependencies = dependencies_factory()

    for profile in dependencies.broker_profiles.list_profile_summaries():
        print(
            profile.id,
            profile.name,
            profile.host,
            profile.port,
            profile.username,
            profile.use_tls,
            sep="\t",
        )

    return 0


def _profile_password(args: argparse.Namespace) -> str:
    if args.no_password and args.password_stdin:
        raise ValueError("--no-password and --password-stdin cannot be combined.")
    if args.no_password:
        return ""
    if args.password_stdin:
        password = sys.stdin.readline()
        if not password:
            raise ValueError("No MQTT password was provided on stdin.")
        return password.rstrip("\r\n")
    return getpass("MQTT password: ")


def add_profile(
    args: argparse.Namespace,
    *,
    dependencies_factory: DependenciesFactory,
) -> int:
    dependencies = dependencies_factory()

    try:
        profile = dependencies.broker_profiles.get_profile_by_name(args.name)
    except KeyError:
        pass
    else:
        print(f"Broker profile already exists: {profile.name}")
        return 0

    try:
        password = _profile_password(args)
    except ValueError as error:
        print_error(error)
        return 1

    config = MqttConfig(
        username=args.username,
        host=args.host,
        port=args.port,
        password=password,
        use_tls=args.use_tls,
    )

    try:
        profile = dependencies.broker_profiles.create_profile(args.name, config)
        dependencies.broker_profiles.save()
    except (KeyError, ValueError) as error:
        print_error(error)
        return 1

    print(f"Broker profile added: {profile.name}")
    return 0


def test_profile(
    args: argparse.Namespace,
    *,
    dependencies_factory: DependenciesFactory,
) -> int:
    dependencies = dependencies_factory()

    try:
        profiles = (
            (dependencies.broker_profiles.get_profile_by_name(args.name),)
            if args.name
            else dependencies.broker_profiles.get_all_profiles()
        )
        for profile in profiles:
            print(f"Testing profile: {profile.name}")
            dependencies.broker_profiles.test_profile(profile.id)
    except (KeyError, ConnectionError, TimeoutError, OSError) as error:
        print_error(error)
        return 1

    return 0


def remove_profile(
    args: argparse.Namespace,
    *,
    dependencies_factory: DependenciesFactory,
) -> int:
    dependencies = dependencies_factory()

    try:
        profile = dependencies.broker_profiles.get_profile_by_name(args.name)
        dependencies.broker_profiles.delete_profile(profile.id)
    except (KeyError, ValueError) as error:
        print_error(error)
        return 1

    print(f"Broker profile removed: {profile.name}")
    return 0


def configure_profile_commands(
    parser: argparse.ArgumentParser,
    dependencies_factory: DependenciesFactory,
) -> None:
    commands = parser.add_subparsers(dest="profile_command", required=True)

    add_parser = commands.add_parser("add", help="Add a new profile")
    add_parser.add_argument(
        "--name", help='Profile name (e.g. "MyBroker123")', required=True
    )
    add_parser.add_argument("--host", default="localhost", help="MQTT broker host")
    add_parser.add_argument(
        "--port", type=int, default=1883, help="MQTT broker port"
    )
    add_parser.add_argument("--username", default="", help="MQTT username")
    add_parser.add_argument("--use-tls", action="store_true", help="Use TLS")
    password_group = add_parser.add_mutually_exclusive_group()
    password_group.add_argument(
        "--no-password", action="store_true", help="Use an empty MQTT password"
    )
    password_group.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the MQTT password from standard input",
    )
    add_parser.set_defaults(
        handler=partial(add_profile, dependencies_factory=dependencies_factory)
    )

    list_parser = commands.add_parser("list", help="List all profiles")
    list_parser.set_defaults(
        handler=partial(list_profiles, dependencies_factory=dependencies_factory)
    )

    test_parser = commands.add_parser("test", help="Test profile connection")
    test_parser.add_argument("--name", help="Test only this profile")
    test_parser.set_defaults(
        handler=partial(test_profile, dependencies_factory=dependencies_factory)
    )

    remove_parser = commands.add_parser("remove", help="Remove a profile")
    remove_parser.add_argument("--name", help="Profile name", required=True)
    remove_parser.set_defaults(
        handler=partial(remove_profile, dependencies_factory=dependencies_factory)
    )
