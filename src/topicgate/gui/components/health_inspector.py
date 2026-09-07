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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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
        self._selected_topic = ""
        self._selected_expectation = None

        self._tabs = QTabWidget()
        self._tabs.setObjectName("healthTabs")
        self._tabs.tabBar().setObjectName("healthTabs")
        self._tabs.addTab(self._overview_page(), "Overview")
        self._broker_expectations = ExpectationEditor(view_model, "broker")
        self._tabs.addTab(self._broker_expectations, "Expectations")
        self._tabs.addTab(self._history_page(), "History")
        self._tabs.currentChanged.connect(self._tab_changed)
        self.content_layout.addWidget(self._tabs, 1)

        self._view_model.health_changed.connect(self.render)
        self._view_model.configuration_changed.connect(self.render)
        self.render()

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
        self._view_history = QPushButton("View failure history")
        self._view_history.setObjectName("viewHealthHistoryButton")
        self._open_topic.clicked.connect(self._open_selected_topic)
        self._edit_expectation.clicked.connect(self._edit_selected_expectation)
        self._view_history.clicked.connect(self._show_selected_history)
        for button in (
            self._open_topic,
            self._edit_expectation,
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
        self._query_button = QPushButton("Apply")
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
        self._render_history()

    def show_history(self, topic: str = "") -> None:
        self._history_topic.setText(topic)
        if self._tabs.currentIndex() == 2:
            self.query_history()
        else:
            self._tabs.setCurrentIndex(2)

    def select_broker_expectation(self, expectation_id: object) -> None:
        self._tabs.setCurrentIndex(1)
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
