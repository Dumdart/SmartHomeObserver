from pathlib import Path
from unittest.mock import MagicMock, patch

from topicgate.app.app_dependencies import AppDependencies
from topicgate.app.services.broker_snapshot_service import BrokerSnapshotService
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.mqtt_message import MqttMessage


def test_app_dependencies_uses_os_credentials_for_broker_profiles(
    tmp_path: Path,
) -> None:
    database = MagicMock()
    profile = MagicMock()
    profile.config = MqttConfig("broker", 1883, "observer", "entered-secret")
    profile.workspace.subscriptions = ()
    broker_profiles = MagicMock()
    broker_profiles.get_profile.return_value = profile
    broker_profiles.get_all_profiles.return_value = (profile,)
    credential_store = MagicMock()

    with (
        patch(
            "topicgate.app.app_dependencies.DatabaseContext",
            return_value=database,
        ),
        patch(
            "topicgate.app.app_dependencies.BrokerProfileService",
            return_value=broker_profiles,
        ) as repository_type,
        patch(
            "topicgate.app.app_dependencies.TopicMessageRepository"
        ) as message_type,
        patch(
            "topicgate.app.app_dependencies.ObservationRetentionPolicyRepository"
        ) as retention_type,
        patch("topicgate.app.app_dependencies.ObserverMqttRepository"),
    ):
        dependencies = AppDependencies(
            data_dir=tmp_path,
            credential_store=credential_store,
        )

    repository_type.assert_called_once_with(
        database,
        credential_store=credential_store,
        runtime_state=dependencies.broker_runtime_state,
        topic_messages=dependencies.topic_messages,
    )
    message_type.assert_called_once_with(
        database,
        policy_provider=dependencies.retention_policy.get,
    )
    retention_type.assert_called_once_with(database)
    assert dependencies.service_items == (
        dependencies.persistence,
        dependencies.health_monitor,
        dependencies.runtime,
    )
    assert isinstance(dependencies.snapshot_service, BrokerSnapshotService)
    assert dependencies.snapshot_service._runtime is dependencies.runtime
    assert dependencies.snapshot_service._resolver is dependencies.broker_resolver
    assert dependencies.runtime._observation_query is dependencies.observation_query


def test_live_observations_are_queued_for_persistence(
    tmp_path: Path,
    credential_store,
) -> None:
    dependencies = AppDependencies(
        data_dir=tmp_path,
        credential_store=credential_store,
    )
    profile = dependencies.broker_profiles.get_profile()
    repository = dependencies.broker_runtime_state.repositories[profile.id]

    try:
        repository.handle_message(
            None,
            None,
            MqttMessage("factory/temperature", b"21.5", 1, True),
        )

        stored = tuple(
            current.message
            for current in dependencies.topic_messages.get_current_topics(profile.id)
        )

        assert len(stored) == 1
        assert stored[0].topic == "factory/temperature"
        assert stored[0].payload == b"21.5"
        assert stored[0].observation_id is not None
    finally:
        dependencies.topic_messages.close()
        dependencies._db_context.dispose()
