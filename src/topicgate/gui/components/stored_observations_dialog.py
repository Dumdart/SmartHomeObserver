from datetime import datetime
from uuid import UUID

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDateTimeEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from topicgate.core.models.observation_retention_policy import (
    ObservationRetentionPolicy,
)
from topicgate.core.models.message_filter import OrderType
from topicgate.gui.main_view_model import MainViewModel
from topicgate.gui.components.history_settings_widget import HistorySettingsWidget
from topicgate.gui.components.quantity_editor import QuantityEditor
from topicgate.presentation.retention_presentation import (
    AgeUnit,
    ByteUnit,
    cache_usage_display,
    display_age_value,
    display_byte_value,
    exact_age_seconds,
    exact_byte_value,
)
from topicgate.presentation.snapshot_presentation import datetime_label, size_label



class StoredObservationsDialog(QDialog):
    """Application-wide retention policy and persisted cache administration."""

    save_policy_requested = Signal(object)
    broker_requested = Signal(object)
    deletion_requested = Signal(str, object, object)
    query_requested = Signal(object, str, object, object, object, int)
    refresh_requested = Signal(object)
    message_requested = Signal(object)

    def __init__(self, view_model: MainViewModel, parent=None) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._rendering = False
        self._errors: dict[str, QLabel] = {}
        self.setObjectName("storedObservationsDialog")
        self.setWindowTitle("Stored observations")
        self.resize(900, 680)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("storedObservationsPages")
        self.tabs.tabBar().setObjectName("storedObservationsPages")
        self.tabs.tabBar().setExpanding(False)
        self.tabs.setAccessibleName("Stored observations settings pages")
        self.tabs.addTab(self._history_page(), "Latest stored values")
        self.tabs.addTab(self._retention_page(), "Latest-state retention")
        self.tabs.addTab(self._cache_page(), "Cache administration")
        self.history_settings = HistorySettingsWidget(view_model)
        self.tabs.addTab(self.history_settings, "History settings")
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            title = self.tabs.tabText(index)
            self.tabs.removeTab(index)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidget(page)
            self.tabs.insertTab(index, scroll, title)
        self.tabs.setCurrentIndex(0)
        self.tabs.currentChanged.connect(self._page_changed)
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._view_model.stored_observations_changed.connect(self.render)
        self._view_model.operation_state_changed.connect(self._render_busy)

    def _page_changed(self) -> None:
        if self.tabs.currentIndex() == 3:
            self.history_settings.request_load()

    def _history_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("observationHistoryPage")
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.history_broker = QComboBox()
        self.history_broker.setObjectName("historyBrokerSelection")
        self.history_broker.setAccessibleName("Latest stored state broker")
        self.history_broker.setAccessibleDescription(
            "Select the broker whose persisted observations will be queried."
        )
        self.history_broker.currentIndexChanged.connect(self._history_broker_changed)
        form.addRow("Broker", self.history_broker)
        self.history_topic_filter = QLineEdit("#")
        self.history_topic_filter.setObjectName("historyTopicFilter")
        self.history_topic_filter.setAccessibleName("Latest stored state topic filter")
        self.history_topic_filter.setAccessibleDescription(
            "MQTT topic filter supporting plus and hash wildcards."
        )
        form.addRow("MQTT topic filter", self.history_topic_filter)
        self.history_after_enabled, self.history_after = self._date_time_filter(
            "After", "historyAfter"
        )
        form.addRow("Received after", self._optional_date_time_row(
            self.history_after_enabled, self.history_after
        ))
        self.history_before_enabled, self.history_before = self._date_time_filter(
            "Before", "historyBefore"
        )
        form.addRow("Received before", self._optional_date_time_row(
            self.history_before_enabled, self.history_before
        ))
        self.history_order = QComboBox()
        self.history_order.setObjectName("historySortOrder")
        self.history_order.setAccessibleName("Latest stored state sort order")
        for label, order in (
            ("Received · newest first", OrderType.RECEIVED_DESC),
            ("Received · oldest first", OrderType.RECEIVED_ASC),
            ("Topic · A to Z", OrderType.TOPIC_ASC),
            ("Topic · Z to A", OrderType.TOPIC_DESC),
            ("Message count · highest first", OrderType.MESSAGE_COUNT_DESC),
            ("Message count · lowest first", OrderType.MESSAGE_COUNT_ASC),
            ("Payload size · largest first", OrderType.PAYLOAD_SIZE_DESC),
            ("Payload size · smallest first", OrderType.PAYLOAD_SIZE_ASC),
        ):
            self.history_order.addItem(label, order)
        form.addRow("Sort order", self.history_order)
        self.history_limit = QSpinBox()
        self.history_limit.setObjectName("historyResultLimit")
        self.history_limit.setAccessibleName("Latest stored state result limit")
        self.history_limit.setRange(1, 1000)
        self.history_limit.setValue(50)
        form.addRow("Result limit", self.history_limit)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.query_history = QPushButton("Search")
        self.query_history.setObjectName("queryStoredObservationsButton")
        self.query_history.setAccessibleName("Query stored observations")
        self.query_history.clicked.connect(self._request_query)
        actions.addWidget(self.query_history)
        self.refresh_history = QPushButton("Refresh")
        self.refresh_history.setObjectName("refreshStoredObservationsButton")
        self.refresh_history.setAccessibleName("Refresh stored observation results")
        self.refresh_history.clicked.connect(self._request_refresh)
        actions.addWidget(self.refresh_history)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.history_state = QLabel("Query stored observations to view persisted values.")
        self.history_state.setObjectName("historyQueryState")
        self.history_state.setAccessibleName("Latest stored state query status")
        self.history_state.setTextFormat(Qt.TextFormat.PlainText)
        self.history_state.setWordWrap(True)
        layout.addWidget(self.history_state)
        self.history_results = QTableWidget(0, 6)
        self.history_results.setObjectName("storedObservationResultsTable")
        self.history_results.setAccessibleName("Stored observation query results")
        self.history_results.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.history_results.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.history_results.setHorizontalHeaderLabels(
            ("Topic", "Received", "Payload size", "Messages", "QoS", "Retained")
        )
        self.history_results.itemSelectionChanged.connect(
            self._history_selection_changed
        )
        layout.addWidget(self.history_results, 2)
        self.history_payload_metadata = QLabel("Select an observation to inspect its payload.")
        self.history_payload_metadata.setObjectName("storedObservationPayloadMetadata")
        self.history_payload_metadata.setAccessibleName(
            "Selected stored observation metadata"
        )
        self.history_payload_metadata.setTextFormat(Qt.TextFormat.PlainText)
        self.history_payload_metadata.setWordWrap(True)
        layout.addWidget(self.history_payload_metadata)
        self.history_payload = QPlainTextEdit()
        self.history_payload.setObjectName("storedObservationPayload")
        self.history_payload.setAccessibleName("Selected stored observation payload")
        self.history_payload.setAccessibleDescription(
            "Plain-text rendering of the selected persisted MQTT payload."
        )
        self.history_payload.setReadOnly(True)
        self.history_payload.setPlaceholderText(
            "Select a stored observation to inspect its payload."
        )
        layout.addWidget(self.history_payload, 1)
        return page

    def _retention_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.preset = QComboBox()
        self.preset.setObjectName("retentionPreset")
        self.preset.setAccessibleName("Retention policy preset")
        self.preset.addItems(
            tuple(item.name for item in self._view_model.retention_presets)
            + ("Custom",)
        )
        self.preset.currentTextChanged.connect(self._preset_selected)
        form.addRow("Preset", self.preset)
        self.per_broker_entries = self._integer_field(
            form, "Maximum entries per broker", "maxEntriesPerBroker"
        )
        self.total_entries = self._integer_field(
            form, "Maximum entries across all brokers", "maxEntriesTotal"
        )
        self.per_topic_bytes = self._byte_field(
            form, "Maximum stored payload per topic", "maxPayloadPerTopic"
        )
        self.per_broker_bytes = self._byte_field(
            form, "Maximum stored payload per broker", "maxPayloadPerBroker"
        )
        self.database_bytes = self._byte_field(
            form, "Maximum persisted payload in database", "maxPayloadDatabase"
        )
        age_widget = QWidget()
        age_layout = QHBoxLayout(age_widget)
        age_layout.setContentsMargins(0, 0, 0, 0)
        self.unlimited_age = QCheckBox("Unlimited")
        self.unlimited_age.setObjectName("unlimitedRetentionAge")
        self.unlimited_age.setAccessibleName("Keep observations indefinitely")
        self.maximum_age = QuantityEditor(
            tuple(item.value for item in AgeUnit),
            "maximumRetentionAge",
        )
        age_layout.addWidget(self.unlimited_age)
        age_layout.addWidget(self.maximum_age, 1)
        self._add_error_row(form, "Maximum age", age_widget, "max_age_seconds")
        self.warning_threshold = QSpinBox()
        self.warning_threshold.setObjectName("retentionWarningThreshold")
        self.warning_threshold.setAccessibleName("Retention warning threshold")
        self.warning_threshold.setRange(1, 100)
        self.warning_threshold.setSuffix("%")
        self._add_error_row(
            form,
            "Warning threshold",
            self.warning_threshold,
            "warning_threshold",
        )
        self.remove_expired = QCheckBox("Automatically remove expired observations")
        self.remove_expired.setObjectName("autoRemoveExpired")
        self.remove_expired.setAccessibleName("Automatically remove expired observations")
        self.remove_excess = QCheckBox(
            "Automatically remove observations exceeding count or storage limits"
        )
        self.remove_excess.setObjectName("autoRemoveExcess")
        self.remove_excess.setAccessibleName("Automatically remove excess observations")
        self.remove_unsubscribed = QCheckBox(
            "Automatically remove observations no longer matched by subscriptions"
        )
        self.remove_unsubscribed.setObjectName("autoRemoveUnsubscribed")
        self.remove_unsubscribed.setAccessibleName(
            "Automatically remove unsubscribed observations"
        )
        form.addRow(self.remove_expired)
        form.addRow(self.remove_excess)
        form.addRow(self.remove_unsubscribed)
        self.age_explanation = QLabel()
        self.age_explanation.setObjectName("unlimitedAgeExplanation")
        self.age_explanation.setWordWrap(True)
        form.addRow(self.age_explanation)
        self.save_policy = QPushButton("Preview & save")
        self.save_policy.setObjectName("saveRetentionPolicyButton")
        self.save_policy.setAccessibleName("Preview and save retention policy")
        self.save_policy.clicked.connect(self._request_policy_save)
        form.addRow(self.save_policy)
        layout.addLayout(form)
        layout.addStretch(1)
        for widget in self._draft_widgets():
            if isinstance(widget, QuantityEditor):
                widget.changed.connect(self._draft_changed)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._draft_changed)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._draft_changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._draft_changed)
        self.unlimited_age.toggled.connect(self.maximum_age.setDisabled)
        self._validate_draft()
        return page

    def _cache_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.cache_warning_banner = QLabel()
        self.cache_warning_banner.setObjectName("cacheRetentionWarningBanner")
        self.cache_warning_banner.setWordWrap(True)
        self.cache_warning_banner.setTextFormat(Qt.TextFormat.PlainText)
        self.cache_warning_banner.setAccessibleName("Retention capacity warning")
        self.cache_warning_banner.setStyleSheet(
            "background: #fef3c7; color: #92400e; border: 1px solid #f59e0b; "
            "border-radius: 4px; padding: 6px;"
        )
        self.cache_warning_banner.setVisible(False)
        layout.addWidget(self.cache_warning_banner)
        self.usage_table = QTableWidget(0, 8)
        self.usage_table.setObjectName("cacheUsageTable")
        self.usage_table.setAccessibleName("Cache usage by broker")
        self.usage_table.setHorizontalHeaderLabels(
            (
                "Broker",
                "Status",
                "Entries",
                "Payload",
                "Oldest",
                "Newest",
                "Entry limit",
                "Payload limit",
            )
        )
        layout.addWidget(self.usage_table)
        broker_row = QHBoxLayout()
        broker_row.addWidget(QLabel("Persisted topics for"))
        self.broker = QComboBox()
        self.broker.setObjectName("cacheBrokerSelection")
        self.broker.setAccessibleName("Broker cache selection")
        self.broker.currentIndexChanged.connect(self._broker_changed)
        broker_row.addWidget(self.broker, 1)
        layout.addLayout(broker_row)
        self.broker_storage_summary = QLabel()
        self.broker_storage_summary.setObjectName("selectedBrokerStorageSummary")
        self.broker_storage_summary.setAccessibleName(
            "Selected broker storage summary"
        )
        self.broker_storage_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.broker_storage_summary.setWordWrap(True)
        layout.addWidget(self.broker_storage_summary)
        self.topics = QTableWidget(0, 4)
        self.topics.setObjectName("persistedTopicsTable")
        self.topics.setAccessibleName("Persisted topic observations")
        self.topics.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.topics.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.topics.setHorizontalHeaderLabels(
            ("Topic", "Stored payload", "Received", "Subscription")
        )
        layout.addWidget(self.topics)
        actions = QHBoxLayout()
        for text, name, scope in (
            ("Delete selected cached topics…", "deleteSelectedTopicsButton", "selected_topics"),
            ("Delete unsubscribed…", "deleteUnsubscribedButton", "unsubscribed"),
            ("Delete broker cache…", "deleteBrokerCacheButton", "broker"),
            ("Delete cache for all brokers…", "deleteAllCachesButton", "all_brokers"),
        ):
            button = QPushButton(text)
            button.setObjectName(name)
            button.setProperty("danger", True)
            if scope == "all_brokers":
                button.setToolTip("Delete latest stored values for all brokers. Broker profiles, event history, and failure history are not deleted.")
            button.setAccessibleName(text.rstrip("…"))
            button.clicked.connect(
                lambda _checked=False, selected_scope=scope: (
                    self._request_deletion(selected_scope)
                )
            )
            actions.addWidget(button)
        layout.addLayout(actions)
        return page

    def render(self) -> None:
        self._rendering = True
        try:
            policy = self._view_model.effective_retention_policy
            if policy is not None:
                self._render_policy(policy)
            self._render_usage()
            self._render_brokers()
            self._render_topics()
            self._render_history()
            self._render_busy()
        finally:
            self._rendering = False

    def draft_policy(self) -> ObservationRetentionPolicy:
        values, errors = self._draft_values()
        if errors:
            raise ValueError(next(iter(errors.values())))
        return ObservationRetentionPolicy(**values)

    def selected_broker_id(self) -> UUID:
        return self.broker.currentData()

    def selected_topics(self) -> tuple[str, ...]:
        rows = sorted({index.row() for index in self.topics.selectedIndexes()})
        return tuple(self.topics.item(row, 0).text() for row in rows)

    def _render_policy(self, policy: ObservationRetentionPolicy) -> None:
        self.per_broker_entries.setText(str(policy.max_entries_per_broker))
        self.total_entries.setText(str(policy.max_entries_total))
        self._set_byte_value(self.per_topic_bytes, policy.max_payload_bytes_per_topic)
        self._set_byte_value(
            self.per_broker_bytes, policy.max_payload_bytes_per_broker
        )
        self._set_byte_value(
            self.database_bytes,
            policy.max_persisted_payload_database_bytes_total,
        )
        self.unlimited_age.setChecked(policy.max_age_seconds is None)
        if policy.max_age_seconds is not None:
            value, unit = display_age_value(policy.max_age_seconds)
            self.maximum_age.value.setText(str(value))
            self.maximum_age.unit.setCurrentText(unit.value)
        self.warning_threshold.setValue(round(policy.warning_threshold * 100))
        self.remove_expired.setChecked(policy.auto_remove_expired)
        self.remove_excess.setChecked(policy.auto_remove_excess)
        self.remove_unsubscribed.setChecked(policy.auto_remove_unsubscribed)
        preset = next(
            (
                item.name
                for item in self._view_model.retention_presets
                if item.policy == policy
            ),
            "Custom",
        )
        self.preset.setCurrentText(preset)
        self._validate_draft()

    def _render_usage(self) -> None:
        policy = self._view_model.effective_retention_policy
        if policy is None:
            return
        brokers = {item.id: item for item in self._view_model.broker_profiles}
        rows = self._view_model.cache_usage_summary.brokers
        self.usage_table.setRowCount(len(rows) + 1)
        for row, usage in enumerate(rows):
            broker = brokers[usage.broker_id]
            display = cache_usage_display(usage, policy)
            values = (
                broker.name,
                "Active" if broker.id == self._view_model.active_broker_profile.id else "Inactive",
                str(usage.entry_count),
                size_label(usage.stored_payload_bytes),
                self._optional_datetime(usage.oldest_received_at),
                self._optional_datetime(usage.newest_received_at),
            )
            for column, value in enumerate(values):
                self.usage_table.setItem(row, column, QTableWidgetItem(value))
            self._set_progress(row, 6, display.entry_utilization, display.entry_warning)
            self._set_progress(row, 7, display.payload_utilization, display.payload_warning)
        total = self._view_model.cache_usage_summary
        total_row = len(rows)
        total_values = (
            "All brokers",
            "Global",
            str(total.entry_count),
            size_label(total.stored_payload_bytes),
            self._optional_datetime(total.oldest_received_at),
            self._optional_datetime(total.newest_received_at),
        )
        for column, value in enumerate(total_values):
            self.usage_table.setItem(total_row, column, QTableWidgetItem(value))
        self._set_progress(
            total_row,
            6,
            total.entry_count / policy.max_entries_total,
            total.entry_count / policy.max_entries_total >= policy.warning_threshold,
        )
        self._set_progress(
            total_row,
            7,
            total.stored_payload_bytes
            / policy.max_persisted_payload_database_bytes_total,
            total.stored_payload_bytes
            / policy.max_persisted_payload_database_bytes_total
            >= policy.warning_threshold,
        )
        warnings: list[str] = []
        for usage in rows:
            display = cache_usage_display(usage, policy)
            broker = brokers[usage.broker_id]
            if display.entry_warning:
                warnings.append(
                    f"{broker.name} has reached {display.entry_utilization:.0%} of its entry limit"
                )
            if display.payload_warning:
                warnings.append(
                    f"{broker.name} has reached {display.payload_utilization:.0%} of its payload limit"
                )
        total_entries = total.entry_count / policy.max_entries_total
        total_payload = (
            total.stored_payload_bytes
            / policy.max_persisted_payload_database_bytes_total
        )
        if total_entries >= policy.warning_threshold:
            warnings.append(
                f"all brokers have reached {total_entries:.0%} of the global entry limit"
            )
        if total_payload >= policy.warning_threshold:
            warnings.append(
                f"all brokers have reached {total_payload:.0%} of the global payload limit"
            )
        self.cache_warning_banner.setText(
            "Retention warning: "
            + "; ".join(warnings)
            + ". Review the retention policy before automatic cleanup is needed."
        )
        self.cache_warning_banner.setVisible(bool(warnings))

    def _render_brokers(self) -> None:
        selected = self._view_model.stored_observations_broker_id
        for combo in (self.broker, self.history_broker):
            combo.blockSignals(True)
            combo.clear()
            for broker in self._view_model.broker_profiles:
                status = (
                    "active"
                    if broker.id == self._view_model.active_broker_profile.id
                    else "inactive"
                )
                combo.addItem(f"{broker.name} ({status})", broker.id)
            index = combo.findData(selected)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)
        summary = self._view_model.broker_cache_usage_summary
        self.broker_storage_summary.setText(
            f"Selected broker: {summary.entry_count} observations, "
            f"{size_label(summary.stored_payload_bytes)} stored payload; "
            f"oldest {self._optional_datetime(summary.oldest_received_at)}, "
            f"newest {self._optional_datetime(summary.newest_received_at)}."
        )

    def _render_topics(self) -> None:
        topics = self._view_model.persisted_topics
        self.topics.setRowCount(len(topics))
        for row, topic in enumerate(topics):
            values = (
                topic.topic,
                size_label(topic.stored_payload_bytes),
                datetime_label(topic.received_at),
                "Subscribed" if topic.is_subscribed else "Unsubscribed",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.topics.setItem(row, column, item)

    def _render_history(self) -> None:
        query = self._view_model.stored_observation_filter
        self.history_topic_filter.setText(query.topic_filter)
        order_index = self.history_order.findData(query.order)
        if order_index >= 0:
            self.history_order.setCurrentIndex(order_index)
        self.history_limit.setValue(query.limit)
        self._set_optional_date_time(
            self.history_after_enabled,
            self.history_after,
            query.after,
        )
        self._set_optional_date_time(
            self.history_before_enabled,
            self.history_before,
            query.before,
        )
        results = self._view_model.stored_observation_results
        self.history_results.setRowCount(len(results))
        for row, message in enumerate(results):
            values = (
                message.topic,
                datetime_label(message.received_at),
                size_label(message.payload_size),
                str(message.message_count),
                str(message.qos),
                "Yes" if message.retain else "No",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, message.observation_id)
                self.history_results.setItem(row, column, item)
        detail = self._view_model.selected_stored_observation_detail
        if detail.has_value:
            self.history_payload_metadata.setText(
                f"{detail.topic} · {detail.received_at} · QoS {detail.qos_label} · "
                f"retained {detail.retain_label} · {detail.payload_size_label}"
            )
            self.history_payload.setPlainText(detail.decoded_payload)
        else:
            self.history_payload_metadata.setText(
                "Select an observation to inspect its payload."
            )
            self.history_payload.clear()

    def _render_busy(self) -> None:
        busy = self._view_model.is_busy("stored-observations")
        self.tabs.setEnabled(not busy)
        self.query_history.setEnabled(not busy)
        self.refresh_history.setEnabled(not busy)
        if busy:
            self.history_state.setText("Loading stored observations…")
        elif self._view_model.stored_observation_error:
            self.history_state.setText(
                "Could not query stored observations: "
                f"{self._view_model.stored_observation_error}"
            )
        else:
            count = len(self._view_model.stored_observation_results)
            self.history_state.setText(
                f"{count} stored observation{'s' if count != 1 else ''} returned."
                if count
                else "No stored observations match this query."
            )

    def _preset_selected(self, name: str) -> None:
        if self._rendering or name == "Custom":
            return
        preset = next(item for item in self._view_model.retention_presets if item.name == name)
        self._rendering = True
        try:
            self._render_policy(preset.policy)
        finally:
            self._rendering = False
        self._view_model.log_message.emit(f"Retention preset selected: {name}.")

    def _draft_changed(self, *_args) -> None:
        if self._rendering:
            return
        self.preset.blockSignals(True)
        self.preset.setCurrentText("Custom")
        self.preset.blockSignals(False)
        self._validate_draft()

    def _validate_draft(self) -> None:
        for label in self._errors.values():
            label.clear()
        values, errors = self._draft_values()
        if not errors:
            errors = self._view_model.validate_retention_policy_draft(values)
        for name, message in errors.items():
            self._errors.get(name, self._errors["form"]).setText(message)
        self.save_policy.setEnabled(not errors)
        self.age_explanation.setText(
            "Expired-state removal is configured but inactive while maximum age is unlimited."
            if self.unlimited_age.isChecked() and self.remove_expired.isChecked()
            else ""
        )

    def _draft_values(self) -> tuple[dict[str, object], dict[str, str]]:
        errors: dict[str, str] = {}

        def integer(name: str, widget: QLineEdit) -> int:
            try:
                return self._positive_int(widget)
            except ValueError as error:
                errors[name] = str(error)
                return 0

        def byte_limit(name: str, editor: QuantityEditor) -> int:
            try:
                return self._byte_value(editor)
            except ValueError as error:
                errors[name] = str(error)
                return 0

        maximum_age = None
        if not self.unlimited_age.isChecked():
            try:
                maximum_age = exact_age_seconds(
                    self._positive_int(self.maximum_age.value),
                    AgeUnit(self.maximum_age.unit.currentText()),
                )
            except ValueError as error:
                errors["max_age_seconds"] = str(error)
                maximum_age = 0
        values: dict[str, object] = {
            "max_entries_per_broker": integer(
                "max_entries_per_broker", self.per_broker_entries
            ),
            "max_entries_total": integer("max_entries_total", self.total_entries),
            "max_payload_bytes_per_topic": byte_limit(
                "max_payload_bytes_per_topic", self.per_topic_bytes
            ),
            "max_payload_bytes_per_broker": byte_limit(
                "max_payload_bytes_per_broker", self.per_broker_bytes
            ),
            "max_persisted_payload_database_bytes_total": byte_limit(
                "max_persisted_payload_database_bytes_total", self.database_bytes
            ),
            "max_age_seconds": maximum_age,
            "warning_threshold": self.warning_threshold.value() / 100,
            "auto_remove_expired": self.remove_expired.isChecked(),
            "auto_remove_excess": self.remove_excess.isChecked(),
            "auto_remove_unsubscribed": self.remove_unsubscribed.isChecked(),
        }
        return values, errors

    def _request_policy_save(self) -> None:
        try:
            policy = self.draft_policy()
        except ValueError as error:
            self._errors["form"].setText(str(error))
            return
        self.save_policy_requested.emit(policy)

    def _broker_changed(self) -> None:
        if not self._rendering and self.broker.currentData() is not None:
            self.broker_requested.emit(self.broker.currentData())

    def _history_broker_changed(self) -> None:
        if not self._rendering and self.history_broker.currentData() is not None:
            self.broker_requested.emit(self.history_broker.currentData())

    def _request_query(self) -> None:
        broker_id = self.history_broker.currentData()
        if broker_id is None:
            return
        self.query_requested.emit(
            broker_id,
            self.history_topic_filter.text(),
            self._optional_date_time_value(
                self.history_after_enabled,
                self.history_after,
            ),
            self._optional_date_time_value(
                self.history_before_enabled,
                self.history_before,
            ),
            self.history_order.currentData(),
            self.history_limit.value(),
        )

    def _request_refresh(self) -> None:
        broker_id = self.history_broker.currentData()
        if broker_id is not None:
            self.refresh_requested.emit(broker_id)

    def _history_selection_changed(self) -> None:
        if self._rendering:
            return
        row = self.history_results.currentRow()
        if row < 0:
            return
        item = self.history_results.item(row, 0)
        if item is not None:
            self.message_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def _request_deletion(self, scope: str) -> None:
        topics = self.selected_topics() if scope == "selected_topics" else ()
        self.deletion_requested.emit(scope, self.broker.currentData(), topics)

    @staticmethod
    def _date_time_filter(
        label: str,
        object_name: str,
    ) -> tuple[QCheckBox, QDateTimeEdit]:
        enabled = QCheckBox("Use filter")
        enabled.setObjectName(f"{object_name}Enabled")
        enabled.setAccessibleName(f"Enable received {label.lower()} filter")
        editor = QDateTimeEdit(QDateTime.currentDateTimeUtc())
        editor.setObjectName(object_name)
        editor.setAccessibleName(f"Received {label.lower()} date and time")
        editor.setCalendarPopup(True)
        editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss 'UTC'")
        editor.setEnabled(False)
        enabled.toggled.connect(editor.setEnabled)
        return enabled, editor

    @staticmethod
    def _optional_date_time_row(
        enabled: QCheckBox,
        editor: QDateTimeEdit,
    ) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(enabled)
        layout.addWidget(editor, 1)
        return container

    @staticmethod
    def _optional_date_time_value(
        enabled: QCheckBox,
        editor: QDateTimeEdit,
    ) -> datetime | None:
        return editor.dateTime().toPython() if enabled.isChecked() else None

    @staticmethod
    def _set_optional_date_time(
        enabled: QCheckBox,
        editor: QDateTimeEdit,
        value: datetime | None,
    ) -> None:
        enabled.setChecked(value is not None)
        if value is not None:
            editor.setDateTime(value)

    def _integer_field(self, form, title: str, name: str) -> QLineEdit:
        widget = QLineEdit()
        widget.setObjectName(name)
        widget.setAccessibleName(title)
        self._add_error_row(form, title, widget, self._field_name(name))
        return widget

    def _byte_field(self, form, title: str, name: str) -> QuantityEditor:
        widget = QuantityEditor(tuple(item.value for item in ByteUnit), name)
        widget.setAccessibleName(title)
        widget.value.setAccessibleName(f"{title} value")
        widget.unit.setAccessibleName(f"{title} unit")
        self._add_error_row(form, title, widget, self._field_name(name))
        return widget

    def _add_error_row(self, form, title, widget, key: str) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        error = QLabel()
        error.setObjectName(f"{key}Error")
        error.setStyleSheet("color: #b91c1c;")
        error.setWordWrap(True)
        layout.addWidget(error)
        self._errors[key] = error
        form.addRow(title, container)
        if "form" not in self._errors:
            self._errors["form"] = error

    @staticmethod
    def _field_name(name: str) -> str:
        return {
            "maxEntriesPerBroker": "max_entries_per_broker",
            "maxEntriesTotal": "max_entries_total",
            "maxPayloadPerTopic": "max_payload_bytes_per_topic",
            "maxPayloadPerBroker": "max_payload_bytes_per_broker",
            "maxPayloadDatabase": "max_persisted_payload_database_bytes_total",
        }[name]

    def _draft_widgets(self) -> tuple[QWidget, ...]:
        return (
            self.per_broker_entries,
            self.total_entries,
            self.per_topic_bytes,
            self.per_broker_bytes,
            self.database_bytes,
            self.maximum_age,
            self.unlimited_age,
            self.warning_threshold,
            self.remove_expired,
            self.remove_excess,
            self.remove_unsubscribed,
        )

    @staticmethod
    def _positive_int(widget: QLineEdit) -> int:
        try:
            value = int(widget.text())
        except ValueError as error:
            raise ValueError("Enter a positive integer.") from error
        if value <= 0:
            raise ValueError("Enter a positive integer.")
        return value

    def _byte_value(self, editor: QuantityEditor) -> int:
        return exact_byte_value(
            self._positive_int(editor.value),
            ByteUnit(editor.unit.currentText()),
        )

    @staticmethod
    def _set_byte_value(editor: QuantityEditor, stored: int) -> None:
        value, unit = display_byte_value(stored)
        editor.value.setText(str(value))
        editor.unit.setCurrentText(unit.value)

    def _set_progress(
        self,
        row: int,
        column: int,
        utilization: float,
        warning: bool,
    ) -> None:
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(min(100, round(utilization * 100)))
        progress.setFormat(f"{utilization:.0%}")
        progress.setProperty("warning", warning)
        if warning:
            progress.setStyleSheet("QProgressBar::chunk { background: #d97706; }")
        self.usage_table.setCellWidget(row, column, progress)

    @staticmethod
    def _optional_datetime(value: datetime | None) -> str:
        return "-" if value is None else datetime_label(value)
