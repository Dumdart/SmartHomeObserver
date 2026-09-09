from datetime import timezone

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from topicgate.core.models.health import ObservationFindingCode
from topicgate.gui.components.expectation_editor import ExpectationEditor
from topicgate.gui.components.workspace_pane import WorkspacePane
from topicgate.gui.main_view_model import MainViewModel


class HealthInspector(WorkspacePane):
    """Broker-scoped health overview, expectation rules, and history."""

    topic_requested = Signal(str)
    expectation_edit_requested = Signal(str, object)

    def __init__(self, view_model: MainViewModel) -> None:
        super().__init__("Health", minimum_hint_width=320)
        self.setObjectName("healthInspector")
        self._view_model = view_model
        self._advanced_mode = True
        self._selected_topic = ""
        self._selected_expectation = None

        self._tabs = QTabWidget()
        self._tabs.setObjectName("healthTabs")
        self._tabs.tabBar().setObjectName("healthTabs")
        self._tabs.addTab(self._overview_page(), "Overview")
        self._broker_expectations = ExpectationEditor(view_model, "broker")
        expectations_scroll = QScrollArea()
        expectations_scroll.setWidgetResizable(True)
        expectations_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        expectations_scroll.setWidget(self._expectations_page())
        self._tabs.addTab(expectations_scroll, "Expectations")
        self._tabs.addTab(self._history_page(), "Failure history")
        self._tabs.addTab(self._changes_page(), "Changes")
        self._tabs.currentChanged.connect(self._tab_changed)
        self.content_layout.addWidget(self._tabs, 1)

        self._view_model.health_changed.connect(self.render)
        self._view_model.configuration_changed.connect(self.render)
        self.render()

    def set_advanced_mode(self, advanced: bool) -> None:
        self._advanced_mode = advanced
        self._tabs.blockSignals(True)
        if not advanced and self._tabs.currentIndex() == 3:
            self._tabs.setCurrentIndex(0)
        self._tabs.setTabVisible(3, advanced)
        self._tabs.setTabEnabled(3, advanced)
        self._tabs.blockSignals(False)
        self._delete_history_button.setVisible(advanced)
        self._history_table.setColumnHidden(4, not advanced)
        self._broker_expectations.set_advanced_mode(advanced)

    def _expectations_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._expectation_scope = QComboBox()
        self._expectation_scope.setObjectName("healthExpectationScope")
        self._expectation_scope.addItems(["All expectations", "Broker expectations", "Topic expectations"])
        self._expectation_scope.currentIndexChanged.connect(self._render_expectation_directory)
        layout.addWidget(self._expectation_scope)
        self._expectation_directory = QTableWidget(0, 2)
        self._expectation_directory.setObjectName("healthExpectationDirectory")
        self._expectation_directory.setHorizontalHeaderLabels(["Name", "Scope / target"])
        self._expectation_directory.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._expectation_directory.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._expectation_directory.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._expectation_directory.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._expectation_directory.itemSelectionChanged.connect(
            lambda: self._open_rule.setEnabled(self._expectation_directory.currentRow() >= 0)
        )
        self._expectation_directory.cellDoubleClicked.connect(lambda *_: self._open_directory_rule())
        layout.addWidget(self._expectation_directory, 1)
        self._directory_status = QLabel()
        self._directory_status.setTextFormat(Qt.TextFormat.PlainText)
        self._directory_status.setWordWrap(True)
        layout.addWidget(self._directory_status)
        self._open_rule = QPushButton("Edit selected expectation")
        self._open_rule.setEnabled(False)
        self._open_rule.clicked.connect(self._open_directory_rule)
        layout.addWidget(self._open_rule)
        add_broker = QPushButton("Add broker expectation")
        add_broker.clicked.connect(self._add_broker_expectation)
        layout.addWidget(add_broker)
        layout.addWidget(self._broker_expectations, 1)
        self._broker_expectations.setVisible(False)
        return page

    def _add_broker_expectation(self) -> None:
        self._broker_expectations.setVisible(True)
        self._broker_expectations.start_new()

    def _render_expectation_directory(self) -> None:
        from topicgate.core.models.health import TopicTarget

        scope = self._expectation_scope.currentIndex()
        selected_cell = self._expectation_directory.item(self._expectation_directory.currentRow(), 0)
        selected = None if selected_cell is None else selected_cell.data(Qt.ItemDataRole.UserRole)
        rows = tuple(
            item for item in self._view_model.all_expectations
            if scope == 0 or (scope == 2) == isinstance(item.target, TopicTarget)
        )
        self._expectation_directory.setRowCount(0)
        self._expectation_directory.setRowCount(len(rows))
        for row, expectation in enumerate(rows):
            topic = getattr(expectation.target, "topic", "")
            cell = QTableWidgetItem(expectation.name)
            cell.setData(Qt.ItemDataRole.UserRole, (topic, expectation.expectation_id))
            self._expectation_directory.setItem(row, 0, cell)
            self._expectation_directory.setItem(row, 1, QTableWidgetItem("Topic: " + topic if topic else "Broker connection"))
        self._directory_status.setText(
            f"{self._view_model.active_broker_profile.name} · {len(rows)} configured expectations in this scope. "
            "Select a rule to edit it. Topic rules open with their topic selected."
        )
        self._open_rule.setEnabled(False)
        for row in range(self._expectation_directory.rowCount()):
            if self._expectation_directory.item(row, 0).data(Qt.ItemDataRole.UserRole) == selected:
                self._expectation_directory.selectRow(row)
                break
        if scope == 2:
            self._broker_expectations.setVisible(False)

    def _open_directory_rule(self) -> None:
        cell = self._expectation_directory.item(self._expectation_directory.currentRow(), 0)
        if cell is not None:
            topic, expectation_id = cell.data(Qt.ItemDataRole.UserRole)
            self.expectation_edit_requested.emit(topic, expectation_id)

    def _overview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QHBoxLayout()
        self._status = QLabel("Health has not been evaluated.")
        self._status.setObjectName("healthAggregateStatus")
        self._status.setWordWrap(True)
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.setObjectName("refreshHealthButton")
        self._refresh_button.clicked.connect(self.refresh_health)
        header.addWidget(self._status, 1)
        header.addWidget(self._refresh_button)
        layout.addLayout(header)

        layout.addWidget(self._section("Broker checks"))
        self._broker_table = self._health_table("currentBrokerHealthTable")
        layout.addWidget(self._broker_table, 1)
        layout.addWidget(self._section("Topic checks"))
        self._topic_table = self._health_table("currentTopicHealthTable")
        layout.addWidget(self._topic_table, 2)

        self._evidence = QPlainTextEdit()
        self._evidence.setObjectName("healthEvidenceDetail")
        self._evidence.setReadOnly(True)
        self._evidence.setMaximumHeight(120)
        self._evidence.setPlaceholderText(
            "Select a check to inspect its evidence and evaluation time."
        )
        layout.addWidget(self._evidence)

        actions = QHBoxLayout()
        self._open_topic = QPushButton("Open topic")
        self._open_topic.setObjectName("openHealthTopicButton")
        self._edit_expectation = QPushButton("Edit expectation")
        self._edit_expectation.setObjectName("editHealthExpectationButton")
        self._remove_expectation = QPushButton("Remove")
        self._remove_expectation.setObjectName("removeHealthExpectationButton")
        self._remove_expectation.setProperty("danger", True)
        self._view_history = QPushButton("View failure history")
        self._view_history.setObjectName("viewHealthHistoryButton")
        self._open_topic.clicked.connect(self._open_selected_topic)
        self._edit_expectation.clicked.connect(self._edit_selected_expectation)
        self._remove_expectation.clicked.connect(self._remove_selected_expectation)
        self._view_history.clicked.connect(self._show_selected_history)
        for button in (
            self._open_topic,
            self._edit_expectation,
            self._remove_expectation,
            self._view_history,
        ):
            button.setEnabled(False)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def _history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        filters = QHBoxLayout()
        self._history_topic = QLineEdit()
        self._history_topic.setObjectName("healthHistoryTopic")
        self._history_topic.setPlaceholderText("Exact topic (optional)")
        self._history_status = QComboBox()
        self._history_status.setObjectName("healthHistoryStatus")
        self._history_status.addItems(["all", "active", "recovered"])
        self._query_button = QPushButton("Search")
        self._query_button.setObjectName("queryHealthHistoryButton")
        self._query_button.clicked.connect(self.query_history)
        self._delete_history_button = QPushButton("Delete selected")
        self._delete_history_button.setObjectName("deleteHealthHistoryButton")
        self._delete_history_button.setProperty("danger", True)
        self._delete_history_button.setEnabled(False)
        self._delete_history_button.clicked.connect(self._delete_selected_history)
        for widget in (
            self._history_topic,
            self._history_status,
            self._query_button,
            self._delete_history_button,
        ):
            filters.addWidget(widget)
        layout.addLayout(filters)

        time_filters = QHBoxLayout()
        self._after_enabled = QCheckBox("After")
        self._after = QDateTimeEdit(QDateTime.currentDateTime().addDays(-7))
        self._after.setCalendarPopup(True)
        self._after.setEnabled(False)
        self._after_enabled.toggled.connect(self._after.setEnabled)
        self._before_enabled = QCheckBox("Before")
        self._before = QDateTimeEdit(QDateTime.currentDateTime())
        self._before.setCalendarPopup(True)
        self._before.setEnabled(False)
        self._before_enabled.toggled.connect(self._before.setEnabled)
        for widget in (
            self._after_enabled,
            self._after,
            self._before_enabled,
            self._before,
        ):
            time_filters.addWidget(widget)
        time_filters.addStretch(1)
        layout.addLayout(time_filters)

        self._history_message = QLabel()
        self._history_message.setObjectName("healthHistoryMessage")
        self._history_message.setWordWrap(True)
        layout.addWidget(self._history_message)
        self._history_table = QTableWidget(0, 6)
        self._history_table.setObjectName("healthHistoryTable")
        self._history_table.setHorizontalHeaderLabels(
            ["Target", "Started", "Last seen", "Recovered", "Count", "Evidence"]
        )
        self._configure_table(self._history_table)
        header = self._history_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._history_table.cellDoubleClicked.connect(self._activate_history_topic)
        self._history_table.itemSelectionChanged.connect(
            self._history_selection_changed
        )
        layout.addWidget(self._history_table, 1)
        history_actions = QHBoxLayout()
        self._more_button = QPushButton("Load more")
        self._more_button.setObjectName("loadMoreHealthHistoryButton")
        self._more_button.clicked.connect(self.load_more_history)
        history_actions.addStretch(1)
        history_actions.addWidget(self._more_button)
        layout.addLayout(history_actions)
        return page

    def _changes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._delta_message = QLabel()
        self._delta_message.setObjectName("findingDeltaMessage")
        self._delta_message.setWordWrap(True)
        layout.addWidget(self._delta_message)
        self._delta_table = QTableWidget(0, 5)
        self._delta_table.setObjectName("findingDeltaTable")
        self._delta_table.setHorizontalHeaderLabels(
            ["Change", "Rule", "Target", "Status", "Severity"]
        )
        self._configure_table(self._delta_table)
        header = self._delta_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._delta_table, 1)
        return page

    def refresh_health(self) -> None:
        self._refresh_button.setEnabled(False)
        try:
            self._view_model.refresh_health()
        except Exception as error:
            self._status.setText(f"Health evaluation failed: {error}")
        finally:
            self._refresh_button.setEnabled(True)

    def query_history(self) -> None:
        self._query_history(None)

    def load_more_history(self) -> None:
        cursor = self._view_model.health_history.next_cursor
        if cursor is not None:
            self._query_history(cursor)

    def render(self) -> None:
        self._render_expectation_directory()
        summary = self._view_model.health_summary
        counts = f" — {summary.counts}" if summary.counts else ""
        self._status.setText(
            f"{self._view_model.active_broker_profile.name}: "
            f"{summary.label}{counts}. {summary.explanation}"
        )
        self._status.setProperty("healthTone", summary.tone)
        report = self._view_model.health_report
        if report is None:
            self._render_health_rows(self._broker_table, ())
            self._render_health_rows(self._topic_table, ())
        else:
            broker_rows = tuple(
                (
                    item.code.value.replace("_", " ").title(),
                    "broker",
                    self._status_label(item.status),
                    item.evidence_summary,
                    "",
                    None,
                    report.evaluated_at,
                    False,
                )
                for item in report.observation_findings
                if item.code is not ObservationFindingCode.BROKER_DISCONNECTED
            ) + tuple(
                self._finding_row(item, report.evaluated_at)
                for item in report.expectation_findings
                if item.target_kind == "broker"
            )
            topic_rows = tuple(
                self._finding_row(item, report.evaluated_at)
                for item in report.expectation_findings
                if item.target_kind == "topic"
            )
            self._render_health_rows(
                self._broker_table,
                tuple(sorted(broker_rows, key=self._health_row_sort_key)),
            )
            self._render_health_rows(self._topic_table, topic_rows)
        self._render_delta(report)
        self._render_history()

    def show_history(self, topic: str = "") -> None:
        self._history_topic.setText(topic)
        if self._tabs.currentIndex() == 2:
            self.query_history()
        else:
            self._tabs.setCurrentIndex(2)

    def select_broker_expectation(self, expectation_id: object) -> None:
        self._tabs.setCurrentIndex(1)
        self._expectation_scope.setCurrentIndex(1)
        self._broker_expectations.setVisible(True)
        self._broker_expectations.select_expectation(expectation_id)

    def _render_health_rows(self, table: QTableWidget, rows: tuple) -> None:
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            visible = values[:4]
            for column, value in enumerate(visible):
                item = QTableWidgetItem(str(value or "Evidence unavailable"))
                item.setData(Qt.ItemDataRole.UserRole, values)
                table.setItem(row, column, item)

    def _render_history(self) -> None:
        history = self._view_model.health_history
        self._history_table.setRowCount(len(history.items))
        for row, item in enumerate(history.items):
            values = (
                item.target,
                item.first_failed_at.isoformat(timespec="seconds"),
                item.last_seen_at.isoformat(timespec="seconds"),
                "Active"
                if item.recovered_at is None
                else item.recovered_at.isoformat(timespec="seconds"),
                str(item.occurrence_count),
                item.evidence_summary or "Evidence unavailable",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item)
                self._history_table.setItem(row, column, cell)
        self._history_message.setText(
            "No failure episodes match these filters."
            if not history.items
            else f"Showing {history.returned_count} failure episode(s)."
        )
        self._more_button.setVisible(history.next_cursor is not None)
        self._history_selection_changed()

    def _render_delta(self, report) -> None:
        delta = None if report is None else report.delta
        events = () if delta is None else delta.events
        self._delta_table.setRowCount(len(events))
        for row, event in enumerate(events):
            values = (
                str(event.kind).replace("_", " ").title(),
                next((item.name for item in self._view_model.all_expectations
                      if str(event.rule_id) in (str(item.expectation_id), item.rule_id)), str(event.rule_id)),
                event.matched_topic or "broker",
                self._transition_label(
                    event.previous_status,
                    event.current_status,
                    self._status_label,
                ),
                self._transition_label(
                    event.previous_severity,
                    event.current_severity,
                    self._enum_label,
                ),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, event)
                self._delta_table.setItem(row, column, cell)

        if report is None:
            message = "Health has not been evaluated."
        elif delta is None:
            checkpoint = report.checkpoint
            message = "Baseline captured. Refresh after a health change to compare findings."
            if checkpoint is not None and not checkpoint.complete:
                message += (
                    f" The baseline is incomplete; {checkpoint.omitted_count} "
                    "finding(s) were omitted."
                )
        elif events:
            message = f"Showing {delta.returned_count} finding change event(s)."
        else:
            message = "No finding changes since the previous evaluation."
        if delta is not None and not delta.complete:
            message += " Comparison is incomplete; recoveries may be suppressed."
            if delta.omitted_count:
                message += f" {delta.omitted_count} additional event(s) were omitted."
        self._delta_message.setText(message)

    def _finding_row(self, item, evaluated_at) -> tuple:
        return (
            item.name or str(item.expectation_id),
            item.target,
            self._status_label(item.status),
            item.evidence_summary or "Evidence unavailable",
            item.target if item.target_kind == "topic" else "",
            item.expectation_id,
            evaluated_at,
            item.evidence_truncated,
        )

    def _open_selected_topic(self) -> None:
        if self._selected_topic:
            self.topic_requested.emit(self._selected_topic)

    def _edit_selected_expectation(self) -> None:
        if self._selected_expectation is not None:
            self.expectation_edit_requested.emit(
                self._selected_topic,
                self._selected_expectation,
            )

    def _remove_selected_expectation(self) -> None:
        if self._selected_expectation is None:
            return
        pack_backed = self._view_model.expectation_is_pack_backed(
            self._selected_expectation
        )
        answer = QMessageBox.question(
            self,
            "Disable package rule?" if pack_backed else "Remove expectation?",
            (
                "Disable this package rule for the active diagnostic profile? "
                "It can be restored later by updating the profile."
                if pack_backed
                else "Remove this expectation? Its active failure will be closed "
                "and historical episodes will be retained."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._view_model.remove_expectation(self._selected_expectation)
        except Exception as error:
            QMessageBox.warning(self, "Unable to remove expectation", str(error))
            return
        self._selected_topic = ""
        self._selected_expectation = None
        self._open_topic.setEnabled(False)
        self._edit_expectation.setEnabled(False)
        self._remove_expectation.setEnabled(False)
        self._view_history.setEnabled(False)

    def _show_selected_history(self) -> None:
        self.show_history(self._selected_topic)

    def _activate_history_topic(self, row: int, _column: int) -> None:
        item = self._history_table.item(row, 0)
        history_item = item.data(Qt.ItemDataRole.UserRole) if item else None
        topic = "" if history_item is None else history_item.target
        if topic and topic != "broker":
            self.topic_requested.emit(topic)

    def _history_selection_changed(self) -> None:
        self._delete_history_button.setEnabled(
            self._history_table.currentRow() >= 0
        )

    def _delete_selected_history(self) -> None:
        row = self._history_table.currentRow()
        cell = self._history_table.item(row, 0) if row >= 0 else None
        item = cell.data(Qt.ItemDataRole.UserRole) if cell is not None else None
        if item is None:
            return
        consequence = (
            " This episode is active; if the check is still failing, a new "
            "episode may be created on the next evaluation."
            if item.recovered_at is None
            else ""
        )
        answer = QMessageBox.question(
            self,
            "Delete failure episode?",
            "Permanently delete the selected failure episode from history?"
            f"{consequence}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._view_model.delete_health_history(item.failure_id)
        except Exception as error:
            QMessageBox.warning(self, "Unable to delete failure episode", str(error))

    def _query_history(self, cursor: int | None) -> None:
        try:
            self._view_model.query_health_history(
                topic=self._history_topic.text().strip() or None,
                status=self._history_status.currentText(),
                after=self._optional_datetime(self._after_enabled, self._after),
                before=self._optional_datetime(self._before_enabled, self._before),
                cursor=cursor,
            )
        except Exception as error:
            self._history_message.setText(f"Unable to load failure history: {error}")

    def _tab_changed(self, index: int) -> None:
        if index == 3 and not self._advanced_mode:
            self._tabs.setCurrentIndex(0)
            return
        if index == 2:
            self.query_history()

    @staticmethod
    def _health_table(name: str) -> QTableWidget:
        table = QTableWidget(0, 4)
        table.setObjectName(name)
        table.setHorizontalHeaderLabels(["Check", "Target", "Result", "Evidence"])
        HealthInspector._configure_table(table)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table.itemSelectionChanged.connect(
            lambda table=table: HealthInspector._select_table_row(table)
        )
        return table

    @staticmethod
    def _select_table_row(table: QTableWidget) -> None:
        parent = table.parent()
        while parent is not None and not isinstance(parent, HealthInspector):
            parent = parent.parent()
        if isinstance(parent, HealthInspector):
            parent._selected_health_row_from(table)

    def _selected_health_row_from(self, table: QTableWidget) -> None:
        item = table.item(table.currentRow(), 0)
        values = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not values:
            return
        self._selected_topic = values[4]
        self._selected_expectation = values[5]
        evaluated = values[6].astimezone().isoformat(timespec="seconds")
        truncated = (
            "\n\nEvidence was truncated by the report limit." if values[7] else ""
        )
        self._evidence.setPlainText(f"{values[3]}\n\nEvaluated: {evaluated}{truncated}")
        self._open_topic.setEnabled(bool(self._selected_topic))
        self._edit_expectation.setEnabled(self._selected_expectation is not None)
        self._remove_expectation.setEnabled(self._selected_expectation is not None)
        if self._selected_expectation is not None:
            self._remove_expectation.setText(
                "Disable"
                if self._view_model.expectation_is_pack_backed(
                    self._selected_expectation
                )
                else "Remove"
            )
        self._view_history.setEnabled(self._selected_expectation is not None)

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

    @staticmethod
    def _status_label(status: object) -> str:
        value = str(getattr(status, "value", status))
        return {
            "problem": "Failed",
            "unknown": "Unknown",
            "healthy": "Healthy",
        }.get(value, value.replace("_", " ").title())

    @staticmethod
    def _enum_label(value: object) -> str:
        text = str(getattr(value, "value", value))
        return text.replace("_", " ").title()

    @staticmethod
    def _transition_label(previous, current, formatter) -> str:
        if previous is None:
            return formatter(current) if current is not None else "-"
        if current is None:
            return f"{formatter(previous)} -> absent"
        if previous == current:
            return formatter(current)
        return f"{formatter(previous)} -> {formatter(current)}"

    @staticmethod
    def _health_row_sort_key(row: tuple) -> int:
        return {"Failed": 0, "Unknown": 1, "Healthy": 2}.get(row[2], 3)

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    @staticmethod
    def _optional_datetime(enabled: QCheckBox, field: QDateTimeEdit):
        if not enabled.isChecked():
            return None
        value = field.dateTime().toPython()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
