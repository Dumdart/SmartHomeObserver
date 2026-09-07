from datetime import timezone

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from topicgate.gui.components.expectation_editor import ExpectationEditor
from topicgate.gui.main_view_model import MainViewModel


class HealthDialog(QDialog):
    """Broker-scoped expectation health, rules, and failure history."""

    def __init__(
        self,
        view_model: MainViewModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self.setObjectName("healthDialog")
        self.setWindowTitle("Broker health")
        self.resize(920, 620)

        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.setObjectName("healthTabs")
        self._tabs.tabBar().setObjectName("healthTabs")
        self._tabs.addTab(self._current_page(), "Current Health")
        self._tabs.addTab(
            ExpectationEditor(view_model, "broker"),
            "Broker Expectations",
        )
        self._tabs.addTab(self._history_page(), "Failure History")
        layout.addWidget(self._tabs)

        self._view_model.health_changed.connect(self.render)
        self._view_model.connection_changed.connect(self._refresh_if_visible)
        self._view_model.subscriptions_changed.connect(self._refresh_if_visible)
        self._view_model.configuration_changed.connect(self._refresh_if_visible)

    def _current_page(self) -> QWidget:
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
        self._health_table = QTableWidget(0, 4)
        self._health_table.setObjectName("currentHealthTable")
        self._health_table.setHorizontalHeaderLabels(
            ["Check", "Target", "Status", "Evidence"]
        )
        self._health_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._health_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._health_table.verticalHeader().setVisible(False)
        health_header = self._health_table.horizontalHeader()
        health_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        health_header.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        health_header.setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        health_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._health_table, 1)
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
        self._query_button = QPushButton("Apply")
        self._query_button.setObjectName("queryHealthHistoryButton")
        self._query_button.clicked.connect(self.query_history)
        for widget in (
            self._history_topic,
            self._history_status,
            self._query_button,
        ):
            filters.addWidget(widget)
        layout.addLayout(filters)
        time_filters = QHBoxLayout()
        time_filters.addWidget(self._after_enabled)
        time_filters.addWidget(self._after)
        time_filters.addWidget(self._before_enabled)
        time_filters.addWidget(self._before)
        time_filters.addStretch(1)
        layout.addLayout(time_filters)
        self._history_error = QLabel()
        self._history_error.setObjectName("healthHistoryMessage")
        self._history_error.setWordWrap(True)
        layout.addWidget(self._history_error)
        self._history_table = QTableWidget(0, 6)
        self._history_table.setObjectName("healthHistoryTable")
        self._history_table.setHorizontalHeaderLabels(
            ["Target", "Started", "Last seen", "Recovered", "Count", "Evidence"]
        )
        self._history_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._history_table.verticalHeader().setVisible(False)
        history_header = self._history_table.horizontalHeader()
        history_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            history_header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        history_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._history_table, 1)
        self._more_button = QPushButton("Load more")
        self._more_button.setObjectName("loadMoreHealthHistoryButton")
        self._more_button.clicked.connect(self.load_more_history)
        layout.addWidget(self._more_button, 0, Qt.AlignmentFlag.AlignRight)
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
        report = self._view_model.health_report
        if report is None:
            self._status.setText("Health has not been evaluated.")
            self._health_table.setRowCount(0)
        else:
            completeness = "complete" if report.evidence_complete else "limited"
            omitted = (
                f"; {report.omitted_count} expectation(s) omitted by the limit"
                if report.omitted_count
                else ""
            )
            self._status.setText(
                f"{self._view_model.active_broker_profile.name}: "
                f"{report.aggregate_status.value.title()} at "
                f"{report.evaluated_at.isoformat(timespec='seconds')} "
                f"({completeness} evidence, "
                f"{report.active_failure_count} active failure(s){omitted})"
            )
            rows = []
            rows.extend(
                (
                    f"Observation: {item.code.value.replace('_', ' ').title()}",
                    "broker",
                    item.status.value,
                    item.evidence_summary,
                )
                for item in report.observation_findings
            )
            rows.extend(
                (
                    item.name or str(item.expectation_id),
                    item.target,
                    item.status.value,
                    item.evidence_summary or "",
                )
                for item in report.expectation_findings
            )
            self._health_table.setRowCount(len(rows))
            for row, values in enumerate(rows):
                for column, value in enumerate(values):
                    self._health_table.setItem(
                        row,
                        column,
                        QTableWidgetItem(str(value)),
                    )

        history = self._view_model.health_history
        self._history_table.setRowCount(len(history.items))
        for row, item in enumerate(history.items):
            values = (
                item.target,
                item.first_failed_at.isoformat(timespec="seconds"),
                item.last_seen_at.isoformat(timespec="seconds"),
                (
                    "Active"
                    if item.recovered_at is None
                    else item.recovered_at.isoformat(timespec="seconds")
                ),
                str(item.occurrence_count),
                item.evidence_summary or "Evidence unavailable",
            )
            for column, value in enumerate(values):
                self._history_table.setItem(row, column, QTableWidgetItem(value))
        self._history_error.setText(
            "No failure episodes match these filters."
            if not history.items
            else f"Showing {history.returned_count} failure episode(s)."
        )
        self._more_button.setVisible(history.next_cursor is not None)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.refresh_health()
        self.query_history()

    def _query_history(self, cursor: int | None) -> None:
        try:
            self._view_model.query_health_history(
                topic=self._history_topic.text().strip() or None,
                status=self._history_status.currentText(),
                after=self._optional_datetime(self._after_enabled, self._after),
                before=self._optional_datetime(
                    self._before_enabled,
                    self._before,
                ),
                cursor=cursor,
            )
        except Exception as error:
            self._history_error.setText(f"Unable to load failure history: {error}")

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.refresh_health()
            self.query_history()

    @staticmethod
    def _optional_datetime(enabled: QCheckBox, field: QDateTimeEdit):
        if not enabled.isChecked():
            return None
        value = field.dateTime().toPython()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
