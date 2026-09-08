from unittest.mock import MagicMock

from topicgate.core.config.app_config import AppConfig
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.subscription import Subscription
from topicgate.app.services.broker_profile_service import BrokerProfileService


class FakeMqttConnection:
    def __init__(
        self,
        *,
        connect_error: Exception | None = None,
    ) -> None:
        self.connect_error = connect_error
        self.connect_timeouts: list[float] = []
        self.disconnect_count = 0

    async def connect(self, timeout: float = 10.0) -> bool:
        self.connect_timeouts.append(timeout)
        if self.connect_error is not None:
            raise self.connect_error
        return True

    async def disconnect(self) -> bool:
        self.disconnect_count += 1
        return False

def test_broker_repository_returns_initial_settings(credential_store) -> None:
    config = AppConfig(
        mqtt=MqttConfig("broker", 1883, "observer", "password")
    )

    repository = BrokerProfileService(config, credential_store=credential_store)

    assert repository.get() is config
    assert repository.get_mqtt() == config.mqtt


def test_broker_repository_uses_local_defaults_without_configuration_file(
    credential_store,
) -> None:
    repository = BrokerProfileService(credential_store=credential_store)

    assert repository.get_mqtt() == MqttConfig("localhost", 1883, "", "")


def test_broker_repository_updates_complete_settings_and_saves(
    credential_store,
) -> None:
    repository = BrokerProfileService(
        AppConfig(MqttConfig("old", 1883, "", "")),
        credential_store=credential_store,
    )
    repository.save = MagicMock()
    replacement = AppConfig(MqttConfig("new", 8883, "observer", "password", True))

    repository.update(replacement)

    assert repository.get() is replacement
    repository.save.assert_called_once_with()


def test_broker_repository_updates_mqtt_settings_and_saves(
    credential_store,
) -> None:
    initial = AppConfig(MqttConfig("old", 1883, "", ""))
    repository = BrokerProfileService(initial, credential_store=credential_store)
    repository.save = MagicMock()
    replacement = MqttConfig("new", 8883, "observer", "password", True)

    repository.update_mqtt(replacement)

    assert repository.get_mqtt() == replacement
    assert repository.get().mqtt == replacement
    repository.save.assert_called_once_with()


def test_broker_repository_links_workspace_to_active_profile(
    credential_store,
) -> None:
    repository = BrokerProfileService(
        AppConfig(MqttConfig("broker", 1883, "", "")),
        credential_store=credential_store,
    )
    profile = repository.get_profile()
    workspace = repository.get_observer_workspace()
    assert workspace.profile_id == profile.id
    assert profile.workspace_id == workspace.id


def test_broker_profile_service_adds_and_removes_profile_subscription(
    credential_store,
) -> None:
    repository = BrokerProfileService(credential_store=credential_store)
    profile = repository.create_profile(
        "Subscribed",
        MqttConfig("broker", 1883, "", ""),
    )
    assert repository.get_profile_by_name(" subscribed ").id == profile.id
    subscription = Subscription(
        "zigbee2mqtt/#",
        qos=2,
        retain_as_published=True,
        retain_handling=1,
    )

    added = repository.add_subscription(profile.id, subscription)

    assert added is subscription
    assert repository.get_profile(profile.id).workspace.subscriptions == (
        subscription,
    )

    removed = repository.remove_subscription(
        profile.id,
        subscription.topic_filter,
    )

    assert removed == subscription
    assert repository.get_profile(profile.id).workspace.subscriptions == ()


def test_broker_profile_service_rejects_unknown_profile_name(
    credential_store,
) -> None:
    repository = BrokerProfileService(credential_store=credential_store)

    try:
        repository.get_profile_by_name("Missing")
    except KeyError as error:
        assert error.args[0] == "Unknown broker profile: Missing"
    else:
        raise AssertionError("Expected an unknown profile name to be rejected")


def test_broker_profile_service_tests_temporary_mqtt_connection(
    credential_store,
) -> None:
    connection = FakeMqttConnection()
    supplied_configs: list[MqttConfig] = []

    def create_connection(config: MqttConfig) -> FakeMqttConnection:
        supplied_configs.append(config)
        return connection

    repository = BrokerProfileService(
        credential_store=credential_store,
        mqtt_client_factory=create_connection,
    )
    profile = repository.create_profile(
        "Tested",
        MqttConfig("broker", 8883, "observer", "secret", True),
    )

    result = repository.test_profile(profile.id, timeout=2.5)

    assert result is True
    assert supplied_configs == [profile.config]
    assert connection.connect_timeouts == [2.5]
    assert connection.disconnect_count == 1


def test_broker_profile_service_disconnects_after_failed_test(
    credential_store,
) -> None:
    connection = FakeMqttConnection(
        connect_error=ConnectionError("broker unavailable")
    )
    repository = BrokerProfileService(
        credential_store=credential_store,
        mqtt_client_factory=lambda _config: connection,
    )
    profile = repository.create_profile(
        "Unavailable",
        MqttConfig("broker", 1883, "", ""),
    )

    try:
        repository.test_profile(profile.id)
    except ConnectionError as error:
        assert str(error) == "broker unavailable"
    else:
        raise AssertionError("Expected the connection test to fail")

    assert connection.disconnect_count == 1


