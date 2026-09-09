from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from topicgate.core.models.history_retention import HistoryRetentionPolicy

if TYPE_CHECKING:
    from topicgate.gui.main_view_model import MainViewModel


class HistorySettingsWidget(QWidget):
    load_requested = Signal(object)
    save_requested = Signal(object, bool, object)

    def __init__(self, view_model: MainViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._loaded = False
        layout = QVBoxLayout(self)
        warning = QLabel("Large history retention limits may slow startup and history queries.")
        warning.setObjectName("historyRetentionWarning")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        form = QFormLayout()
        self.broker = QComboBox()
        self.broker.setObjectName("historyRecordingBroker")
        self.broker.setAccessibleName("History recording broker")
        for broker in view_model.broker_profiles:
            self.broker.addItem(broker.name, broker.id)
        self.enabled = QCheckBox("Record new events for this broker (opt-in)")
        self.enabled.setObjectName("historyRecordingEnabled")
        form.addRow("Broker", self.broker)
        form.addRow(self.enabled)
        layout.addLayout(form)
        description = QLabel(
            "Limits below apply to event history across all brokers. Disabling recording "
            "keeps saved history available and continues pruning. Size measures stored "
            "payload bytes, not the SQLite file. Latest-state retention is separate."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        limits = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for name, label in (
            ("max_age_seconds", "Maximum age (seconds; blank = unlimited)"),
            ("max_events_per_broker", "Maximum events per broker"),
            ("max_events_per_topic", "Maximum events per topic (blank = unlimited)"),
            ("max_payload_bytes", "Global history payload budget (bytes)"),
            ("prune_batch_size", "Pruning batch size (1–500)"),
            ("prune_interval_seconds", "Idle pruning interval (seconds)"),
        ):
            editor = QLineEdit()
            editor.setObjectName(f"history_{name}")
            editor.setAccessibleName(label)
            editor.textChanged.connect(self._validate)
            self.fields[name] = editor
            limits.addRow(label, editor)
        layout.addLayout(limits)
        self.error = QLabel()
        self.error.setObjectName("historySettingsError")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #b91c1c;")
        self.summary = QLabel("Load history settings to inspect recording and storage usage.")
        self.summary.setObjectName("historyUsageSummary")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.save = QPushButton("Apply history settings")
        self.save.setObjectName("saveHistorySettings")
        self.save.setEnabled(False)
        self.save.clicked.connect(self._save)
        self.reload = QPushButton("Reload settings and usage")
        self.reload.clicked.connect(lambda: self.load_requested.emit(self.broker.currentData()))
        layout.addWidget(self.error)
        layout.addWidget(self.summary)
        layout.addWidget(self.save)
        layout.addWidget(self.reload)
        layout.addStretch()
        self.broker.currentIndexChanged.connect(self._broker_changed)
        view_model.history_settings_changed.connect(self.render)

    def draft_policy(self) -> HistoryRetentionPolicy:
        values = {}
        for name, editor in self.fields.items():
            value = editor.text().strip()
            if not value and name in ("max_age_seconds", "max_events_per_topic"):
                values[name] = None
            else:
                try:
                    values[name] = int(value)
                except ValueError as error:
                    raise ValueError(f"{editor.accessibleName()}: enter a positive integer.") from error
        return HistoryRetentionPolicy(**values)

    def render(self) -> None:
        vm = self._view_model
        if vm.history_settings_broker != self.broker.currentData():
            return
        self._loaded = vm.history_policy is not None
        if vm.history_policy is not None:
            for name, editor in self.fields.items():
                value = getattr(vm.history_policy, name)
                editor.setText("" if value is None else str(value))
        status = vm.history_recording_status
        usage = vm.history_usage
        if status is not None:
            self.enabled.setChecked(status.enabled)
        if status is not None and usage is not None:
            self.summary.setText(
                f"Broker history: {usage.event_count:,} events; {usage.payload_bytes:,} payload bytes. "
                f"Committed: {status.committed:,}; pending: {status.pending:,}; "
                f"dropped: {status.dropped:,}; failed: {status.failed:,}. "
                f"Prior unclean session: {'yes' if status.previous_unclean else 'no'}. "
                f"Last prune: {usage.last_pruned_at or 'never'}. "
                f"Global evictions by limit: {usage.evictions}. "
                f"Enforcement pending: {'yes' if usage.enforcement_pending else 'no'}."
            )
        self._validate()
        if vm.history_settings_error:
            self.error.setText(vm.history_settings_error)

    def _validate(self) -> None:
        if not hasattr(self, "save"):
            return
        try:
            self.draft_policy()
        except ValueError as error:
            self.error.setText(str(error))
            self.save.setEnabled(False)
        else:
            self.error.clear()
            self.save.setEnabled(self._loaded)

    def _broker_changed(self) -> None:
        self._loaded = False
        self.save.setEnabled(False)
        self.load_requested.emit(self.broker.currentData())

    def _save(self) -> None:
        self.save_requested.emit(self.broker.currentData(), self.enabled.isChecked(), self.draft_policy())
