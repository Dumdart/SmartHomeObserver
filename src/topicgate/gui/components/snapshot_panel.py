from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from topicgate.app.services.broker_snapshot_service import (
    MAX_SNAPSHOT_RESULT_LIMIT,
)
from topicgate.core.payload_limits import MAX_RENDERED_PAYLOAD_BYTES
from topicgate.gui.components.workspace_pane import WorkspacePane
from topicgate.presentation.snapshot_presentation import (
    BrokerSnapshotHealth,
    SnapshotQuery,
)


class SnapshotPanel(WorkspacePane):
    """Broker snapshot inspector with persistent controls and advanced health."""

    apply_requested = Signal(object)
    reset_requested = Signal()
    reconnect_observe_requested = Signal(object)
    validation_failed = Signal(str)
    advanced_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__("Broker snapshot")
        self.setObjectName("snapshotPanel")
        self.setMinimumWidth(0)

        self._advanced_button = QToolButton()
        self._advanced_button.setObjectName("snapshotAdvancedButton")
        self._advanced_button.setText("Advanced")
        self._advanced_button.setCheckable(True)
        self._advanced_button.setAccessibleName(
            "Show advanced snapshot details"
        )
        self._advanced_button.toggled.connect(self._set_advanced_visible)
        self._summary_labels = {
            name: self._summary_label(object_name)
            for name, object_name in (
                ("connection", "snapshotSummaryConnection"),
                ("returned", "snapshotSummaryReturned"),
                ("dropped", "snapshotSummaryDropped"),
                ("completeness", "snapshotSummaryCompleteness"),
            )
        }
        self.header_layout.addWidget(self._summary_labels["connection"])
        self.header_layout.addWidget(self._summary_labels["completeness"])
        self.header_layout.addWidget(self._advanced_button)

        secondary_summary = QHBoxLayout()
        secondary_summary.setSpacing(12)
        secondary_summary.addStretch(1)
        secondary_summary.addWidget(self._summary_labels["returned"])
        secondary_summary.addWidget(self._summary_labels["dropped"])
        self.content_layout.addLayout(secondary_summary)

        self._scope_summary = QLabel()
        self._scope_summary.setObjectName("snapshotScopeSummary")
        self._scope_summary.setTextFormat(Qt.TextFormat.PlainText)
        self._scope_summary.setWordWrap(True)
        self._scope_summary.setStyleSheet("color: #92400e;")
        self.content_layout.addWidget(self._scope_summary)

        controls = QGroupBox("Snapshot filters")
        controls.setObjectName("snapshotControls")
        form = QFormLayout(controls)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._topic_filter = QLineEdit("#")
        self._topic_filter.setObjectName("snapshotTopicFilter")
        self._topic_filter.setAccessibleName("Snapshot topic filter")
        self._maximum_age = QLineEdit()
        self._maximum_age.setObjectName("snapshotMaximumAge")
        self._maximum_age.setAccessibleName("Snapshot maximum age in seconds")
        self._maximum_age.setPlaceholderText("No maximum")
        age_validator = QDoubleValidator(0.0, float("inf"), 3, self)
        age_validator.setLocale(QLocale.c())
        self._maximum_age.setValidator(age_validator)
        self._result_limit = QSpinBox()
        self._result_limit.setObjectName("snapshotResultLimit")
        self._result_limit.setAccessibleName("Snapshot result limit")
        self._result_limit.setRange(1, MAX_SNAPSHOT_RESULT_LIMIT)
        self._payload_limit = QSpinBox()
        self._payload_limit.setObjectName("snapshotPayloadLimit")
        self._payload_limit.setAccessibleName("Snapshot payload rendering limit in bytes")
        self._payload_limit.setRange(0, MAX_RENDERED_PAYLOAD_BYTES)
        self._payload_limit.setSuffix(" bytes")
        form.addRow("Topic filter", self._topic_filter)
        form.addRow("Maximum age (seconds)", self._maximum_age)
        form.addRow("Result limit", self._result_limit)
        form.addRow("Payload rendering limit", self._payload_limit)

        button_row = QHBoxLayout()
        apply_button = QPushButton("Apply")
        apply_button.setObjectName("applySnapshotButton")
        apply_button.setAccessibleName("Apply snapshot filters")
        clear_button = QPushButton("Clear filters")
        clear_button.setObjectName("clearSnapshotFiltersButton")
        clear_button.setAccessibleName("Clear snapshot filters")
        apply_button.clicked.connect(self._emit_apply)
        clear_button.clicked.connect(self._clear_filters)
        button_row.addWidget(apply_button)
        button_row.addWidget(clear_button)
        form.addRow(button_row)

        self._warning = QLabel(
            "Reconnect & observe interrupts and renews the active broker "
            "connection before capturing a new snapshot."
        )
        self._warning.setObjectName("reconnectObserveWarning")
        self._warning.setWordWrap(True)
        self._warning.setStyleSheet("color: #92400e;")
        form.addRow(self._warning)
        observe_button = QPushButton("Reconnect && observe")
        observe_button.setObjectName("reconnectObserveButton")
        observe_button.setAccessibleName("Reconnect & observe")
        observe_button.setProperty("primary", True)
        observe_button.clicked.connect(self._emit_observe)
        form.addRow(observe_button)
        self.content_layout.addWidget(controls)

        self._advanced_content = QWidget()
        self._advanced_content.setObjectName("snapshotAdvancedContent")
        advanced_layout = QVBoxLayout(self._advanced_content)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(6)

        legend = QLabel(
            "Value legend: Live = received during this run. Cached = restored from local storage. "
            "Stale = older than the current observation window. Stored = persisted source."
        )
        legend.setObjectName("snapshotFreshnessLegend")
        legend.setWordWrap(True)
        legend.setTextFormat(Qt.TextFormat.PlainText)
        legend.setAccessibleName("Freshness and source legend")
        advanced_layout.addWidget(legend)

        health_group = QGroupBox("Snapshot health")
        health_group.setObjectName("snapshotHealthPanel")
        health_form = QFormLayout(health_group)
        health_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._health_labels = {
            name: self._label(object_name)
            for name, object_name in (
                ("captured", "snapshotCapturedAt"),
                ("connected", "snapshotConnectedAt"),
                ("observation", "snapshotObservationStartedAt"),
                ("duration", "snapshotObservationDuration"),
                ("returned", "snapshotReturnedCount"),
                ("omitted", "snapshotOmittedCount"),
                ("stale", "snapshotStaleCount"),
                ("truncated", "snapshotTruncatedCount"),
                ("dropped", "snapshotDroppedCount"),
                ("completeness", "snapshotCompletenessStatus"),
            )
        }
        for title, key in (
            ("Captured", "captured"),
            ("Connected", "connected"),
            ("Observation", "observation"),
            ("Observed for", "duration"),
            ("Returned", "returned"),
            ("Omitted", "omitted"),
            ("Stale", "stale"),
            ("Truncated", "truncated"),
            ("Dropped", "dropped"),
            ("Completeness", "completeness"),
        ):
            health_form.addRow(title, self._health_labels[key])
        self._limitations = self._label("snapshotLimitations")
        self._limitations.setWordWrap(True)
        health_form.addRow("Limitations", self._limitations)
        advanced_layout.addWidget(health_group)
        self.content_layout.addWidget(self._advanced_content)
        self.content_layout.addStretch(1)

        self._action_widgets = (
            apply_button,
            clear_button,
            observe_button,
        )
        self._rendered_query = SnapshotQuery()
        self._omitted_count = 0
        self.render_query(SnapshotQuery())
        self.render_connection_status("disconnected")
        self._summary_labels["returned"].setText("Returned 0")
        self._summary_labels["dropped"].setText("Dropped 0")
        self._summary_labels["completeness"].setText("Limited")
        self._set_advanced_visible(False)

    @property
    def is_advanced_visible(self) -> bool:
        return self._advanced_button.isChecked()

    @property
    def query(self) -> SnapshotQuery:
        age_text = self._maximum_age.text().strip()
        try:
            maximum_age = None if not age_text else float(age_text)
        except ValueError as error:
            raise ValueError(
                "Maximum age must be a non-negative number or blank."
            ) from error
        return SnapshotQuery(
            topic_filter=self._topic_filter.text().strip(),
            max_age_seconds=maximum_age,
            result_limit=self._result_limit.value(),
            payload_limit_bytes=self._payload_limit.value(),
        )

    def set_advanced_visible(self, visible: bool) -> None:
        self._advanced_button.setChecked(visible)

    def render_query(self, query: SnapshotQuery) -> None:
        self._rendered_query = query
        self._topic_filter.setText(query.topic_filter)
        self._maximum_age.setText(
            "" if query.max_age_seconds is None else str(query.max_age_seconds)
        )
        self._result_limit.setValue(query.result_limit)
        self._payload_limit.setValue(query.payload_limit_bytes)
        self._render_scope_summary()

    def render_connection_status(self, status: str) -> None:
        value = status.replace("_", " ").title()
        label = self._summary_labels["connection"]
        label.setText(value)
        color = {
            "connected": "#168a55",
            "connecting": "#92400e",
            "reconnecting": "#92400e",
            "disconnected": "#9f2f2f",
        }.get(status.lower(), "#4b5563")
        label.setStyleSheet(f"color: {color};")
        self._update_summary_accessibility()

    def render_health(self, health: BrokerSnapshotHealth) -> None:
        self._omitted_count = health.omitted_count
        values = {
            "captured": health.captured_at_label,
            "connected": health.connected_at_label,
            "observation": health.observation_started_at_label,
            "duration": health.observed_for_label,
            "returned": str(health.returned_count),
            "omitted": str(health.omitted_count),
            "stale": str(health.stale_count),
            "truncated": str(health.truncated_count),
            "dropped": str(health.dropped_message_count),
            "completeness": health.completeness_status,
        }
        for name, value in values.items():
            self._health_labels[name].setText(value)
        self._limitations.setText(
            "\n".join(f"- {item}" for item in health.limitation_labels) or "None"
        )
        self._summary_labels["returned"].setText(
            f"Returned {health.returned_count}"
        )
        self._summary_labels["dropped"].setText(
            f"Dropped {health.dropped_message_count}"
        )
        completeness = self._summary_labels["completeness"]
        completeness.setText(health.completeness_status)
        completeness.setStyleSheet(
            "color: #168a55;"
            if health.completeness_status == "Complete"
            else "color: #92400e;"
        )
        self._update_summary_accessibility()
        self._render_scope_summary()

    def set_busy(self, busy: bool) -> None:
        for widget in self._action_widgets:
            widget.setEnabled(not busy)

    def _set_advanced_visible(self, visible: bool) -> None:
        self._advanced_content.setVisible(visible)
        self._advanced_button.setText(
            "Hide advanced" if visible else "Advanced"
        )
        self._advanced_button.setAccessibleName(
            "Hide advanced snapshot details"
            if visible
            else "Show advanced snapshot details"
        )
        self.advanced_changed.emit(visible)

    def _update_summary_accessibility(self) -> None:
        summary = ", ".join(
            label.text() for label in self._summary_labels.values()
        )
        self._advanced_button.setAccessibleDescription(f"{summary}.")

    def _render_scope_summary(self) -> None:
        query = self._rendered_query
        bounds: list[str] = []
        if query.topic_filter != "#":
            bounds.append(f"filter {query.topic_filter}")
        if query.max_age_seconds is not None:
            bounds.append(f"maximum age {query.max_age_seconds:g}s")
        if query.result_limit != SnapshotQuery().result_limit:
            bounds.append(f"result limit {query.result_limit}")
        if self._omitted_count:
            bounds.append(f"{self._omitted_count} topic(s) omitted")
        self._scope_summary.setText(
            "Active snapshot bounds: " + ", ".join(bounds)
            if bounds
            else ""
        )
        self._scope_summary.setVisible(bool(bounds))

    def _emit_apply(self) -> None:
        self._emit_query(self.apply_requested)

    def _emit_observe(self) -> None:
        self._emit_query(self.reconnect_observe_requested)

    def _clear_filters(self) -> None:
        query = SnapshotQuery()
        self.render_query(query)
        self.reset_requested.emit()

    def _emit_query(self, signal: Signal) -> None:
        try:
            query = self.query
        except ValueError as error:
            self.validation_failed.emit(str(error))
            return
        signal.emit(query)

    @staticmethod
    def _label(name: str) -> QLabel:
        label = QLabel("-")
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _summary_label(name: str) -> QLabel:
        label = QLabel("-")
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setStyleSheet("color: #4b5563;")
        return label
