from base64 import b64encode
from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.health import ActionKind, HealthExpectation
from topicgate.core.models.health.condition import Condition
from topicgate.core.models.health.condition import EqualCondition
from topicgate.core.models.health.condition import FreshnessCondition
from topicgate.core.models.health.condition import InRangeCondition
from topicgate.core.models.health.condition import NumericRangeCondition
from topicgate.core.models.health.condition import OutSideCondition
from topicgate.core.models.health.condition import TopicAbsentCondition
from topicgate.core.models.health.condition import TopicExistsCondition
from topicgate.core.models.health.condition_kind import ConditionKind
from topicgate.gui.main_view_model import MainViewModel
from topicgate.presentation.health_presentation import finding_result_label


class ExpectationEditor(QWidget):
    """Edit broker- or topic-scoped health expectations."""

    def __init__(
        self,
        view_model: MainViewModel,
        target_kind: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if target_kind not in {"broker", "topic"}:
            raise ValueError("target_kind must be 'broker' or 'topic'")
        self._view_model = view_model
        self._target_kind = target_kind
        self._selected_id: UUID | None = None
        self._editing = target_kind == "topic"
        self._expectations: tuple[HealthExpectation, ...] = ()
        self.setObjectName(f"{target_kind}ExpectationEditor")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._context = QLabel()
        self._context.setObjectName("expectationContext")
        self._context.setWordWrap(True)
        layout.addWidget(self._context)
        self._result_summary = QLabel()
        self._result_summary.setObjectName("expectationResultSummary")
        self._result_summary.setWordWrap(True)
        self._result_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._result_summary)

        self._table = QTableWidget(0, 4)
        self._table.setObjectName("expectationTable")
        self._table.setHorizontalHeaderLabels(
            ["Name", "Expected", "Configuration", "Result"]
        )
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        self._table.cellClicked.connect(self._select_row)
        layout.addWidget(self._table, 1)

        self._form_container = QWidget()
        self._form_container.setObjectName("expectationEditingControls")
        form = QFormLayout(self._form_container)
        self._form = form
        self._name = QLineEdit()
        self._name.setObjectName("expectationName")
        self._description = QLineEdit()
        self._description.setObjectName("expectationDescription")
        self._expected = QComboBox()
        self._expected.setObjectName("expectationExpectedValue")
        self._expected.setEditable(target_kind == "topic")
        if target_kind == "topic":
            self._expected.lineEdit().setPlaceholderText("e.g. ExpectedValue")
        if target_kind == "broker":
            self._expected.addItems([item.value for item in ConnectionStatus])
            self._expected.setCurrentText(ConnectionStatus.CONNECTED.value)
            self._expected.setMaximumWidth(220)
        self._encoding = QComboBox()
        self._encoding.setObjectName("expectationEncoding")
        self._encoding.addItem("UTF-8 text", "utf-8")
        self._encoding.addItem("Base64 bytes", "base64")
        self._encoding.setVisible(target_kind == "topic")
        self._condition_kind = QComboBox()
        self._condition_kind.setObjectName("expectationConditionKind")
        self._condition_kind.addItem("Equals", ConditionKind.EQUAL)
        self._condition_kind.addItem("One of", ConditionKind.IN_RANGE)
        self._condition_kind.addItem("Not one of", ConditionKind.OUTSIDE)
        if target_kind == "topic":
            self._condition_kind.addItem(
                "Number between",
                ConditionKind.NUMERIC_RANGE,
            )
            self._condition_kind.addItem(
                "Topic exists",
                ConditionKind.TOPIC_EXISTS,
            )
            self._condition_kind.addItem(
                "Topic does not exist",
                ConditionKind.TOPIC_ABSENT,
            )
            self._condition_kind.addItem(
                "Fresh within",
                ConditionKind.FRESH_WITHIN,
            )
        self._condition_kind.currentIndexChanged.connect(
            self._condition_kind_changed
        )
        self._expected_values = QPlainTextEdit()
        self._expected_values.setObjectName("expectationExpectedValues")
        self._expected_values.setPlaceholderText("e.g. 1,3")
        self._expected_values.setMaximumHeight(90)
        self._expected_editor = QStackedWidget()
        self._expected_editor.setObjectName("expectationExpectedEditor")
        self._expected_editor.addWidget(self._expected)
        self._expected_editor.addWidget(self._expected_values)
        self._expected_editor.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._expected_editor.setMaximumHeight(32)
        if target_kind == "broker":
            self._condition_kind.setMaximumWidth(220)
            self._expected_editor.setMaximumWidth(220)
        self._enabled = QCheckBox("Enabled")
        self._enabled.setObjectName("expectationEnabled")
        self._enabled.setChecked(True)
        self._log_action = QCheckBox("Log transitions")
        self._log_action.setObjectName("expectationLogAction")
        self._log_action.setChecked(True)
        self._store_action = QCheckBox("Store failure history")
        self._store_action.setObjectName("expectationStoreAction")
        self._store_action.setChecked(True)
        form.addRow("Name", self._name)
        form.addRow("Condition", self._condition_kind)
        form.addRow("Expected", self._expected_editor)
        self._condition_hint = QLabel()
        self._condition_hint.setObjectName("expectationConditionHint")
        self._condition_hint.setWordWrap(True)
        form.addRow(self._condition_hint)
        if target_kind == "topic":
            form.addRow("Encoding", self._encoding)
        form.addRow("", self._enabled)
        form.addRow("Actions", self._log_action)
        form.addRow("", self._store_action)
        form.addRow("Description", self._description)
        self._revision = QLabel()
        self._revision.setObjectName("expectationRevision")
        form.addRow("Details", self._revision)
        layout.addWidget(self._form_container)
        self._form_container.setVisible(self._editing)

        buttons = QHBoxLayout()
        self._new_button = QPushButton("Add expectation")
        self._new_button.setObjectName("addExpectationButton")
        self._delete_button = QPushButton("Delete")
        self._delete_button.setObjectName("deleteExpectationButton")
        self._save_button = QPushButton("Save")
        self._save_button.setObjectName("saveExpectationButton")
        self._new_button.clicked.connect(self._new)
        self._delete_button.clicked.connect(self._delete)
        self._save_button.clicked.connect(self._save)
        buttons.addWidget(self._new_button)
        buttons.addStretch(1)
        buttons.addWidget(self._delete_button)
        buttons.addWidget(self._save_button)
        layout.addLayout(buttons)

        self._view_model.health_changed.connect(self.render)
        self.render()

    def render(self) -> None:
        if self._target_kind == "topic":
            topic = self._view_model.topic
            available = bool(topic) and "+" not in topic and "#" not in topic
            self._context.setText(
                f"Expectations for {topic}"
                if available
                else "Select an exact topic to configure expectations."
            )
            self._expectations = self._view_model.topic_expectations
            topic_health = self._view_model.selected_topic_health
            self._result_summary.setText(
                "\n".join(
                    part
                    for part in (topic_health.detail, topic_health.evidence)
                    if part
                )
                if available
                else ""
            )
        else:
            available = True
            self._context.setText(
                f"Broker expectations for "
                f"{self._view_model.active_broker_profile.name}"
            )
            self._expectations = self._view_model.broker_expectations
            self._result_summary.setText("")

        self._table.setRowCount(len(self._expectations))
        for row, expectation in enumerate(self._expectations):
            values = (
                expectation.name,
                self._expected_label(expectation),
                "Enabled" if expectation.enabled else "Disabled",
                finding_result_label(expectation, self._view_model.health_report),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, expectation.expectation_id)
                self._table.setItem(row, column, item)
        self._new_button.setEnabled(available)
        self._set_form_enabled(available)
        if self._selected_id is not None:
            selected = next(
                (
                    item
                    for item in self._expectations
                    if item.expectation_id == self._selected_id
                ),
                None,
            )
            if selected is not None:
                self._load(selected)
                return
        self._selected_id = None
        self._delete_button.setEnabled(False)
        if self._target_kind == "broker":
            self._form_container.setVisible(self._editing)

    def _new(self) -> None:
        self._editing = True
        self._form_container.setVisible(True)
        self._selected_id = None
        self._name.clear()
        self._description.clear()
        self._enabled.setChecked(True)
        self._log_action.setChecked(True)
        self._store_action.setChecked(True)
        self._condition_kind.setCurrentIndex(0)
        self._expected_values.clear()
        self._revision.setText("New expectation")
        if self._target_kind == "topic":
            self._expected.setEditText("")
            self._encoding.setCurrentIndex(0)
        else:
            self._expected.setCurrentText(ConnectionStatus.CONNECTED.value)
        self._delete_button.setEnabled(False)
        self._name.setFocus(Qt.FocusReason.OtherFocusReason)

    def _select_row(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._expectations):
            self._load(self._expectations[row])

    def _load(self, expectation: HealthExpectation) -> None:
        self._editing = True
        self._form_container.setVisible(True)
        self._selected_id = expectation.expectation_id
        self._name.setText(expectation.name)
        self._description.setText(expectation.description)
        self._enabled.setChecked(expectation.enabled)
        self._log_action.setChecked(ActionKind.LOG in expectation.actions)
        self._store_action.setChecked(
            ActionKind.STORE_FAILURE in expectation.actions
        )
        condition_kind = self._condition_kind_for(expectation.condition)
        self._condition_kind.setCurrentIndex(
            self._condition_kind.findData(condition_kind)
        )
        values = self._condition_values(expectation.condition)
        self._set_expected_values(values)
        self._revision.setText(f"Revision {expectation.revision}")
        self._delete_button.setEnabled(True)

    def select_expectation(self, expectation_id: object) -> None:
        """Select an expectation from another health presentation."""
        selected = next(
            (
                item
                for item in self._expectations
                if item.expectation_id == expectation_id
            ),
            None,
        )
        if selected is not None:
            self._load(selected)
            row = self._expectations.index(selected)
            self._table.selectRow(row)

    def _save(self) -> None:
        try:
            self._view_model.save_expectation(
                target_kind=self._target_kind,
                expectation_id=self._selected_id,
                name=self._name.text(),
                description=self._description.text(),
                condition_kind=self._selected_condition_kind(),
                expected_values=self._form_expected_values(),
                encoding=str(self._encoding.currentData() or "utf-8"),
                enabled=self._enabled.isChecked(),
                log_action=self._log_action.isChecked(),
                store_failure=self._store_action.isChecked(),
            )
        except (RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "Invalid expectation", str(error))
            return
        self._selected_id = None
        self._editing = self._target_kind == "topic"
        self.render()

    def _delete(self) -> None:
        if self._selected_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete expectation?",
            "Delete this expectation? Its active failure will be closed and "
            "historical episodes will be retained.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._view_model.delete_expectation(self._selected_id)
        self._selected_id = None
        self._new()
        self._editing = self._target_kind == "topic"
        self._form_container.setVisible(self._editing)
        self.render()

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (
            self._name,
            self._description,
            self._condition_kind,
            self._expected_editor,
            self._expected,
            self._expected_values,
            self._encoding,
            self._enabled,
            self._log_action,
            self._store_action,
            self._save_button,
        ):
            widget.setEnabled(enabled)

    @staticmethod
    def _expected_label(expectation: HealthExpectation) -> str:
        condition = expectation.condition
        values = ExpectationEditor._condition_values(condition)
        rendered = ", ".join(
            ExpectationEditor._display_value(value) for value in values
        )
        if isinstance(condition, EqualCondition):
            return rendered
        if isinstance(condition, InRangeCondition):
            return f"One of: {rendered}"
        if isinstance(condition, OutSideCondition):
            return f"Not one of: {rendered}"
        if isinstance(condition, NumericRangeCondition):
            return f"Between {condition.minimum} and {condition.maximum}"
        if isinstance(condition, TopicExistsCondition):
            return "Topic exists"
        if isinstance(condition, TopicAbsentCondition):
            return "Topic does not exist"
        if isinstance(condition, FreshnessCondition):
            return f"Fresh within {condition.max_age_seconds:g} seconds"
        return type(condition).__name__

    def _condition_kind_changed(self, index: int) -> None:
        condition_kind = self._condition_kind_from_value(
            self._condition_kind.itemData(index)
        )
        is_multiple = condition_kind in {
            ConditionKind.IN_RANGE,
            ConditionKind.OUTSIDE,
            ConditionKind.NUMERIC_RANGE,
        }
        has_no_value = condition_kind in {
            ConditionKind.TOPIC_EXISTS,
            ConditionKind.TOPIC_ABSENT,
        }
        if is_multiple and not self._expected_values.toPlainText():
            current_value = self._expected.currentText()
            if current_value:
                self._expected_values.setPlainText(current_value)
        elif not is_multiple and not self._expected.currentText():
            first_value = self._form_expected_values()[0:1]
            if first_value:
                self._expected.setEditText(first_value[0])
        self._expected_editor.setCurrentIndex(1 if is_multiple else 0)
        self._expected_editor.setMaximumHeight(90 if is_multiple else 32)
        self._expected_editor.setVisible(not has_no_value)
        expected_label = self._form.labelForField(self._expected_editor)
        if expected_label is not None:
            expected_label.setVisible(not has_no_value)
            expected_label.setText(
                "Max age (seconds)"
                if condition_kind is ConditionKind.FRESH_WITHIN
                else "Expected"
            )
        uses_payload_encoding = condition_kind in {
            ConditionKind.EQUAL,
            ConditionKind.IN_RANGE,
            ConditionKind.OUTSIDE,
        }
        if self._target_kind == "topic":
            self._encoding.setVisible(uses_payload_encoding)
            encoding_label = self._form.labelForField(self._encoding)
            if encoding_label is not None:
                encoding_label.setVisible(uses_payload_encoding)
        if condition_kind is ConditionKind.NUMERIC_RANGE:
            self._condition_hint.setText(
                "Enter inclusive minimum and maximum values, e.g. 1,3."
            )
            self._condition_hint.setVisible(True)
        elif is_multiple:
            self._condition_hint.setText(
                "Enter comma-separated values, e.g. online,degraded."
            )
            self._condition_hint.setVisible(True)
        elif condition_kind is ConditionKind.FRESH_WITHIN:
            self._condition_hint.setText(
                "Enter the maximum observation age in seconds."
            )
            self._condition_hint.setVisible(True)
        else:
            self._condition_hint.clear()
            self._condition_hint.setVisible(False)

    def _form_expected_values(self) -> tuple[str, ...]:
        condition_kind = self._selected_condition_kind()
        if condition_kind in {
            ConditionKind.TOPIC_EXISTS,
            ConditionKind.TOPIC_ABSENT,
        }:
            return ()
        if condition_kind in {
            ConditionKind.EQUAL,
            ConditionKind.FRESH_WITHIN,
        }:
            return (self._expected.currentText(),)
        return tuple(
            value.strip()
            for line in self._expected_values.toPlainText().splitlines()
            for value in line.split(",")
            if value.strip()
        )

    def _selected_condition_kind(self) -> ConditionKind:
        return self._condition_kind_from_value(self._condition_kind.currentData())

    @staticmethod
    def _condition_kind_from_value(value: object) -> ConditionKind:
        if isinstance(value, ConditionKind):
            return value
        try:
            return ConditionKind(str(value))
        except ValueError as error:
            raise ValueError(f"Unsupported condition kind: {value!r}") from error

    @staticmethod
    def _condition_kind_for(condition: Condition) -> ConditionKind:
        if isinstance(condition, EqualCondition):
            return ConditionKind.EQUAL
        if isinstance(condition, InRangeCondition):
            return ConditionKind.IN_RANGE
        if isinstance(condition, OutSideCondition):
            return ConditionKind.OUTSIDE
        if isinstance(condition, NumericRangeCondition):
            return ConditionKind.NUMERIC_RANGE
        if isinstance(condition, TopicExistsCondition):
            return ConditionKind.TOPIC_EXISTS
        if isinstance(condition, TopicAbsentCondition):
            return ConditionKind.TOPIC_ABSENT
        if isinstance(condition, FreshnessCondition):
            return ConditionKind.FRESH_WITHIN
        raise ValueError(
            f"Unsupported expectation condition: {type(condition).__name__}"
        )

    @staticmethod
    def _condition_values(condition: Condition) -> tuple[bytes | str, ...]:
        if isinstance(condition, EqualCondition):
            return (condition.expected_value,)
        if isinstance(condition, (InRangeCondition, OutSideCondition)):
            return condition.expected_values
        if isinstance(condition, NumericRangeCondition):
            return (str(condition.minimum), str(condition.maximum))
        if isinstance(condition, FreshnessCondition):
            return (f"{condition.max_age_seconds:g}",)
        if isinstance(condition, (TopicExistsCondition, TopicAbsentCondition)):
            return ()
        raise ValueError(
            f"Unsupported expectation condition: {type(condition).__name__}"
        )

    def _set_expected_values(self, values: tuple[bytes | str, ...]) -> None:
        condition_kind = self._selected_condition_kind()
        if condition_kind in {
            ConditionKind.TOPIC_EXISTS,
            ConditionKind.TOPIC_ABSENT,
        }:
            self._expected_values.clear()
            self._expected.setEditText("")
            return
        if condition_kind in {
            ConditionKind.EQUAL,
            ConditionKind.FRESH_WITHIN,
        }:
            value = values[0] if values else ""
            self._set_single_expected_value(value)
            return

        rendered_values = self._render_values(values)
        self._expected_values.setPlainText("\n".join(rendered_values))

    def _set_single_expected_value(self, value: bytes | str) -> None:
        rendered = self._render_values((value,))[0]
        if self._target_kind == "broker":
            self._expected.setCurrentText(rendered)
        else:
            self._expected.setEditText(rendered)

    def _render_values(self, values: tuple[bytes | str, ...]) -> tuple[str, ...]:
        if any(isinstance(value, bytes) for value in values):
            if all(isinstance(value, bytes) for value in values):
                try:
                    rendered = tuple(value.decode("utf-8") for value in values)
                    self._encoding.setCurrentIndex(0)
                    return rendered
                except UnicodeDecodeError:
                    self._encoding.setCurrentIndex(1)
                    return tuple(
                        b64encode(value).decode("ascii")
                        for value in values
                    )
        self._encoding.setCurrentIndex(0)
        return tuple(str(value) for value in values)

    @staticmethod
    def _display_value(value: bytes | str) -> str:
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return f"Base64: {b64encode(value).decode('ascii')}"
        return str(value)
