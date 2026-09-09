import asyncio
from threading import Event
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication, QPushButton

from topicgate.app.services.history_retention_service import HistoryRetentionService
from topicgate.app.services.topic_history_service import TopicHistoryService
from topicgate.core.models.history_recording import HistoryRecordingStatus
from topicgate.core.models.history_retention import HistoryRetentionPolicy, HistoryUsage
from topicgate.gui.components.event_history_widget import EventHistoryWidget
from topicgate.gui.components.stored_observations_dialog import StoredObservationsDialog
from topicgate.gui.components.workspace_pane import WorkspacePane
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


def test_event_history_reuses_the_workspace_pane_design():
    app = QApplication.instance() or QApplication([])
    widget = EventHistoryWidget(MainViewModel(runtime_for(FakeGuiRepository())))

    assert isinstance(widget, WorkspacePane)
    assert widget.property("workspacePane") is True
    assert widget.heading.text() == "Message history"
    assert widget.header_layout.indexOf(widget.heading) == 0

    widget.close()
    app.processEvents()


def test_event_page_separates_latest_state_and_resets_cursor_on_edits():
    app = QApplication.instance() or QApplication([])
    vm = MainViewModel(runtime_for(FakeGuiRepository()))
    dialog = StoredObservationsDialog(vm)
    widget = EventHistoryWidget(vm)
    assert dialog.tabs.tabText(0) == "Latest stored values"
    assert "Event history" not in [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    vm.event_history_result = make_page(widget.broker.currentData())
    vm.event_history_changed.emit()
    assert widget.results.rowCount() == 1
    assert widget.next_page.isEnabled()
    assert "recording" not in widget.status.text()
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


async def test_recording_status_precedes_search_and_enable_preserves_retention():
    app = QApplication.instance() or QApplication([])
    runtime = runtime_for(FakeGuiRepository())
    vm = MainViewModel(runtime)
    dialog = StoredObservationsDialog(vm)
    widget = EventHistoryWidget(vm)
    broker = widget.broker.currentData()
    runtime.get_history_retention_policy = Mock(return_value=HistoryRetentionPolicy())
    runtime.get_history_usage = Mock(return_value=HistoryUsage(broker, 0, 0, None, None, 0))
    runtime.get_history_recording_status = Mock(return_value=HistoryRecordingStatus(broker))
    runtime.update_history_retention_policy = Mock()
    runtime.set_history_recording = Mock()
    await vm.load_history_settings(broker)
    assert vm.event_history_result is None
    assert "Recording disabled" in widget.recording_status.text()
    assert widget.enable_recording.isEnabled()
    widget.request_recording_status()
    vm.operation_state_changed.emit()
    assert not widget.enable_recording.isEnabled()
    assert "Loading" in widget.recording_status.text()
    await vm.load_history_settings(broker)
    requested = []
    widget.recording_requested.connect(lambda broker, enabled: requested.append((broker, enabled)))
    widget.enable_recording.click()
    assert requested == [(broker, True)]
    runtime.get_history_recording_status.return_value = HistoryRecordingStatus(broker, enabled=True)
    await vm.set_history_recording(broker, True)
    runtime.set_history_recording.assert_called_once_with(broker, True)
    runtime.update_history_retention_policy.assert_not_called()
    assert "Recording enabled" in widget.recording_status.text()
    assert widget.enable_recording.isEnabled()
    assert widget.enable_recording.isChecked()
    widget.enable_recording.click()
    assert requested[-1] == (broker, False)
    runtime.get_history_recording_status.return_value = HistoryRecordingStatus(broker)
    await vm.set_history_recording(broker, False)
    assert runtime.set_history_recording.call_args.args == (broker, False)
    runtime.update_history_retention_policy.assert_not_called()
    assert not widget.enable_recording.isChecked()
    widget.broker.setCurrentIndex(1)
    assert not widget.enable_recording.isEnabled()
    assert "Loading" in widget.recording_status.text()
    dialog.close()
    app.processEvents()


async def test_recording_failure_is_visible_without_claiming_success():
    app = QApplication.instance() or QApplication([])
    runtime = runtime_for(FakeGuiRepository())
    vm = MainViewModel(runtime)
    dialog = StoredObservationsDialog(vm)
    widget = EventHistoryWidget(vm)
    broker = widget.broker.currentData()
    runtime.set_history_recording = Mock(side_effect=RuntimeError("unavailable"))
    await vm.set_history_recording(broker, True)
    assert "could not be changed" in widget.recording_status.text()
    assert not widget.enable_recording.isEnabled()
    dialog.close()
    app.processEvents()


def test_history_units_unlimited_and_advanced_pruning_round_trip():
    app = QApplication.instance() or QApplication([])
    vm = MainViewModel(runtime_for(FakeGuiRepository()))
    dialog = StoredObservationsDialog(vm)
    widget = dialog.history_settings
    vm.history_settings_broker = widget.broker.currentData()
    vm.history_policy = HistoryRetentionPolicy()
    widget.render()
    assert widget.draft_policy() == vm.history_policy
    assert not widget.advanced_content.isHidden()
    assert widget.findChild(QPushButton, "historyAdvancedPruning") is None
    widget.unlimited["max_age_seconds"].setChecked(True)
    assert widget.draft_policy().max_age_seconds is None
    assert not widget.quantities["max_age_seconds"].isEnabled()
    widget.unlimited["max_age_seconds"].setChecked(False)
    widget.fields["max_age_seconds"].setText("2")
    widget.quantities["max_age_seconds"].unit.setCurrentText("Days")
    assert widget.draft_policy().max_age_seconds == 172800
    widget.fields["max_payload_bytes"].setText("not a number")
    assert not widget.save.isEnabled()
    previous_broker = widget.broker.currentData()
    widget.broker.setCurrentIndex(1)
    vm.history_settings_broker = widget.broker.currentData()
    vm.history_policy = None
    vm.history_recording_status = HistoryRecordingStatus(previous_broker, enabled=True)
    widget.render()
    assert not widget.enabled.isChecked()
    assert not widget.enabled.isEnabled()
    assert "unavailable for this broker" in widget.summary.text()
    dialog.close()
    app.processEvents()


async def test_workspace_history_navigation_and_recording_are_scoped(tmp_path):
    from PySide6.QtCore import QSettings
    from topicgate.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    runtime = runtime_for(FakeGuiRepository())
    vm = MainViewModel(runtime)
    enabled_by_broker = {}
    runtime.get_history_retention_policy = Mock(return_value=HistoryRetentionPolicy())
    runtime.get_history_usage = Mock(
        side_effect=lambda broker: HistoryUsage(broker, 0, 0, None, None, 0)
    )
    runtime.get_history_recording_status = Mock(
        side_effect=lambda broker: HistoryRecordingStatus(
            broker, enabled=enabled_by_broker.get(broker, False)
        )
    )
    runtime.set_history_recording = Mock(
        side_effect=lambda broker, enabled: enabled_by_broker.update({broker: enabled})
    )
    runtime.update_history_retention_policy = Mock()
    runtime.get_topic_history = Mock(side_effect=lambda broker, *a, **kw: make_page(broker))
    window = MainWindow(vm, QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat))
    tabs = window._destination_tabs
    widget = window._event_history

    async def finish_operations():
        await asyncio.gather(*tuple(window._operation_tasks))

    try:
        assert [tabs.tabText(i) for i in range(tabs.count())] == [
            "Health", "Selected", "Snapshot", "History"
        ]
        assert not tabs.expanding()
        assert not tabs.isTabEnabled(1)
        tabs.setCurrentIndex(3)
        await finish_operations()
        assert window._stored_observations_dialog is None
        assert widget.broker.currentData() == vm.active_broker_profile.id
        assert "Recording disabled" in widget.recording_status.text()
        runtime.set_history_recording.assert_not_called()
        runtime.get_topic_history.assert_not_called()

        widget.enable_recording.click()
        assert not widget.enable_recording.isEnabled()
        widget.enable_recording.click()
        await finish_operations()
        runtime.set_history_recording.assert_called_once_with(vm.active_broker_profile.id, True)
        assert widget.enable_recording.isChecked()
        widget.enable_recording.click()
        await finish_operations()
        assert not widget.enable_recording.isChecked()

        widget.broker.setCurrentIndex(1)
        await finish_operations()
        other = widget.broker.currentData()
        widget.enable_recording.click()
        await finish_operations()
        assert runtime.set_history_recording.call_args.args == (other, True)
        runtime.update_history_retention_policy.assert_not_called()
        widget.search.click()
        await finish_operations()
        assert widget.results.rowCount() == 1

        tabs.setCurrentIndex(2)
        tabs.setCurrentIndex(3)
        await finish_operations()
        assert widget.broker.currentData() == vm.active_broker_profile.id
        assert widget.results.rowCount() == 0
        assert not widget.enable_recording.isChecked()
        assert not widget.next_page.isEnabled()
        assert window._context_panel.isHidden()
    finally:
        await window.cancel_pending_operations()
        window.close()
        app.processEvents()


