from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PublishPane(QWidget):
    """Publish editor embedded in the topic details pane."""

    publish_requested = Signal(str, str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("topicPublishPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._connected = False
        self._busy = False
        self._selected_topic = ""
        self._topic_hint = QLabel()
        self._topic_hint.setObjectName("publishTopicHint")
        self._topic_hint.setTextFormat(Qt.TextFormat.PlainText)
        self._topic_hint.setWordWrap(True)
        layout.addWidget(self._topic_hint)
        form = QFormLayout()
        self._encoding = QComboBox()
        self._encoding.setObjectName("publishEncoding")
        self._encoding.addItem("UTF-8", "utf-8")
        self._encoding.addItem("Base64", "base64")
        form.addRow("Encoding", self._encoding)
        layout.addLayout(form)
        self._payload = QPlainTextEdit()
        self._payload.setObjectName("publishPayload")
        self._payload.setPlaceholderText("Message payload")
        self._payload.setMaximumHeight(120)
        layout.addWidget(self._payload)
        self._publish = QPushButton("Publish message")
        self._publish.setObjectName("publishButton")
        self._publish.setProperty("primary", True)
        self._publish.clicked.connect(self._emit_publish)
        self._payload.textChanged.connect(self._update_enabled)
        layout.addWidget(self._publish)

    def render(self, selected_topic: str, connected: bool, busy: bool) -> None:
        self._connected = connected
        self._busy = busy
        self._selected_topic = selected_topic
        is_filter = "+" in selected_topic or "#" in selected_topic
        if is_filter:
            self._topic_hint.setText(
                "Select a concrete topic from the Payload tab to publish."
            )
        elif not selected_topic:
            self._topic_hint.setText("Select a topic to publish a message.")
        self._topic_hint.setVisible(is_filter or not selected_topic)
        self._publish.setText("Publishing…" if busy else "Publish message")
        for widget in (self._payload, self._encoding):
            widget.setEnabled(not busy)
        self._update_enabled()

    def _emit_publish(self) -> None:
        self.publish_requested.emit(
            self._selected_topic,
            self._payload.toPlainText(),
            str(self._encoding.currentData()),
        )

    def _update_enabled(self) -> None:
        topic = self._selected_topic.strip()
        self._publish.setEnabled(
            self._connected
            and not self._busy
            and bool(topic)
            and "+" not in topic
            and "#" not in topic
            and bool(self._payload.toPlainText())
        )
