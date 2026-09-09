from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFormLayout, QLabel, QToolButton, QWidget

from topicgate.presentation.topic_presentation import TopicDetail


class TopicMetadataPane(QWidget):
    """Compact metadata renderer for the selected topic detail."""

    advanced_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._form = QFormLayout(self)
        self._form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self.received = self._label("receivedAtLabel")
        self.age = self._label("topicAgeLabel")
        self.source = self._label("observationSourceLabel")
        self.status = self._label("topicStateStatusLabel")
        self.encoding = self._label("payloadEncodingLabel")
        self.size = self._label("payloadSizeLabel")
        self.original_size = self._label("originalPayloadSizeLabel")
        self.available_size = self._label("availablePayloadSizeLabel")
        self.rendered_size = self._label("renderedPayloadSizeLabel")
        self.ingestion_truncation = self._label("ingestionTruncationLabel")
        self.rendering_truncation = self._label("renderingTruncationLabel")
        self.qos = self._label("qosLabel")
        self.retained = self._label("retainedLabel")
        self.messages = self._label("messageCountLabel", "0")
        self.dropped = self._label("droppedMessageCountLabel", "0")
        rows = (
            ("Last received", self.received),
            ("Age", self.age),
            ("Observation source", self.source),
            ("State", self.status),
            ("Encoding", self.encoding),
            ("Payload size", self.size),
            ("Original payload", self.original_size),
            ("Available payload", self.available_size),
            ("Rendered payload", self.rendered_size),
            ("Ingestion truncation", self.ingestion_truncation),
            ("Rendering truncation", self.rendering_truncation),
            ("QoS", self.qos),
            ("Retained", self.retained),
            ("Message count", self.messages),
            ("Dropped messages", self.dropped),
        )
        for title, widget in rows:
            self._form.addRow(title, widget)

        self._advanced_widgets = (
            self.source,
            self.encoding,
            self.size,
            self.original_size,
            self.available_size,
            self.rendered_size,
            self.ingestion_truncation,
            self.rendering_truncation,
            self.qos,
            self.dropped,
        )
        self._advanced_button = QToolButton()
        self._advanced_button.setObjectName("topicMetadataAdvancedButton")
        self._advanced_button.setCheckable(True)
        self._advanced_button.setText("Advanced")
        self._advanced_button.setAccessibleName("Show advanced topic details")
        self._advanced_button.toggled.connect(self._set_advanced_visible)
        self._form.addRow(self._advanced_button)
        self._set_advanced_visible(False)

    def render(self, detail: TopicDetail) -> None:
        self.received.setText(detail.received_at)
        self.age.setText(detail.age_label)
        self.source.setText(detail.source_label)
        self.status.setText(detail.status_label)
        self.encoding.setText(detail.payload_encoding)
        self.size.setText(detail.payload_size_label)
        self.original_size.setText(detail.original_payload_size_label)
        self.available_size.setText(detail.available_payload_size_label)
        self.rendered_size.setText(
            f"{detail.rendered_payload_size} bytes"
            if detail.has_value
            else "-"
        )
        self.ingestion_truncation.setText(detail.ingestion_truncation_label)
        self.rendering_truncation.setText(detail.rendering_truncation_label)
        self.qos.setText(detail.qos_label)
        self.retained.setText(detail.retain_label)
        self.messages.setText(str(detail.message_count))
        self.dropped.setText(str(detail.dropped_message_count))

    def _set_advanced_visible(self, visible: bool) -> None:
        for widget in self._advanced_widgets:
            self._form.setRowVisible(widget, visible)
        self._advanced_button.setText(
            "Hide advanced" if visible else "Advanced"
        )
        self._advanced_button.setAccessibleName(
            "Hide advanced topic details"
            if visible
            else "Show advanced topic details"
        )
        self.advanced_changed.emit(visible)

    @staticmethod
    def _label(name: str, text: str = "-") -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label
