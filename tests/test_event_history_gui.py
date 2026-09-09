import asyncio
from threading import Event
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from topicgate.app.services.history_retention_service import HistoryRetentionService
from topicgate.app.services.topic_history_service import TopicHistoryService
from topicgate.core.models.history_recording import HistoryRecordingStatus
from topicgate.core.models.history_retention import HistoryRetentionPolicy, HistoryUsage
from topicgate.gui.components.stored_observations_dialog import StoredObservationsDialog
from topicgate.gui.main_view_model import MainViewModel
from test_gui import FakeGuiRepository, runtime_for
from test_observation_history_repository import event


def make_page(broker):
    from topicgate.core.models.observation_event import HistoryScan

    reader = Mock()
    reader.scan.return_value = HistoryScan((event(broker), event(broker, 2)), 2, False)
    retention = Mock()
    retention.get_policy.return_value = HistoryRetentionPolicy()
    retention.usage.return_value = HistoryUsage(broker, 2, 18, event(broker).received_at, None, 0)
    return TopicHistoryService(reader, HistoryRetentionService(retention),
                               lambda owner: HistoryRecordingStatus(owner, dropped=2)).query(broker, "#", limit=1)


def test_event_page_separates_latest_state_and_resets_cursor_on_edits():
    app = QApplication.instance() or QApplication([])
    vm = MainViewModel(runtime_for(FakeGuiRepository()))
    dialog = StoredObservationsDialog(vm)
    widget = dialog.event_history
    assert dialog.tabs.tabText(0) == "Latest stored state"
    assert dialog.tabs.tabText(1) == "Event history"
    vm.event_history_result = make_page(widget.broker.currentData())
    vm.event_history_changed.emit()
    assert widget.results.rowCount() == 1
    assert widget.next_page.isEnabled()
    assert "disabled" in widget.status.text()
    assert "dropped 2" in widget.status.text()
    assert "incomplete" in widget.limitations.toPlainText()
    widget.results.selectRow(0)
    assert "live receipt" in widget.payload.toPlainText()
    assert "synthetic" in widget.payload.toPlainText()
    widget.topic_filter.setText("home/+")
    assert vm.event_history_result is None
    assert not widget.next_page.isEnabled()
    assert widget.results.rowCount() == 0
    dialog.close()
    app.processEvents()


async def test_stale_async_history_results_are_discarded():
    app = QApplication.instance() or QApplication([])
    runtime = runtime_for(FakeGuiRepository())
    vm = MainViewModel(runtime)
    broker = vm.active_broker_profile.id
    started, release = Event(), Event()

    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return make_page(broker)

    runtime.get_topic_history = delayed
    task = asyncio.create_task(vm.query_event_history(broker, "#"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        vm.invalidate_event_history()
        release.set()
        await task
        assert vm.event_history_result is None
        assert not vm.event_history_busy
    finally:
        release.set()
        await task
        app.processEvents()


async def test_new_page_replaces_previous_page_and_failures_are_visible():
    app = QApplication.instance() or QApplication([])
    runtime = runtime_for(FakeGuiRepository())
    vm = MainViewModel(runtime)
    broker = vm.active_broker_profile.id
    page = make_page(broker)
    runtime.get_topic_history = Mock(return_value=page)
    await vm.query_event_history(broker, "#")
    assert vm.event_history_result == page
    await vm.query_event_history(broker, "#", cursor=page.next_cursor)
    assert vm.event_history_result == page
    assert len(vm.event_history_result.events) == 1
    runtime.get_topic_history.side_effect = ValueError("Invalid history cursor.")
    await vm.query_event_history(broker, "#", cursor="bad")
    assert vm.event_history_result is None
    assert vm.event_history_error == "Invalid history cursor."
    app.processEvents()
