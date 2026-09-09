from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from topicgate.core.models.history_retention import HistoryRetentionPolicy
from topicgate.infrastructure.repository.history_retention_repository import HistoryRetentionRepository
from test_observation_history_repository import event, history_store


@pytest.mark.parametrize("field,value", [
    ("max_age_seconds", 0), ("max_events_per_broker", True),
    ("max_events_per_topic", -1), ("max_payload_bytes", 0),
    ("prune_batch_size", 501), ("prune_interval_seconds", 0),
])
def test_policy_rejects_invalid_limits(field, value):
    with pytest.raises(ValueError):
        replace(HistoryRetentionPolicy(), **{field: value})


@pytest.mark.parametrize("field,value,reason", [
    ("max_events_per_broker", 2, "broker_count"),
    ("max_events_per_topic", 2, "topic_count"),
    ("max_payload_bytes", 18, "payload_bytes"),
])
def test_count_and_payload_limits_remove_oldest_ties(history_store, field, value, reason):
    db, store, broker = history_store
    retention = HistoryRetentionRepository(db)
    retention.set_policy(replace(HistoryRetentionPolicy(max_age_seconds=None), **{field: value}))
    store.append(tuple(event(broker, number) for number in range(1, 5)))
    result = retention.prune(event(broker).received_at)
    assert result.by_reason == {reason: 2}
    assert [e.observation_id.int for e in store.scan(broker).events] == [3, 4]
    usage = retention.usage(broker)
    assert usage.event_count == 2
    assert usage.payload_bytes == 18
    assert usage.evictions == {reason: 2}
    assert usage.generation == 1


def test_age_boundary_and_composed_attribution_are_bounded(history_store):
    db, store, broker = history_store
    retention = HistoryRetentionRepository(db)
    now = event(broker).received_at
    retention.set_policy(HistoryRetentionPolicy(
        max_age_seconds=10, max_events_per_topic=2, max_events_per_broker=1,
        max_payload_bytes=5, prune_batch_size=2,
    ))
    store.append((event(broker, 1, received_at=now - timedelta(seconds=11)),
                  event(broker, 2, received_at=now - timedelta(seconds=10)),
                  event(broker, 3), event(broker, 4)))
    result = retention.prune(now)
    assert result.by_reason == {"age": 1, "topic_count": 1}
    assert result.pending
    result = retention.prune(now)
    assert result.by_reason == {"broker_count": 1, "payload_bytes": 1}
    assert not retention.prune(now).pending
    assert not store.scan(broker).events


def test_retention_does_not_touch_latest_state_or_recording_settings(history_store):
    from topicgate.core.models.topic_message import TopicMessage
    from topicgate.infrastructure.repository.topic_message_repository import TopicMessageRepository
    from topicgate.infrastructure.repository.history_recording_repository import HistoryRecordingRepository

    db, store, broker = history_store
    item = event(broker)
    latest = TopicMessageRepository(db)
    try:
        latest.record_message(TopicMessage(broker, item.topic, item.payload, item.qos,
                                          item.retain, item.received_at, item.payload_size,
                                          1, item.observation_id))
        latest.flush()
        store.append((item,))
        retention = HistoryRetentionRepository(db)
        retention.prune(item.received_at + timedelta(days=8))
        assert not store.scan(broker).events
        assert latest.get_message(item.observation_id).payload == item.payload
        assert not HistoryRecordingRepository(db).status(broker).enabled
    finally:
        latest.close()


def test_policy_persists_and_database_enforces_bounds(history_store):
    db, _, _ = history_store
    retention = HistoryRetentionRepository(db)
    policy = replace(HistoryRetentionPolicy(), max_events_per_topic=17, prune_batch_size=5)
    retention.set_policy(policy)
    assert HistoryRetentionRepository(db).get_policy() == policy
    with pytest.raises(IntegrityError):
        with db.transaction() as session:
            session.execute(text("UPDATE history_retention_policy SET prune_batch_size=501"))
    assert retention.get_policy() == policy


def test_empty_payloads_are_count_bounded(history_store):
    db, store, broker = history_store
    retention = HistoryRetentionRepository(db)
    retention.set_policy(HistoryRetentionPolicy(max_age_seconds=None, max_events_per_broker=1))
    store.append(tuple(event(broker, n, payload=b"", payload_size=0) for n in range(1, 4)))
    assert retention.prune(event(broker).received_at).by_reason == {"broker_count": 2}


def test_large_age_limit_does_not_overflow_datetime(history_store):
    db, store, broker = history_store
    retention = HistoryRetentionRepository(db)
    retention.set_policy(HistoryRetentionPolicy(max_age_seconds=2**63 - 1))
    store.append((event(broker),))
    assert retention.prune(event(broker).received_at).deleted == 0
    with pytest.raises(ValueError):
        HistoryRetentionPolicy(max_payload_bytes=2**63)


async def test_pruning_failure_is_visible_and_service_stops_without_disposal_race():
    import asyncio
    from threading import Event
    from unittest.mock import Mock
    from topicgate.app.services.history_retention_service import HistoryRetentionService
    from topicgate.core.models.history_retention import HistoryUsage

    attempted = Event()

    def fail(_now):
        attempted.set()
        raise RuntimeError("synthetic failure")

    store = Mock()
    store.prune.side_effect = fail
    store.usage.return_value = HistoryUsage(None, 0, 0, None, None, 0)
    service = HistoryRetentionService(store)
    await service.start()
    assert await asyncio.to_thread(attempted.wait, 2)
    await service.stop()
    assert service.last_error == "History pruning failed; retention enforcement is pending."
    assert service.usage().enforcement_pending
