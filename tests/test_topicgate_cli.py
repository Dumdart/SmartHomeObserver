from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from topicgate.cli.topicgate_cli import main
from topicgate.app.models.integration import (
    IntegrationResult,
    McpMode,
    PlatformIntegrationState,
)
from topicgate.core.models.subscription import Subscription


def dependencies_with(profile_name: str = "Zigbee2MQTT Demo"):
    profile = SimpleNamespace(id=uuid4(), name=profile_name)
    broker_profiles = MagicMock()
    broker_profiles.get_profile_by_name.return_value = profile
    return SimpleNamespace(broker_profiles=broker_profiles), profile


def test_profile_add_reuses_existing_profile() -> None:
    dependencies, profile = dependencies_with()

    with patch(
        "topicgate.cli.topicgate_cli.AppDependencies",
        return_value=dependencies,
    ):
        result = main(
            [
                "profile",
                "add",
                "--name",
                profile.name,
                "--no-password",
            ]
        )

    assert result == 0
    dependencies.broker_profiles.create_profile.assert_not_called()


def test_profile_add_reads_password_from_stdin() -> None:
    dependencies, profile = dependencies_with()
    dependencies.broker_profiles.get_profile_by_name.side_effect = KeyError(
        profile.name
    )
    dependencies.broker_profiles.create_profile.return_value = profile

    with (
        patch(
            "topicgate.cli.topicgate_cli.AppDependencies",
            return_value=dependencies,
        ),
        patch("sys.stdin", StringIO("demo-secret\n")),
    ):
        result = main(
            [
                "profile",
                "add",
                "--name",
                profile.name,
                "--port",
                "1884",
                "--password-stdin",
            ]
        )

    assert result == 0
    config = dependencies.broker_profiles.create_profile.call_args.args[1]
    assert config.port == 1884
    assert config.password == "demo-secret"


def test_subscription_add_resolves_name_and_parses_options() -> None:
    dependencies, profile = dependencies_with()
    subscription = Subscription(
        "zigbee2mqtt/#",
        qos=2,
        retain_as_published=True,
        retain_handling=1,
    )
    dependencies.broker_profiles.add_subscription.return_value = subscription

    with patch(
        "topicgate.cli.topicgate_cli.AppDependencies",
        return_value=dependencies,
    ):
        result = main(
            [
                "sub",
                "add",
                "--name",
                profile.name,
                "--topic",
                subscription.topic_filter,
                "--qos",
                "2",
                "--retain-as-published",
                "--retain-handling",
                "1",
            ]
        )

    assert result == 0
    dependencies.broker_profiles.get_profile_by_name.assert_called_once_with(
        profile.name
    )
    dependencies.broker_profiles.add_subscription.assert_called_once_with(
        profile.id,
        subscription,
    )


def test_subscription_add_accepts_duplicate() -> None:
    dependencies, profile = dependencies_with()
    dependencies.broker_profiles.add_subscription.side_effect = ValueError(
        "A subscription for 'zigbee2mqtt/#' already exists."
    )

    with patch(
        "topicgate.cli.topicgate_cli.AppDependencies",
        return_value=dependencies,
    ):
        result = main(
            [
                "sub",
                "add",
                "--name",
                profile.name,
                "--topic",
                "zigbee2mqtt/#",
            ]
        )

    assert result == 0


def test_profile_test_runs_temporary_connection_check() -> None:
    dependencies, profile = dependencies_with()
    dependencies.broker_profiles.get_all_profiles.return_value = (profile,)
    dependencies.broker_profiles.test_profile.return_value = True

    with patch(
        "topicgate.cli.topicgate_cli.AppDependencies",
        return_value=dependencies,
    ):
        result = main(["profile", "test"])

    assert result == 0
    dependencies.broker_profiles.test_profile.assert_called_once_with(profile.id)


def test_profile_test_can_target_one_named_profile() -> None:
    dependencies, profile = dependencies_with()
    dependencies.broker_profiles.test_profile.return_value = True

    with patch(
        "topicgate.cli.topicgate_cli.AppDependencies",
        return_value=dependencies,
    ):
        result = main(["profile", "test", "--name", profile.name])

    assert result == 0
    dependencies.broker_profiles.get_all_profiles.assert_not_called()
    dependencies.broker_profiles.test_profile.assert_called_once_with(profile.id)


def test_profile_remove_resolves_name() -> None:
    dependencies, profile = dependencies_with()

    with patch(
        "topicgate.cli.topicgate_cli.AppDependencies",
        return_value=dependencies,
    ):
        result = main(["profile", "remove", "--name", profile.name])

    assert result == 0
    dependencies.broker_profiles.delete_profile.assert_called_once_with(profile.id)


def test_subscription_list_has_stable_tab_separated_output(capsys) -> None:
    dependencies, profile = dependencies_with()
    profile.workspace = SimpleNamespace(
        subscriptions=(Subscription("zigbee2mqtt/#", 1, True, 0),)
    )

    with patch(
        "topicgate.cli.topicgate_cli.AppDependencies",
        return_value=dependencies,
    ):
        result = main(["sub", "list", "--name", profile.name])

    assert result == 0
    assert capsys.readouterr().out == "zigbee2mqtt/#\t1\tTrue\t0\n"


def test_integration_install_defaults_to_read_only(capsys) -> None:
    service = MagicMock()
    state = PlatformIntegrationState("codex", available=True)
    service.install.return_value = IntegrationResult(
        state, changed=True, messages=("Configured Codex.",)
    )
    dependencies = SimpleNamespace(integration_service=service)

    with (
        patch(
            "topicgate.cli.topicgate_cli.IntegrationDependencies",
            return_value=dependencies,
        ),
        patch("topicgate.cli.platform_commands.codex_commands.CodexIntegration"),
    ):
        result = main(["integration", "install", "codex"])

    assert result == 0
    assert service.install.call_args.args[1] is McpMode.READ_ONLY
    assert capsys.readouterr().out == "Configured Codex.\n"


def test_integration_repair_preserves_mode_by_default() -> None:
    service = MagicMock()
    state = PlatformIntegrationState("codex", available=True)
    service.repair.return_value = IntegrationResult(state, changed=False)
    dependencies = SimpleNamespace(integration_service=service)

    with (
        patch(
            "topicgate.cli.topicgate_cli.IntegrationDependencies",
            return_value=dependencies,
        ),
        patch("topicgate.cli.platform_commands.codex_commands.CodexIntegration"),
    ):
        result = main(["integration", "repair", "codex"])

    assert result == 0
    assert service.repair.call_args.args[1] is None
