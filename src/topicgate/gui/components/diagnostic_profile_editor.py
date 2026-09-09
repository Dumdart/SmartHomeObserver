from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


class DiagnosticProfileEditorWindow(QDialog):
    """A modeless window over an isolated diagnostic-profile draft."""

    def __init__(self, editor, broker_id: UUID, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editor = editor
        self._broker_id = broker_id
        self._loading = False
        self.setObjectName("diagnosticProfileEditor")
        self.setWindowTitle("Diagnostic profiles")
        self.setModal(False)
        self.resize(920, 650)

        layout = QVBoxLayout(self)
        selector_row = QHBoxLayout()
        self._profiles = QComboBox()
        self._profiles.setObjectName("diagnosticProfileSelector")
        self._profiles.currentIndexChanged.connect(self._select_profile)
        self._new = QPushButton("Create")
        self._copy = QPushButton("Copy")
        self._delete = QPushButton("Delete")
        self._new.clicked.connect(self._create)
        self._copy.clicked.connect(self._copy_profile)
        self._delete.clicked.connect(self._delete_profile)
        selector_row.addWidget(QLabel("Profile"))
        selector_row.addWidget(self._profiles, 1)
        selector_row.addWidget(self._new)
        selector_row.addWidget(self._copy)
        selector_row.addWidget(self._delete)
        layout.addLayout(selector_row)

        form = QFormLayout()
        self._name = QLineEdit()
        self._name.setObjectName("diagnosticProfileName")
        self._description = QPlainTextEdit()
        self._description.setObjectName("diagnosticProfileDescription")
        self._description.setMaximumHeight(64)
        self._pack = QComboBox()
        self._pack.setObjectName("diagnosticPackSelector")
        self._pack.currentIndexChanged.connect(self._pack_changed)
        form.addRow("Name", self._name)
        form.addRow("Description", self._description)
        form.addRow("Exact pack", self._pack)
        layout.addLayout(form)

        self._errors = QLabel()
        self._errors.setObjectName("diagnosticProfileValidation")
        self._errors.setWordWrap(True)
        layout.addWidget(self._errors)
        self._rules = QTableWidget(0, 5)
        self._rules.setObjectName("diagnosticProfileRules")
        self._rules.setHorizontalHeaderLabels(["ID", "Name", "Target", "Source", "Enabled"])
        self._rules.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._rules.verticalHeader().setVisible(False)
        layout.addWidget(self._rules, 1)
        self._preview = QLabel("Preview has not been run.")
        self._preview.setObjectName("diagnosticProfilePreview")
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)
        buttons = QHBoxLayout()
        self._preview_button = QPushButton("Preview")
        self._apply = QPushButton("Apply")
        self._revert = QPushButton("Revert")
        self._preview_button.clicked.connect(self._preview_draft)
        self._apply.clicked.connect(self._apply_draft)
        self._revert.clicked.connect(self._revert_draft)
        buttons.addStretch(1)
        buttons.addWidget(self._preview_button)
        buttons.addWidget(self._revert)
        buttons.addWidget(self._apply)
        layout.addLayout(buttons)
        self.set_broker(broker_id)

    def set_broker(self, broker_id: UUID) -> None:
        if broker_id == self._broker_id and self._editor.draft is not None:
            return
        if self._editor.draft is not None and self._editor.is_dirty and not self._confirm_discard():
            return
        self._broker_id = broker_id
        self._editor.open_broker(broker_id)
        self._render()

    def _select_profile(self, index: int) -> None:
        if self._loading or index < 0:
            return
        profile_id = self._profiles.itemData(index)
        if not isinstance(profile_id, UUID):
            return
        if self._editor.draft is not None and self._editor.is_dirty and not self._confirm_discard():
            self._render()
            return
        self._editor.select(self._broker_id, profile_id)
        self._render()

    def _create(self) -> None:
        self._editor.create(self._broker_id, "New profile")
        self._render()

    def _copy_profile(self) -> None:
        draft = self._editor.draft
        if draft is not None:
            self._editor.copy(f"{draft.name} copy")
            self._render()

    def _delete_profile(self) -> None:
        draft = self._editor.draft
        if draft is None or draft.is_default:
            return
        if QMessageBox.question(self, "Delete diagnostic profile?", f"Delete '{draft.name}'?") != QMessageBox.StandardButton.Yes:
            return
        self._editor.delete()
        self.set_broker(self._broker_id)

    def _pack_changed(self, index: int) -> None:
        if self._loading or self._editor.draft is None:
            return
        self._sync_fields()
        self._editor.set_pack(self._pack.itemData(index))
        self._render()

    def _sync_fields(self) -> None:
        draft = self._editor.draft
        if draft is not None:
            self._editor.replace_draft(replace(draft, name=self._name.text(), description=self._description.toPlainText()))

    def _preview_draft(self) -> None:
        self._sync_fields()
        result = self._editor.preview()
        self._render()
        if result is not None:
            self._preview.setText(f"Draft preview: {result.aggregate_status.value}. {len(result.findings)} rule finding(s).")

    def _apply_draft(self) -> None:
        self._sync_fields()
        try:
            self._editor.apply()
        except ValueError:
            self._render()
            return
        self._render()

    def _revert_draft(self) -> None:
        self._editor.revert()
        self._render()

    def _render(self) -> None:
        self._loading = True
        try:
            draft = self._editor.draft
            self._profiles.clear()
            for item in self._editor.profiles:
                self._profiles.addItem(item.name, item.profile_id)
            if draft is None:
                return
            if self._profiles.findData(draft.profile_id) < 0:
                self._profiles.addItem(f"{draft.name} (unapplied)", draft.profile_id)
            self._profiles.setCurrentIndex(self._profiles.findData(draft.profile_id))
            self._name.setText(draft.name)
            self._description.setPlainText(draft.description)
            self._pack.clear()
            self._pack.addItem("No pack", None)
            for reference in self._editor.pack_references:
                self._pack.addItem(f"{reference.pack_id}@{reference.version}", reference)
            pack_index = self._pack.findData(draft.pack_reference)
            self._pack.setCurrentIndex(max(0, pack_index))
            prepared = self._editor.validate()
            self._errors.setText("\n".join(self._editor.validation.errors))
            draft_expectations = tuple(
                rule
                for rule in prepared.expectations
                if rule.profile_id == draft.profile_id
            )
            self._rules.setRowCount(len(draft_expectations))
            for row, rule in enumerate(draft_expectations):
                target = getattr(rule.target, "topic", "Broker")
                values = (rule.rule_id, rule.name, str(target), rule.source_kind, "Yes" if rule.enabled else "No")
                for column, value in enumerate(values):
                    self._rules.setItem(row, column, QTableWidgetItem(value))
            valid = self._editor.validation.is_valid
            self._apply.setEnabled(valid and self._editor.is_dirty)
            self._preview_button.setEnabled(valid)
            self._delete.setEnabled(not draft.is_default)
        finally:
            self._loading = False

    def _confirm_discard(self) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Unapplied diagnostic profile changes")
        dialog.setText("Apply, discard, or keep the current diagnostic-profile draft?")
        apply = dialog.addButton("Apply", QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is apply:
            self._sync_fields()
            try:
                self._editor.apply()
            except ValueError:
                self._render()
                return False
            return True
        return dialog.clickedButton() is discard

    def closeEvent(self, event) -> None:
        if self._editor.is_dirty and not self._confirm_discard():
            event.ignore()
            return
        super().closeEvent(event)
