from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QVBoxLayout, QWidget,
)

from topicgate.core.models.history_retention import HistoryRetentionPolicy
from topicgate.gui.components.quantity_editor import QuantityEditor
from topicgate.presentation.retention_presentation import (
    AgeUnit, ByteUnit, display_age_value, display_byte_value,
    exact_age_seconds, exact_byte_value,
)

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
        advanced_limits = QFormLayout()
        self.advanced = QPushButton("Advanced pruning settings")
        self.advanced.setCheckable(True)
        self.advanced.setObjectName("historyAdvancedPruning")
        self.advanced_content = QWidget()
        self.advanced_content.setLayout(advanced_limits)
        self.advanced_content.setVisible(False)
        self.advanced.toggled.connect(self.advanced_content.setVisible)
        self.fields: dict[str, QLineEdit] = {}
        self.quantities = {}
        self.unlimited = {}
        for name, label in (
            ("max_age_seconds", "Maximum age"),
            ("max_events_per_broker", "Maximum events per broker"),
            ("max_events_per_topic", "Maximum events per topic"),
            ("max_payload_bytes", "Stored payload across all brokers"),
            ("prune_batch_size", "Pruning batch size (1–500)"),
            ("prune_interval_seconds", "Idle pruning interval (seconds)"),
        ):
            editor = QLineEdit()
            field = editor
            if name in ("max_age_seconds", "max_payload_bytes"):
                units = AgeUnit if name == "max_age_seconds" else ByteUnit
                field = QuantityEditor(tuple(unit.value for unit in units), f"history_{name}")
                self.quantities[name] = field
                editor = field.value
                field.changed.connect(self._validate)
            editor.setObjectName(f"history_{name}")
            editor.setAccessibleName(label)
            editor.textChanged.connect(self._validate)
            self.fields[name] = editor
            if name in ("max_age_seconds", "max_events_per_topic"):
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                unlimited = QCheckBox("Unlimited")
                unlimited.setObjectName(f"history_{name}Unlimited")
                unlimited.toggled.connect(lambda checked, field=field: field.setEnabled(not checked))
                unlimited.toggled.connect(self._validate)
                self.unlimited[name] = unlimited
                row_layout.addWidget(unlimited)
                row_layout.addWidget(field, 1)
                field = row
            target_form = advanced_limits if name.startswith("prune_") else limits
            target_form.addRow(label, field)
        layout.addLayout(limits)
        layout.addWidget(self.advanced)
        layout.addWidget(self.advanced_content)
        self.error = QLabel()
        self.error.setObjectName("historySettingsError")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #b91c1c;")
        self.feedback = QLabel()
        self.feedback.setObjectName("historySettingsFeedback")
        self.feedback.setWordWrap(True)
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
        layout.addWidget(self.feedback)
        layout.addWidget(self.summary)
        layout.addWidget(self.save)
        layout.addWidget(self.reload)
        layout.addStretch()
        self.broker.currentIndexChanged.connect(self._broker_changed)
        view_model.history_settings_changed.connect(self.render)
        view_model.operation_state_changed.connect(self._validate)
        self.broker.setCurrentIndex(max(0, self.broker.findData(view_model.active_broker_profile.id)))

    def draft_policy(self) -> HistoryRetentionPolicy:
        values = {}
        for name, editor in self.fields.items():
            value = editor.text().strip()
            if name in self.unlimited and self.unlimited[name].isChecked():
                values[name] = None
            else:
                try:
                    values[name] = int(value)
                except ValueError as error:
                    raise ValueError(f"{editor.accessibleName()}: enter a positive integer.") from error
                if name == "max_age_seconds":
                    values[name] = exact_age_seconds(values[name], AgeUnit(self.quantities[name].unit.currentText()))
                elif name == "max_payload_bytes":
                    values[name] = exact_byte_value(values[name], ByteUnit(self.quantities[name].unit.currentText()))
        return HistoryRetentionPolicy(**values)

    def render(self) -> None:
        vm = self._view_model
        if vm.history_settings_broker != self.broker.currentData():
            return
        self.feedback.setText(vm.history_settings_feedback)
        self._loaded = vm.history_policy is not None
        if vm.history_policy is not None:
            for name, editor in self.fields.items():
                value = getattr(vm.history_policy, name)
                if name in self.unlimited:
                    self.unlimited[name].setChecked(value is None)
                if value is not None and name in self.quantities:
                    value, unit = display_age_value(value) if name == "max_age_seconds" else display_byte_value(value)
                    self.quantities[name].unit.setCurrentText(unit.value)
                editor.setText("" if value is None else str(value))
        status = vm.history_recording_status
        usage = vm.history_usage
        if status is not None and status.broker_id != self.broker.currentData():
            status, usage = None, None
        self.enabled.setEnabled(self._loaded)
        self.enabled.setChecked(status.enabled if status is not None else False)
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
        else:
            self.summary.setText("History usage is unavailable for this broker. Reload to retry.")
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
            busy = self._view_model.is_busy("history-settings")
            self.save.setEnabled(self._loaded and not busy)
            self.save.setText("Applying…" if busy else "Apply history settings")
            self.broker.setEnabled(not busy)
            if self._view_model.history_settings_broker == self.broker.currentData():
                if self._view_model.history_settings_error:
                    self.error.setText(self._view_model.history_settings_error)

    def _broker_changed(self) -> None:
        self._loaded = False
        self.save.setEnabled(False)
        self.enabled.setEnabled(False)
        self.feedback.clear()
        self.summary.setText("Loading history settings for this broker…")
        self.load_requested.emit(self.broker.currentData())

    def request_load(self) -> None:
        self._broker_changed()

    def _save(self) -> None:
        self.save_requested.emit(self.broker.currentData(), self.enabled.isChecked(), self.draft_policy())
