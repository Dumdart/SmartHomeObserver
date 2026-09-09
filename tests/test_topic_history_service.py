import base64
import json
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from topicgate.app.services.history_retention_service import HistoryRetentionService
from topicgate.app.services.topic_history_service import TopicHistoryService, MAX_HISTORY_PAYLOAD_FIELD_BYTES
from topicgate.core.models.history_retention import HistoryRetentionPolicy
from topicgate.infrastructure.repository.history_recording_repository import HistoryRecordingRepository
from topicgate.infrastructure.repository.history_retention_repository import HistoryRetentionRepository
from test_observation_history_repository import event, history_store


def query_service(db, store):
    return TopicHistoryService(store, HistoryRetentionService(HistoryRetentionRepository(db)),
                               HistoryRecordingRepository(db).status)


def test_fixed_snapshot_no_duplicates_or_gaps_with_concurrent_backdated_appends(history_store):
    db, store, broker = history_store
    service = query_service(db, store)
    store.append(tuple(event(broker, n) for n in range(2, 12)))
    page = service.query(broker, "#", limit=3)
    ids = [e.observation_id.int for e in page.events]
    store.append((event(broker, 1), event(broker, 20, received_at=event(broker).received_at - timedelta(days=1)),
                  event(broker, 21, received_at=event(broker).received_at + timedelta(days=1))))
    while page.next_cursor:
        page = service.query(broker, "#", cursor=page.next_cursor, limit=3)
        ids.extend(e.observation_id.int for e in page.events)
    assert ids == list(range(2, 12))
    assert len(service.query(broker, "#").events) == 13


def test_cursor_is_scoped_versioned_and_validated(history_store):
    db, store, broker = history_store
    service = query_service(db, store)
    store.append((event(broker), event(broker, 2)))
    cursor = service.query(broker, "#", limit=1).next_cursor
    for changed in ({"broker_id": uuid4(), "topic_filter": "#"},
                    {"broker_id": broker, "topic_filter": "home/#"},
                    {"broker_id": broker, "topic_filter": "#", "before": event(broker).received_at + timedelta(days=1)}):
        with pytest.raises(ValueError, match="cursor"):
            service.query(**changed, cursor=cursor)
    value = json.loads(base64.urlsafe_b64decode(cursor))
    for key, invalid in (("v", 2), ("w", -1), ("g", True), ("p", ["not a date", "not an ID"])):
        altered = {**value, key: invalid}
        with pytest.raises(ValueError, match="cursor"):
            service.query(broker, "#", cursor=base64.urlsafe_b64encode(json.dumps(altered).encode()).decode())
    for invalid in ("!", "", "a" * 2049, "e30="):
        with pytest.raises(ValueError, match="cursor"):
            service.query(broker, "#", cursor=invalid)


def test_filter_scan_advances_even_with_empty_pages_and_excludes_system_topics(history_store):
    db, store, broker = history_store
    service = query_service(db, store)
    store.append(tuple(event(broker, n, topic="$SYS/load") for n in range(1, 1002)))
    store.append((event(broker, 1002, topic="home/value"),))
    first = service.query(broker, "#")
    assert not first.events
    assert first.next_cursor
    second = service.query(broker, "#", cursor=first.next_cursor)
    assert [e.topic for e in second.events] == ["home/value"]
    assert len(service.query(broker, "$SYS/+", limit=500).events) == 500


def test_encoded_payload_budget_does_not_skip_oversized_next_record(history_store):
    db, store, broker = history_store
    service = query_service(db, store)
    store.append(tuple(event(broker, n, payload=b"\x01" * 20000, payload_size=30000, is_truncated=True)
                       for n in range(1, 9)))
    page = service.query(broker, "#")
    ids = []
    while True:
        assert page.rendered_payload_field_bytes <= MAX_HISTORY_PAYLOAD_FIELD_BYTES
        assert all(e.rendering_truncated and e.is_truncated for e in page.events)
        ids.extend(e.observation_id.int for e in page.events)
        if page.next_cursor is None:
            break
        page = service.query(broker, "#", cursor=page.next_cursor)
    assert ids == list(range(1, 9))


def test_retention_changes_remain_visible_on_every_subsequent_page(history_store):
    db, store, broker = history_store
    service = query_service(db, store)
    store.append(tuple(event(broker, n) for n in range(1, 6)))
    first = service.query(broker, "#", limit=1)
    retention = HistoryRetentionRepository(db)
    retention.set_policy(HistoryRetentionPolicy(max_age_seconds=None, max_events_per_broker=2))
    retention.prune(event(broker).received_at)
    page = service.query(broker, "#", cursor=first.next_cursor, limit=1)
    assert page.events[0].observation_id.int == 4
    assert any("during pagination" in item for item in page.limitations)
    page = service.query(broker, "#", cursor=page.next_cursor, limit=1)
    assert any("during pagination" in item for item in page.limitations)
    assert page.recording.enabled is False
    assert "not authoritative" in page.source


def test_time_bounds_binary_payload_and_invalid_requests(history_store):
    db, store, broker = history_store
    service = query_service(db, store)
    now = event(broker).received_at
    store.append((event(broker, 1), event(broker, 2, payload=b"\xff", received_at=now + timedelta(seconds=1)),
                  event(broker, 3, received_at=now + timedelta(seconds=2))))
    page = service.query(broker, "home/+", after=now, before=now + timedelta(seconds=2))
    assert len(page.events) == 1
    assert page.events[0].payload_text is None
    assert base64.b64decode(page.events[0].payload_base64) == b"\xff"
    for limit in (0, -1, 501, True):
        with pytest.raises(ValueError):
            service.query(broker, "#", limit=limit)
    with pytest.raises(ValueError):
        service.query(broker, "home/#/bad")
    with pytest.raises(ValueError):
        service.query(broker, "#", after=now.replace(tzinfo=None))


async def test_mcp_history_is_passive_and_available_read_only(tmp_path, credential_store):
    from fastmcp import Client, FastMCP
    from topicgate.app.app_dependencies import AppDependencies
    from topicgate.mcp.api.topic_api import TopicAPI

    dependencies = AppDependencies(data_dir=tmp_path, credential_store=credential_store)
    broker = dependencies.runtime.active_broker
    try:
        dependencies.history_repository.append((event(broker.id),))
        api = TopicAPI(dependencies.runtime, dependencies.broker_resolver)
        mcp = FastMCP("history-test")
        api.register(mcp)
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert tools["get_topic_history"].annotations.readOnlyHint
            result = await client.call_tool("get_topic_history", {"broker": broker.name, "topic_filter": "#"})
            assert not result.is_error
            data = result.structured_content
            assert data["recording"]["enabled"] is False
            assert len(data["events"]) == 1
            assert data["events"][0]["provenance"] == "live_receipt"
        assert not dependencies.runtime.active_repo._mqtt_gate.is_started
    finally:
        dependencies.history_recording.close()
        dependencies.topic_messages.close()
        dependencies._db_context.dispose()
