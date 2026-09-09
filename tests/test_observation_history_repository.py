from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import delete, inspect
from sqlalchemy.exc import IntegrityError

from topicgate.app.services.broker_profile_service import BrokerProfileService
from topicgate.core.models.observation_event import ObservationEvent
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.migrations import _alembic_config
from topicgate.infrastructure.database.models.observation_event_row import ObservationEventRow
from topicgate.infrastructure.repository.observation_history_repository import ObservationHistoryRepository


@pytest.fixture
def history_store(tmp_path, credential_store):
    database = DatabaseContext(f"sqlite:///{(tmp_path / 'history.db').as_posix()}")
    brokers = BrokerProfileService(database, credential_store=credential_store)
    broker = brokers.get_profile().id
    try:
        yield database, ObservationHistoryRepository(database), broker
    finally:
        database.dispose()


def event(broker, number=1, **changes):
    return replace(ObservationEvent(
        observation_id=UUID(int=number), broker_id=broker, topic="home/value",
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload=b"synthetic", payload_size=9, qos=1, retain=False,
    ), **changes)


def test_append_preserves_ties_identity_and_snapshot(history_store):
    _, store, broker = history_store
    store.append((event(broker, 2), event(broker, 1)))
    first = store.scan(broker, limit=1)
    assert first.events == (event(broker, 1),)
    assert first.has_more
    store.append((event(broker, 3),))
    second = store.scan(broker, watermark=first.watermark,
                        position=(first.events[0].received_at, first.events[0].observation_id))
    assert second.events == (event(broker, 2),)
    assert not second.has_more
    with pytest.raises(IntegrityError):
        store.append((event(broker, 2, payload=b"changed"),))
    assert len(store.scan(broker).events) == 3


def test_sequence_is_not_reused_after_all_events_deleted(history_store):
    db, store, broker = history_store
    store.append((event(broker),))
    first = store.scan(broker)
    with db.transaction() as session:
        session.execute(delete(ObservationEventRow))
    store.append((event(broker, 2),))
    assert store.scan(broker).watermark > first.watermark
    assert not store.scan(broker, watermark=first.watermark).events


def test_broker_cascade_preserves_other_brokers(history_store, credential_store):
    from topicgate.core.config.mqtt_config import MqttConfig

    db, store, broker = history_store
    brokers = BrokerProfileService(db, credential_store=credential_store)
    other = brokers.create_profile("Other", MqttConfig("localhost", 1883, "", "")).id
    store.append((event(broker), event(other, 2)))
    brokers.select_active_profile(other)
    brokers.delete_profile(broker)
    assert not store.scan(broker).events
    assert store.scan(other).events == (event(other, 2),)


def test_history_migration_indexes_and_downgrade(history_store):
    db, store, broker = history_store
    store.append((event(broker),))
    with db._engine.begin() as connection:
        indexes = {index['name'] for index in inspect(connection).get_indexes("observation_event")}
        assert {"ix_history_topic_order", "ix_history_broker_order", "ix_history_global_order"} <= indexes
        command.downgrade(_alembic_config(connection), "c4d8a7e1f302")
        assert "observation_event" not in inspect(connection).get_table_names()
        assert "mqtt_message" in inspect(connection).get_table_names()
        command.upgrade(_alembic_config(connection), "head")
    assert not store.scan(broker).events


def test_payload_and_provenance_round_trip(history_store):
    _, store, broker = history_store
    item = event(broker, payload=b"\xff", payload_size=100, is_truncated=True, retain=True)
    store.append((item,))
    assert store.scan(broker).events == (item,)
    assert not store.scan(uuid4()).events


def test_non_utc_receipt_is_normalized_before_sqlite_ordering(history_store):
    from datetime import timedelta

    _, store, broker = history_store
    first = event(broker, 1, received_at=datetime(2026, 1, 1, 2, tzinfo=timezone(timedelta(hours=2))))
    second = event(broker, 2)
    store.append((first, second))
    page = store.scan(broker)
    assert [item.observation_id.int for item in page.events] == [1, 2]
    assert page.events[0].received_at == page.events[1].received_at
    with pytest.raises(ValueError, match="timezone"):
        store.append((event(broker, 3, received_at=datetime(2026, 1, 1)),))
    assert len(store.scan(broker).events) == 2


def test_upgrade_does_not_manufacture_events_from_latest_state(history_store):
    from topicgate.core.models.topic_message import TopicMessage
    from topicgate.infrastructure.repository.topic_message_repository import TopicMessageRepository

    db, store, broker = history_store
    item = event(broker)
    latest = TopicMessageRepository(db)
    latest.record_message(TopicMessage(broker, item.topic, item.payload, item.qos,
                                      item.retain, item.received_at, item.payload_size,
                                      1, item.observation_id))
    latest.close()
    with db._engine.begin() as connection:
        command.downgrade(_alembic_config(connection), "c4d8a7e1f302")
        command.upgrade(_alembic_config(connection), "head")
    assert not store.scan(broker).events
    restored = TopicMessageRepository(db)
    try:
        assert restored.get_message(item.observation_id).payload == item.payload
    finally:
        restored.close()
