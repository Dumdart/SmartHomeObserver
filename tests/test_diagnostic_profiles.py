import json
from dataclasses import replace
from uuid import uuid4

import pytest

from topicgate.app.services.diagnostic_profile_service import DiagnosticProfileService
from topicgate.core.interfaces import PackReference
from topicgate.core.models.diagnostic_profile import (
    DiagnosticProfile,
    default_profile_id,
    expectation_id_for_rule,
    normalize_profile_name,
    normalize_rule_id,
)
from topicgate.core.models.health import EqualCondition, HealthExpectation, HealthSeverity, TopicTarget
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.models import MqttConfigRow
from topicgate.infrastructure.diagnostic_packs import DiagnosticPackRegistry, load_zigbee2mqtt_pack
from topicgate.infrastructure.repository.broker_repository import BrokerRepository
from topicgate.infrastructure.repository.diagnostic_profile_repository import SqlDiagnosticProfileRepository
from topicgate.infrastructure.repository.health_expectation_repository import HealthExpectationRepository


def _broker(database, name):
    with database.transaction() as session:
        config = MqttConfigRow("localhost", 1883, "", False)
        session.add(config)
        session.flush()
        return BrokerRepository(database).create_profile(name, config.id, session=session).id


def _service(tmp_path):
    database = DatabaseContext(f"sqlite:///{tmp_path / 'profiles.db'}")
    registry = DiagnosticPackRegistry((load_zigbee2mqtt_pack(),))
    expectations = HealthExpectationRepository(database, registry)
    return database, DiagnosticProfileService(
        SqlDiagnosticProfileRepository(database), expectations, registry, database
    ), expectations


def _rule(broker_id, rule_id="status"):
    return HealthExpectation(
        uuid4(), 1, True, HealthSeverity.CRITICAL,
        TopicTarget(broker_id, "devices/status"), EqualCondition(b"online"),
        frozenset(), name="Status", rule_id=rule_id,
    )


def test_name_rule_validation_and_stable_ids():
    assert normalize_profile_name("  Kitchen  ") == ("Kitchen", "kitchen")
    assert normalize_rule_id("Bridge.Online-1") == "bridge.online-1"
    profile_id = uuid4()
    assert expectation_id_for_rule(profile_id, "A") == expectation_id_for_rule(profile_id, "a")
    with pytest.raises(ValueError): normalize_profile_name(" \n ")
    with pytest.raises(ValueError): normalize_rule_id("bad id")


def test_broker_creation_always_creates_reserved_default(tmp_path):
    database, service, _ = _service(tmp_path)
    try:
        broker_id = _broker(database, "Broker")
        profiles = service.list_profiles(broker_id)
        assert [(item.profile_id, item.name, item.is_default) for item in profiles] == [
            (default_profile_id(broker_id), "Default", True)
        ]
        with pytest.raises(ValueError, match="cannot be deleted"):
            service.delete_profile(broker_id, profiles[0].profile_id)
    finally:
        database.dispose()


def test_custom_and_pack_rules_round_trip_and_hydrate(tmp_path):
    database, service, expectations = _service(tmp_path)
    try:
        broker_id = _broker(database, "Broker")
        profile = service.create_profile(
            broker_id, " Mixed ",
            pack_reference=PackReference("zigbee2mqtt", "1.0.0"),
            custom_rules=(_rule(broker_id, "custom-rule"),),
            pack_overrides={"bridge-online": {"enabled": False}},
        )
        resolved = service.resolve_profile(service.get_profile(broker_id, profile.profile_id))
        assert len(resolved) == 8
        assert next(item for item in resolved if item.rule_id == "bridge-online").enabled is False
        assert expectations.get(expectation_id_for_rule(profile.profile_id, "bridge-online")).name == "Zigbee2MQTT bridge is online"
    finally:
        database.dispose()


