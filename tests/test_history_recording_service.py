from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest

from topicgate.app.app_dependencies import AppDependencies
from topicgate.app.services.history_recording_service import HistoryRecordingService
from topicgate.core.models.mqtt_message import MqttMessage
from topicgate.infrastructure.repository.history_recording_repository import HistoryRecordingRepository
from test_observation_history_repository import event, history_store


def test_opt_in_burst_disable_and_restart(history_store):
    db, store, broker = history_store
    settings = HistoryRecordingRepository(db)
    service = HistoryRecordingService(store, settings)
    try:
        service.record(event(broker))
        assert not store.scan(broker).events
        service.set_enabled(broker, True)
        for number in range(1, 201):
            service.record(event(broker, number))
        service.set_enabled(broker, False)
        service.record(event(broker, 201))
        assert len(store.scan(broker).events) == 200
        assert service.status(broker).committed == 200
        assert service.status(broker).pending == 0
        assert not service.status(broker).enabled
    finally:
        service.close()
    restored = HistoryRecordingService(store, settings)
    try:
        assert restored.status(broker).committed == 200
        assert not restored.status(broker).previous_unclean
    finally:
        restored.close()


@pytest.mark.parametrize("max_events,max_bytes", [(2, 1024), (100, 18)])
def test_backpressure_counts_inflight_and_preserves_admitted_events(
    history_store, max_events, max_bytes,
):
    db, store, broker = history_store
    entered, release = Event(), Event()

    def blocked_append(events):
        entered.set()
        assert release.wait(5)
        store.append(events)

    service = HistoryRecordingService(Mock(append=blocked_append), HistoryRecordingRepository(db),
                                      max_events=max_events, max_bytes=max_bytes)
    try:
        service.set_enabled(broker, True)
        service.record(event(broker, 1))
        assert entered.wait(2)
        service.record(event(broker, 2))
        service.record(event(broker, 3))
        assert service.status(broker).pending == 2
        assert service.status(broker).dropped == 1
        with pytest.raises(TimeoutError, match="incomplete"):
            service.flush(timeout=0.01)
        release.set()
        service.flush()
        assert len(store.scan(broker).events) == 2
    finally:
        release.set()
        service.close()


def test_writer_failure_is_visible_and_later_writes_continue(history_store):
    db, store, broker = history_store
    writer = Mock()
    writer.append.side_effect = RuntimeError("synthetic failure")
    service = HistoryRecordingService(writer, HistoryRecordingRepository(db))
    service.set_enabled(broker, True)
    service.record(event(broker))
    service.flush()
    assert service.status(broker).failed == 1
    writer.append.side_effect = store.append
    service.record(event(broker, 2))
    service.flush()
    assert service.status(broker).committed == 1
    with pytest.raises(RuntimeError, match="incomplete"):
        service.close()
    assert HistoryRecordingRepository(db).status(broker).failed == 1


def test_shutdown_timeout_keeps_writer_alive_until_drain(history_store):
    db, store, broker = history_store
    entered, release = Event(), Event()

    def append(events):
        entered.set()
        assert release.wait(5)
        store.append(events)

    service = HistoryRecordingService(Mock(append=append), HistoryRecordingRepository(db))
    service.set_enabled(broker, True)
    service.record(event(broker))
    assert entered.wait(2)
    try:
        with pytest.raises(TimeoutError, match="incomplete"):
            service.close(timeout=0.01)
        assert service.is_alive
    finally:
        release.set()
        service.close()
    assert len(store.scan(broker).events) == 1


def test_unclean_session_survives_restart_as_uncertainty(history_store):
    db, store, broker = history_store
    settings = HistoryRecordingRepository(db)
    settings.set_enabled(broker, True)
    settings.begin_session(broker, uuid4())
    service = HistoryRecordingService(store, settings)
    try:
        assert service.status(broker).previous_unclean
        assert service.status(broker).failed == 0
    finally:
        service.close()


def test_ingestion_uses_same_canonical_event_for_current_health_and_history(
    tmp_path, credential_store,
):
    from dataclasses import replace

    dependencies = AppDependencies(data_dir=tmp_path, credential_store=credential_store)
    broker = dependencies.runtime.active_broker.id
    observer = dependencies.runtime.active_repo
    health = Mock()
    observer.health_sink = health
    observer._retention_policy = lambda: replace(dependencies.retention_policy.get(),
                                               max_payload_bytes_per_topic=4)
    try:
        dependencies.runtime.set_history_recording(broker, True)
        for number in range(30):
            observer.handle_message(None, None, MqttMessage("home/value", b"synthetic", 1, False))
        dependencies.history_recording.flush()
        events = dependencies.history_repository.scan(broker).events
        current = dependencies.runtime.get_current_topic(broker, "home/value").message
        assert len(events) == 30
        assert current.message_count == 30
        assert all(e.payload == b"synt" and e.is_truncated and e.payload_size == 9 for e in events)
        assert {e.observation_id for e in events} == {
            call.args[0].observation_id for call in health.evaluate_observation.call_args_list
        }
        assert current.observation_id in {e.observation_id for e in events}
    finally:
        dependencies.history_recording.close()
        dependencies.topic_messages.close()
        dependencies._db_context.dispose()