def test_broker_repository_provides_two_empty_independent_profiles(
    credential_store,
) -> None:
    repository = BrokerProfileService(
        AppConfig(MqttConfig("broker", 1883, "", "")),
        credential_store=credential_store,
    )

    default_profile, local_profile = repository.get_all_profiles()

    assert [profile.name for profile in (default_profile, local_profile)] == [
        "Default",
        "Local MQTT",
    ]
    assert local_profile.config == MqttConfig("localhost", 1883, "", "")
    assert default_profile.workspace.subscriptions == ()
    assert local_profile.workspace.subscriptions == ()


def test_broker_repository_creates_updates_and_deletes_profiles(
    credential_store,
) -> None:
    repository = BrokerProfileService(
        AppConfig(MqttConfig("broker", 1883, "", "")),
        credential_store=credential_store,
    )
    repository.save = MagicMock()

    profile = repository.create_profile(
        "  Remote  ",
        MqttConfig("remote", 8883, "user", "secret", True),
    )

    assert profile.name == "Remote"
    assert profile.workspace.profile_id == profile.id
    assert profile.workspace.subscriptions == ()
    assert credential_store.get_password(profile.id) == "secret"

    profile.name = "Remote TLS"
    profile.config = MqttConfig("remote-new", 8883, "user", "new-secret", True)
    repository.update_profile(profile)

    assert repository.get_profile(profile.id).name == "Remote TLS"
    assert credential_store.get_password(profile.id) == "new-secret"
    deleted = repository.delete_profile(profile.id)
    assert deleted is profile
    assert profile not in repository.get_all_profiles()
    assert credential_store.get_password(profile.id) is None
    assert repository.save.call_count == 3


def test_deleting_active_profile_selects_a_replacement(credential_store) -> None:
    repository = BrokerProfileService(credential_store=credential_store)
    active = repository.get_profile()

    repository.delete_profile(active.id)

    assert repository.get_profile().name == "Local MQTT"
    assert repository.get().mqtt == repository.get_profile().config


def test_broker_profile_service_rejects_deleting_final_profile(
    credential_store,
) -> None:
    repository = BrokerProfileService(credential_store=credential_store)
    default, local = repository.get_all_profiles()
    repository.delete_profile(local.id)

    try:
        repository.delete_profile(default.id)
    except ValueError as error:
        assert str(error) == "The final broker profile cannot be removed."
    else:
        raise AssertionError("Expected final broker profile deletion to fail")


def test_broker_profile_service_rejects_duplicate_names(
    credential_store,
) -> None:
    repository = BrokerProfileService(
        AppConfig(MqttConfig("broker", 1883, "", "")),
        credential_store=credential_store,
    )
    try:
        repository.create_profile(" default ", MqttConfig("other", 1883, "", ""))
    except ValueError as error:
        assert str(error) == "A broker profile with that name already exists."
    else:
        raise AssertionError("Expected a duplicate profile name to be rejected")


def test_creating_passwordless_profile_does_not_mutate_credentials(
    credential_store,
) -> None:
    credentials = MagicMock(wraps=credential_store)
    repository = BrokerProfileService(credential_store=credentials)
    credentials.reset_mock()

    repository.create_profile(
        "Passwordless",
        MqttConfig("broker", 1883, "", ""),
    )

    credentials.set_password.assert_not_called()
    credentials.delete_password.assert_not_called()


def test_profile_summaries_do_not_access_credentials(credential_store) -> None:
    credentials = MagicMock(wraps=credential_store)
    repository = BrokerProfileService(credential_store=credentials)
    repository.create_profile(
        "Authenticated",
        MqttConfig("broker", 8883, "observer", "secret", True),
    )
    credentials.reset_mock()

    summaries = repository.list_profile_summaries()

    assert any(item.name == "Authenticated" for item in summaries)
    credentials.get_password.assert_not_called()
    credentials.set_password.assert_not_called()
    credentials.delete_password.assert_not_called()


def test_clearing_existing_profile_password_deletes_credential(
    credential_store,
) -> None:
    credentials = MagicMock(wraps=credential_store)
    repository = BrokerProfileService(credential_store=credentials)
    profile = repository.create_profile(
        "Authenticated",
        MqttConfig("broker", 1883, "observer", "secret"),
    )
    credentials.reset_mock()

    profile.config = MqttConfig("broker", 1883, "", "")
    repository.update_profile(profile)

    credentials.delete_password.assert_called_once_with(profile.id)


def test_broker_repository_allows_plaintext_credential_mutations(
    credential_store,
) -> None:
    repository = BrokerProfileService(credential_store=credential_store)
    active_profile = repository.get_profile()
    insecure = MqttConfig("broker", 1883, "observer", "secret")

    created = repository.create_profile("Plain MQTT", insecure)
    repository.activate_profile(active_profile.id, insecure)
    active_profile.config = insecure
    repository.update_profile(active_profile)

    assert created.config == insecure
    assert len(repository.get_all_profiles()) == 3
    assert repository.get_mqtt() == insecure
