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

    def __init__(self, view_model: MainViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_model = view_model
        layout = QVBoxLayout(self)
        description = QLabel(
            "Individual TopicGate-observed receipts, oldest first. This is not authoritative "
            "broker history. Enable recording per broker in History settings."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.broker = QComboBox()
        self.broker.setObjectName("eventHistoryBroker")
        self.broker.setAccessibleName("Event history broker")
        for broker in view_model.broker_profiles:
            self.broker.addItem(broker.name, broker.id)
        self.topic_filter = QLineEdit("#")
        self.topic_filter.setObjectName("eventHistoryTopicFilter")
        self.topic_filter.setAccessibleName("Event history MQTT topic filter")
        form.addRow("Broker", self.broker)
        form.addRow("MQTT topic filter", self.topic_filter)
        self.after_enabled, self.after = self._time_filter(form, "After")
        self.before_enabled, self.before = self._time_filter(form, "Before")
        self.limit = QSpinBox()
        self.limit.setRange(1, 500)
        self.limit.setValue(100)
        self.limit.setObjectName("eventHistoryLimit")
        self.limit.setAccessibleName("Event history page size")
        form.addRow("Events per page", self.limit)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.search = QPushButton("Search")
        self.refresh = QPushButton("Refresh snapshot")
        self.next_page = QPushButton("Next page")
        self.next_page.setObjectName("eventHistoryNextPage")
        self.next_page.setEnabled(False)
        self.search.clicked.connect(lambda: self._request(False))
        self.refresh.clicked.connect(lambda: self._request(False))
        self.next_page.clicked.connect(lambda: self._request(True))
        for button in (self.search, self.refresh, self.next_page):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel("Search to read committed event history.")
        self.status.setObjectName("eventHistoryStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.limitations = QPlainTextEdit()
        self.limitations.setObjectName("eventHistoryLimitations")
        self.limitations.setAccessibleName("Event history limitations")
        self.limitations.setReadOnly(True)
        self.limitations.setMaximumHeight(90)
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

    @staticmethod
    def _time_filter(form: QFormLayout, title: str) -> tuple[QCheckBox, QDateTimeEdit]:
        enabled = QCheckBox(f"Use {title.lower()} bound")
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
        self.refresh.setEnabled(not vm.event_history_busy)
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
            status = page.recording
            self.status.setText(
                f"{len(page.events)} events on this page · committed snapshot · "
                f"recording {'enabled' if status.enabled else 'disabled'} · "
                f"pending {status.pending} · dropped {status.dropped} · failed {status.failed}. "
                f"Oldest retained: {page.usage.oldest_received_at or 'none'}."
            )
            self.limitations.setPlainText("\n".join(
                item.replace("follow next_cursor", "use Next page") for item in page.limitations
            ))
        else:
            self.status.setText("Search to read committed event history.")
            self.limitations.clear()
        if vm.event_history_busy:
            self.status.setText("Loading committed event history…")
        elif vm.event_history_error:
            self.status.setText(vm.event_history_error)

    def _inspect(self) -> None:
        item = self.results.item(self.results.currentRow(), 0)
        if item is None:
            return
        event = item.data(Qt.ItemDataRole.UserRole)
        payload = event.payload_text if event.payload_text is not None else f"base64: {event.payload_base64}"
        self.payload.setPlainText(
            f"Observation {event.observation_id} · {event.provenance.replace('_', ' ')} · QoS {event.qos} · "
            f"retain {event.retain}\nStorage truncated: {event.is_truncated}; "
            f"rendering truncated: {event.rendering_truncated}\n\n{payload}"
        )