def test_export_import_is_deterministic_portable_and_regenerates_ids(tmp_path):
    database, service, _ = _service(tmp_path)
    try:
        source = _broker(database, "Source")
        target = _broker(database, "Target")
        profile = service.create_profile(source, "Portable", custom_rules=(_rule(source),))
        first = service.export_profile(source, profile.profile_id)
        assert first == service.export_profile(source, profile.profile_id)
        assert first.endswith(b"\n")
        document = json.loads(first)
        assert "broker_id" not in json.dumps(document)
        imported = service.import_profile(target, first)
        imported_rule = service.resolve_profile(imported)[0]
        assert imported.profile_id != profile.profile_id
        assert imported_rule.expectation_id != service.resolve_profile(profile)[0].expectation_id
        assert imported_rule.rule_id == "status"
        assert imported_rule.target.broker_id == target
    finally:
        database.dispose()


def test_import_rejects_unknown_fields_before_writing(tmp_path):
    database, service, _ = _service(tmp_path)
    try:
        broker_id = _broker(database, "Broker")
        before = service.list_profiles(broker_id)
        with pytest.raises(ValueError, match="Unknown"):
            service.import_profile(broker_id, '{"kind":"topicgate.diagnostic-profile","extra":1}')
        assert service.list_profiles(broker_id) == before
    finally:
        database.dispose()


def test_pack_change_is_validated_before_update(tmp_path):
    database, service, _ = _service(tmp_path)
    try:
        broker_id = _broker(database, "Broker")
        profile = service.create_profile(broker_id, "Pack", pack_reference=PackReference("zigbee2mqtt", "1.0.0"))
        with pytest.raises(ValueError, match="not installed"):
            service.update_profile(broker_id, replace(profile, pack_reference=PackReference("zigbee2mqtt", "9")))
        assert service.get_profile(broker_id, profile.profile_id).pack_reference.version == "1.0.0"
    finally:
        database.dispose()


def test_rule_level_pack_disable_persists_as_sparse_override(tmp_path):
    database, service, expectations = _service(tmp_path)
    try:
        broker_id = _broker(database, "Broker")
        profile = service.create_profile(
            broker_id, "Pack", pack_reference=PackReference("zigbee2mqtt", "1.0.0")
        )
        rule = next(
            item for item in expectations.list_for_broker(broker_id)
            if item.rule_id == "bridge-online"
        )
        expectations.update(replace(rule, enabled=False))

        restored = expectations.get(rule.expectation_id)
        aggregate = service.get_profile(broker_id, profile.profile_id)
        assert restored.enabled is False
        assert aggregate.pack_overrides == {"bridge-online": {"enabled": False}}
    finally:
        database.dispose()


def test_profile_can_replace_a_pack_rule_with_a_custom_rule_atomically(tmp_path):
    database, service, _ = _service(tmp_path)
    try:
        broker_id = _broker(database, "Broker")
        profile = service.create_profile(
            broker_id, "Pack", pack_reference=PackReference("zigbee2mqtt", "1.0.0")
        )
        replacement = _rule(broker_id, "bridge-online")

        updated = service.update_profile(
            broker_id,
            replace(profile, pack_reference=None, custom_rules=(replacement,)),
        )

        rules = service.resolve_profile(service.get_profile(broker_id, updated.profile_id))
        assert len(rules) == 1
        assert rules[0].source_kind == "custom"
        assert rules[0].rule_id == "bridge-online"
    finally:
        database.dispose()


def test_prepare_and_pack_catalog_are_side_effect_free(tmp_path):
    database, service, expectations = _service(tmp_path)
    try:
        broker_id = _broker(database, "Broker")
        assert service.list_pack_references() == (PackReference("zigbee2mqtt", "1.0.0"),)
        draft = DiagnosticProfile(uuid4(), broker_id, "Draft", custom_rules=(_rule(broker_id),))

        prepared = service.prepare_profile(draft, new_identity=True)

        assert prepared.is_valid
        assert prepared.profile is not None
        assert prepared.expectations[0].expectation_id == expectation_id_for_rule(draft.profile_id, "status")
        assert expectations.list_for_broker(broker_id) == ()
        invalid = service.prepare_profile(replace(draft, pack_reference=PackReference("missing", "1")))
        assert not invalid.is_valid
        assert "not installed" in invalid.errors[0]
    finally:
        database.dispose()
