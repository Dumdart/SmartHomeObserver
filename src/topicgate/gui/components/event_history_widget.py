from __future__ import annotations

from datetime import timezone
from typing import TYPE_CHECKING

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from topicgate.gui.main_view_model import MainViewModel


class EventHistoryWidget(QWidget):
    query_requested = Signal(object, str, object, object, object, int)
    recording_status_requested = Signal(object)
    recording_requested = Signal(object, bool)

    def __init__(self, view_model: MainViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._advanced_mode = True
        self._recording_pending = False
        self._recording_loading = False
        layout = QVBoxLayout(self)
        heading = QLabel("Message history")
        heading.setObjectName("workspaceHeading")
        layout.addWidget(heading)
        self.setToolTip(
            "Message receipts saved while recording was enabled, oldest first. "
            "Earlier gaps cannot be recovered. Health failure history and latest stored values are separate."
        )
        form = self._form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.broker = QComboBox()
        self.broker.setObjectName("eventHistoryBroker")
        self.broker.setAccessibleName("Event history broker")
        for broker in view_model.broker_profiles:
            self.broker.addItem(broker.name, broker.id)
        self.topic_filter = QLineEdit("#")
        self.topic_filter.setObjectName("eventHistoryTopicFilter")
        self.topic_filter.setAccessibleName("Event history MQTT topic filter")
        form.addRow("MQTT topic filter", self.topic_filter)
        self.after_enabled, self.after = self._time_filter(form, "After")
        self.before_enabled, self.before = self._time_filter(form, "Before")
        self.limit = QSpinBox()
        self.limit.setRange(1, 500)
        self.limit.setValue(100)
        self.limit.setObjectName("eventHistoryLimit")
        self.limit.setAccessibleName("Event history page size")
        form.addRow("Events per page", self.limit)
        self.recording_status = QLabel("Loading recording status…")
        self.recording_status.setObjectName("eventHistoryRecordingStatus")
        self.recording_status.setTextFormat(Qt.TextFormat.PlainText)
        self.recording_status.setWordWrap(True)
        recording_actions = QHBoxLayout()
        self.enable_recording = QCheckBox("Record messages")
        self.enable_recording.setObjectName("enableEventRecording")
        self.enable_recording.setEnabled(False)
        self.enable_recording.setToolTip(
            "Record future receipts for this broker. Turning recording off keeps saved history."
        )
        self.enable_recording.clicked.connect(self._request_recording)
        self.reload_recording = QPushButton("Retry")
        self.reload_recording.clicked.connect(self.request_recording_status)
        broker_row = QHBoxLayout()
        broker_row.addWidget(QLabel("Broker"))
        broker_row.addWidget(self.broker, 1)
        layout.addLayout(broker_row)
        recording_actions.addWidget(self.enable_recording)
        recording_actions.addWidget(self.recording_status, 1)
        recording_actions.addWidget(self.reload_recording)
        recording_actions.addStretch()
        layout.addLayout(recording_actions)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.search = QPushButton("Search")
        self.search.setProperty("primary", True)
        self.search.setToolTip("Start a new search, including newly saved messages.")
        self.next_page = QPushButton("Next page")
        self.next_page.setObjectName("eventHistoryNextPage")
        self.next_page.setEnabled(False)
        self.search.clicked.connect(lambda: self._request(False))
        self.next_page.clicked.connect(lambda: self._request(True))
        for button in (self.search, self.next_page):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = QLabel("Search saved message history.")
        self.status.setObjectName("eventHistoryStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.limitations = QPlainTextEdit()
        self.limitations.setObjectName("eventHistoryLimitations")
        self.limitations.setAccessibleName("Event history limitations")
        self.limitations.setReadOnly(True)
        self.limitations.setMaximumHeight(90)
        self.limitations.setVisible(False)
        layout.addWidget(self.limitations)
        self.results = QTableWidget(0, 5)
        self.results.setObjectName("eventHistoryResults")
        self.results.setAccessibleName("Observed event history results")
        self.results.setHorizontalHeaderLabels(["Topic", "Received (UTC)", "Original bytes", "Stored bytes", "Truncated"])
        self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.results.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.itemSelectionChanged.connect(self._inspect)
        layout.addWidget(self.results, 2)
        self.payload = QPlainTextEdit()
        self.payload.setObjectName("eventHistoryPayload")
        self.payload.setAccessibleName("Selected event payload and provenance")
        self.payload.setReadOnly(True)
        self.payload.setMaximumHeight(140)
        layout.addWidget(self.payload, 1)
        for signal in (self.broker.currentIndexChanged, self.topic_filter.textChanged,
                       self.after.dateTimeChanged, self.before.dateTimeChanged,
                       self.after_enabled.toggled, self.before_enabled.toggled,
                       self.limit.valueChanged):
            signal.connect(view_model.invalidate_event_history)
        view_model.event_history_changed.connect(self.render)
        view_model.history_settings_changed.connect(self._recording_loaded)
        view_model.operation_state_changed.connect(self.render_recording)
        self.broker.currentIndexChanged.connect(self.request_recording_status)
        self.broker.setCurrentIndex(max(0, self.broker.findData(view_model.active_broker_profile.id)))
        self.render_recording()

    def select_workspace_broker(self) -> None:
        broker_id = self._view_model.active_broker_profile.id
        previous = self.broker.currentData()
        self.broker.blockSignals(True)
        self.broker.clear()
        for profile in self._view_model.broker_profiles:
            self.broker.addItem(profile.name, profile.id)
        self.broker.setCurrentIndex(self.broker.findData(broker_id))
        self.broker.blockSignals(False)
        if previous != broker_id:
            self._view_model.invalidate_event_history()
            self.request_recording_status()

    def _request_recording(self, enabled: bool) -> None:
        self._recording_pending = True
        self.enable_recording.setEnabled(False)
        self.recording_requested.emit(self.broker.currentData(), enabled)
        self.render_recording()

    def _recording_loaded(self) -> None:
        if self._view_model.history_settings_broker == self.broker.currentData():
            self._recording_loading = False
            self._recording_pending = False
        self.render_recording()

    def request_recording_status(self) -> None:
        self._recording_loading = True
        self.render_recording()
        self.recording_status_requested.emit(self.broker.currentData())

    def render_recording(self) -> None:
        vm = self._view_model
        broker = self.broker.currentData()
        status = vm.history_recording_status
        known = vm.history_settings_broker == broker and status is not None and status.broker_id == broker
        busy = vm.is_busy("history-settings") or self._recording_pending
        error = vm.history_settings_error if vm.history_settings_broker == broker else None
        self.enable_recording.setEnabled(known and not busy and not error and not self._recording_loading)
        self.enable_recording.setChecked(bool(known and status.enabled))
        self.reload_recording.setVisible(bool(error) or not known or self._recording_loading)
        self.reload_recording.setEnabled(not busy)
        self.broker.setEnabled(not busy)
        if self._recording_loading:
            message = "Loading recording status…"
        elif error:
            message = error
        elif busy:
            message = "Applying recording settings…"
        elif known:
            message = (
                f"Recording {'enabled' if status.enabled else 'disabled'}"
            )
        else:
            message = "Recording status unavailable"
        self.recording_status.setText(message)

    @staticmethod
    def _time_filter(form: QFormLayout, title: str) -> tuple[QCheckBox, QDateTimeEdit]:
        enabled = QCheckBox(f"Received {title.lower()}")
        editor = QDateTimeEdit(QDateTime.currentDateTimeUtc())
        editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss 'UTC'")
        editor.setCalendarPopup(True)
        editor.setEnabled(False)
        editor.setAccessibleName(f"Event history received {title.lower()} UTC")
        enabled.toggled.connect(editor.setEnabled)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(enabled)
        layout.addWidget(editor, 1)
        form.addRow(title, row)
        return enabled, editor

    def _request(self, continuation: bool) -> None:
        page = self._view_model.event_history_result
        cursor = page.next_cursor if continuation and page is not None else None
        self.query_requested.emit(
            self.broker.currentData(), self.topic_filter.text(),
            self.after.dateTime().toUTC().toPython().replace(tzinfo=timezone.utc) if self.after_enabled.isChecked() else None,
            self.before.dateTime().toUTC().toPython().replace(tzinfo=timezone.utc) if self.before_enabled.isChecked() else None,
            cursor, self.limit.value(),
        )

    def render(self) -> None:
        vm = self._view_model
        page = vm.event_history_result
        self.search.setEnabled(not vm.event_history_busy)
        self.next_page.setEnabled(not vm.event_history_busy and page is not None and page.next_cursor is not None)
        self.results.setRowCount(0 if page is None else len(page.events))
        self.payload.clear()
        if page is not None:
            for index, event in enumerate(page.events):
                values = (event.topic, event.received_at.isoformat(), str(event.payload_size),
                          str(event.stored_payload_bytes),
                          "Yes" if event.is_truncated or event.rendering_truncated else "No")
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, event)
                    self.results.setItem(index, column, item)
            self._render_page_status()
            self.limitations.setPlainText("\n".join(
                item.replace("follow next_cursor", "use Next page") for item in page.limitations
            ))
        else:
            self.status.setText("Search saved message history.")
            self.limitations.clear()
        if vm.event_history_busy:
            self.status.setText("Loading saved event history…")
        elif vm.event_history_error:
            self.status.setText(vm.event_history_error)
        self.limitations.setVisible(bool(self.limitations.toPlainText()))

    def _inspect(self) -> None:
        item = self.results.item(self.results.currentRow(), 0)
        if item is None:
            return
        event = item.data(Qt.ItemDataRole.UserRole)
        payload = event.payload_text if event.payload_text is not None else f"base64: {event.payload_base64}"
        details = (
            f"Observation {event.observation_id} · {event.provenance.replace('_', ' ')} · QoS {event.qos} · "
            f"retain {event.retain}\nStorage truncated: {event.is_truncated}; "
            f"rendering truncated: {event.rendering_truncated}\n\n"
        ) if self._advanced_mode else (
            "Partial payload (truncated).\n\n" if event.is_truncated or event.rendering_truncated else ""
        )
        self.payload.setPlainText(details + payload)

    def set_advanced_mode(self, advanced: bool) -> None:
        self._advanced_mode = advanced
        self._form.setRowVisible(self.limit, advanced)
        for column in (2, 3):
            self.results.setColumnHidden(column, not advanced)
        self._render_page_status()
        self._inspect()

    def _render_page_status(self) -> None:
        page = self._view_model.event_history_result
        if page is None or self._view_model.event_history_busy or self._view_model.event_history_error:
            return
        status = page.recording
        detail = (
            f" · pending {status.pending} · dropped {status.dropped} · failed {status.failed}"
            if self._advanced_mode else ""
        )
        self.status.setText(
            f"{len(page.events)} saved events on this page{detail}. "
            f"Oldest retained: {page.usage.oldest_received_at or 'none'}."
        )
