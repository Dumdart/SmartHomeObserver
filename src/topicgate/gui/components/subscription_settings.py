from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from topicgate.core.models.subscription import Subscription


class SubscriptionSettingsPane(QWidget):
    """Explicit Apply editor for the selected subscription filter."""

    apply_requested = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetNoConstraint
        )
        self._subscription: Subscription | None = None
        self._selected_topic = ""

        self._hint = QLabel("No editable subscription filter selected.")
        self._hint.setTextFormat(Qt.TextFormat.PlainText)
        self._hint.setWordWrap(True)
        self._hint.setObjectName("settingsHint")
        self._hint.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.content_layout.addWidget(self._hint)

        form = QFormLayout()
        self._filter_edit = QLineEdit()
        self._qos_combo = self._compact_combo(
            [
                "0 - At most once",
                "1 - At least once",
                "2 - Exactly once",
            ]
        )
        self._retain_as_published = QCheckBox("Preserve retained flag")
        self._retain_handling = self._compact_combo(
            [
                "0 - Send retained messages",
                "1 - Send only for new subscription",
                "2 - Do not send retained messages",
            ]
        )
        form.addRow("MQTT subscription", self._filter_edit)
        form.addRow("QoS", self._qos_combo)
        form.addRow("Retain", self._retain_as_published)
        form.addRow("Handling", self._retain_handling)
        self.content_layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._apply_button = QPushButton("Apply")
        self._apply_button.setObjectName("applySubscriptionButton")
        self._apply_button.setDefault(True)
        self._apply_button.clicked.connect(self._apply)
        buttons.addWidget(self._apply_button)
        self.content_layout.addLayout(buttons)
        self._feedback = QLabel()
        self._feedback.setObjectName("subscriptionApplyFeedback")
        self._feedback.setTextFormat(Qt.TextFormat.PlainText)
        self._feedback.setWordWrap(True)
        self.content_layout.addWidget(self._feedback)
        self.content_layout.addStretch(1)

    def render(
        self,
        selected_topic: str,
        subscription: Subscription | None,
    ) -> None:
        self._selected_topic = selected_topic
        self._feedback.clear()
        self._subscription = subscription
        self._set_editor_enabled(subscription is not None)

        if subscription is None:
            self._hint.setText("No editable subscription filter selected.")
            self._filter_edit.clear()
            self._qos_combo.setCurrentIndex(0)
            self._retain_as_published.setChecked(False)
            self._retain_handling.setCurrentIndex(0)
            return

        if subscription.topic_filter == selected_topic:
            self._hint.setText("Editing the selected subscription filter.")
        else:
            self._hint.setText(
                f"Matched by subscription: {subscription.topic_filter}"
            )
        self._filter_edit.setText(subscription.topic_filter)
        self._qos_combo.setCurrentIndex(subscription.qos)
        self._retain_as_published.setChecked(subscription.retain_as_published)
        self._retain_handling.setCurrentIndex(subscription.retain_handling)

    def _apply(self) -> None:
        if self._subscription is None:
            return
        try:
            updated = Subscription(
                topic_filter=self._filter_edit.text().strip(),
                qos=self._qos_combo.currentIndex(),
                retain_as_published=self._retain_as_published.isChecked(),
                retain_handling=self._retain_handling.currentIndex(),
            )
        except ValueError as error:
            QMessageBox.warning(self, "Invalid subscription", str(error))
            return
        self.apply_requested.emit(self._subscription.topic_filter, updated)

    def show_feedback(self, message: str) -> None:
        self._feedback.setText(message)

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self._filter_edit,
            self._qos_combo,
            self._retain_as_published,
            self._retain_handling,
            self._apply_button,
        ):
            widget.setEnabled(enabled)

    @staticmethod
    def _compact_combo(items: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(12)
        combo.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        combo.addItems(items)
        return combo


SubscriptionEditor = SubscriptionSettingsPane