async def test_late_recording_status_cannot_enable_controls_for_another_broker():
    app = QApplication.instance() or QApplication([])
    runtime = runtime_for(FakeGuiRepository())
    vm = MainViewModel(runtime)
    widget = EventHistoryWidget(vm)
    first = widget.broker.currentData()
    started, release = Event(), Event()

    def read_status(broker):
        if broker == first:
            started.set()
            assert release.wait(5)
        return HistoryRecordingStatus(broker, enabled=broker == first)

    runtime.get_history_recording_status = Mock(side_effect=read_status)
    runtime.get_history_retention_policy = Mock(return_value=HistoryRetentionPolicy())
    runtime.get_history_usage = Mock(
        side_effect=lambda broker: HistoryUsage(broker, 0, 0, None, None, 0)
    )
    task = asyncio.create_task(vm.load_history_settings(first))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        widget.broker.setCurrentIndex(1)
        assert not widget.enable_recording.isEnabled()
        await vm.load_history_settings(widget.broker.currentData())
        release.set()
        await task
        assert widget.enable_recording.isEnabled()
        assert not widget.enable_recording.isChecked()
        assert "Recording disabled" in widget.recording_status.text()
    finally:
        release.set()
        await task
        widget.close()
        app.processEvents()


async def test_recording_status_loads_complete_independently_per_broker():
    app = QApplication.instance() or QApplication([])
    runtime = runtime_for(FakeGuiRepository())
    vm = MainViewModel(runtime)
    workspace = EventHistoryWidget(vm)
    other = EventHistoryWidget(vm)
    first = workspace.broker.currentData()
    other.broker.setCurrentIndex(1)
    second = other.broker.currentData()
    started, release = Event(), Event()

    def read_status(broker):
        if broker == first:
            started.set()
            assert release.wait(5)
        return HistoryRecordingStatus(broker, enabled=broker == first)

    runtime.get_history_recording_status = Mock(side_effect=read_status)
    runtime.get_history_retention_policy = Mock(return_value=HistoryRetentionPolicy())
    runtime.get_history_usage = Mock(
        side_effect=lambda broker: HistoryUsage(broker, 0, 0, None, None, 0)
    )
    task = asyncio.create_task(vm.load_history_settings(first))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await vm.load_history_settings(second)
        assert not workspace._recording_loading
        assert not workspace.enable_recording.isEnabled()
        assert other.enable_recording.isEnabled()
        release.set()
        await task
        assert workspace.enable_recording.isEnabled()
        assert workspace.enable_recording.isChecked()
        assert not other.enable_recording.isChecked()
    finally:
        release.set()
        await task
        workspace.close()
        other.close()
        app.processEvents()


def test_history_mode_switch_preserves_page_selection_and_payload():
    app = QApplication.instance() or QApplication([])
    vm = MainViewModel(runtime_for(FakeGuiRepository()))
    widget = EventHistoryWidget(vm)
    page = make_page(widget.broker.currentData())
    vm.event_history_result = page
    widget.render()
    widget.results.selectRow(0)
    selected = widget.results.currentRow()
    queries, recordings = [], []
    widget.query_requested.connect(lambda *args: queries.append(args))
    widget.recording_requested.connect(lambda *args: recordings.append(args))
    widget.set_advanced_mode(False)
    assert vm.event_history_result is page
    assert widget.results.currentRow() == selected
    assert "Observation " not in widget.payload.toPlainText()
    assert "synthetic" in widget.payload.toPlainText()
    assert "dropped 2" not in widget.status.text()
    assert not widget.limitations.isHidden()
    widget.set_advanced_mode(True)
    assert "Observation " in widget.payload.toPlainText()
    assert "dropped 2" in widget.status.text()
    assert queries == recordings == []
    widget.close()
    app.processEvents()
