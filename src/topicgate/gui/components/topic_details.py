from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from topicgate.gui.main_view_model import MainViewModel
from topicgate.gui.components.publish_pane import PublishPane
from topicgate.gui.components.workspace_pane import (
    WORKSPACE_CONTROL_HEIGHT,
    WorkspacePane,
)
from topicgate.gui.components.topic_metadata import TopicMetadataPane
from topicgate.gui.icons import edit_icon


class TopicDetailsPane(WorkspacePane):
    """Inspect received payloads or publish a message for the selected topic."""

    topic_selected = Signal(str)
    publish_requested = Signal(str, str, str)
    subscription_editing_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__("No topic selected")

        self._context_kind = QLabel("FILTER")
        self._context_kind.setObjectName("topicContextKind")
        self._context_kind.setAccessibleName("Topic filter")
        self._context_kind.setHidden(True)
        self.header_layout.insertWidget(1, self._context_kind)

        self._edit_button = QToolButton()
        self._edit_button.setObjectName("topicEditButton")
        self._edit_button.setFixedHeight(WORKSPACE_CONTROL_HEIGHT)
        self._edit_button.setIcon(edit_icon())
        self._edit_button.setIconSize(QSize(14, 14))
        self._edit_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._edit_button.setText("Settings")
        self._edit_button.setCheckable(True)
        self._edit_button.setAccessibleName("Topic settings")
        self._edit_button.setToolTip("Show subscription and expectation settings")
        self._edit_button.toggled.connect(self._toggle_subscription_editing)
        self._mode_tabs = QTabBar()
        self._mode_tabs.setObjectName("topicDetailsMode")
        self._mode_tabs.setFixedHeight(WORKSPACE_CONTROL_HEIGHT)
        self._mode_tabs.setAccessibleName("Topic details mode")
        self._mode_tabs.setDrawBase(False)
        self._mode_tabs.setExpanding(True)
        self._mode_tabs.addTab("Payload")
        self._mode_tabs.addTab("Publish")
        self._mode_tabs.currentChanged.connect(self._set_mode)
        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(8)
        mode_layout.addWidget(self._mode_tabs, 1)
        mode_layout.addWidget(self._edit_button)
        self.content_layout.addLayout(mode_layout)

        self._payload_content = QWidget()
        self._payload_content.setObjectName("topicPayloadContent")
        payload_layout = QVBoxLayout(self._payload_content)
        payload_layout.setContentsMargins(0, 0, 0, 0)

        self._filter_summary = QWidget()
        self._filter_summary.setObjectName("subscriptionFilterSummary")
        filter_layout = QVBoxLayout(self._filter_summary)
        filter_layout.setContentsMargins(0, 0, 0, 0)

        filter_form = QFormLayout()
        self._filter_labels = {
            name: self._plain_label(object_name)
            for name, object_name in (
                ("topics", "filterMatchingTopicCount"),
                ("messages", "filterMessageCount"),
                ("states", "filterStateCounts"),
                ("retained", "filterRetainedCount"),
            )
        }
        filter_form.addRow("Topics", self._filter_labels["topics"])
        filter_form.addRow("Messages", self._filter_labels["messages"])
        filter_form.addRow("States", self._filter_labels["states"])
        filter_form.addRow("Retained", self._filter_labels["retained"])
        filter_layout.addLayout(filter_form)

        self._filter_topics = QTableWidget(0, 2)
        self._filter_topics.setObjectName("filterMatchingTopics")
        self._filter_topics.setHorizontalHeaderLabels(
            ["Topic", "Last received"]
        )
        self._filter_topics.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._filter_topics.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._filter_topics.setAlternatingRowColors(True)
        self._filter_topics.verticalHeader().setVisible(False)
        header = self._filter_topics.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self._filter_topics.cellClicked.connect(self._select_filter_topic)
        filter_layout.addWidget(self._filter_topics, 1)

        self._filter_scope = QLabel(
            "Current snapshot only. Topics omitted by snapshot filters, age, "
            "or result limits are not included."
        )
        self._filter_scope.setObjectName("filterSummaryScope")
        self._filter_scope.setTextFormat(Qt.TextFormat.PlainText)
        self._filter_scope.setWordWrap(True)
        filter_layout.addWidget(self._filter_scope)
        self._filter_summary.setHidden(True)
        payload_layout.addWidget(self._filter_summary, 1)

        self._metadata = TopicMetadataPane()
        self._metadata.advanced_changed.connect(self._set_advanced_visible)
        payload_layout.addWidget(self._metadata)

        self._decoded_label = self._section_label("Decoded payload")
        payload_layout.addWidget(self._decoded_label)
        self._decoded_payload = QPlainTextEdit()
        self._decoded_payload.setObjectName("decodedPayload")
        self._decoded_payload.setReadOnly(True)
        self._decoded_payload.setPlaceholderText(
            "Select a topic to inspect its payload"
        )
        payload_layout.addWidget(self._decoded_payload, 3)

        self._raw_label = self._section_label("Raw payload (hex)")
        payload_layout.addWidget(self._raw_label)
        self._raw_payload = QPlainTextEdit()
        self._raw_payload.setObjectName("rawPayload")
        self._raw_payload.setReadOnly(True)
        self._raw_payload.setMaximumHeight(110)
        payload_layout.addWidget(self._raw_payload, 1)

        self._publish_pane = PublishPane()
        self._publish_pane.publish_requested.connect(self.publish_requested)
        self.content_layout.addWidget(self._payload_content, 1)
        self.content_layout.addWidget(self._publish_pane, 1)
        self._showing_filter = False
        self._advanced_visible = False
        self._publish_mode = False
        self._publish_pane.setHidden(True)
        self._update_raw_visibility()

    def render(self, view_model: MainViewModel) -> None:
        subscription = view_model.selected_subscription
        summary = view_model.selected_wildcard_filter_summary
        showing_filter = summary is not None
        selected_path = view_model.topic
        heading = selected_path or "No topic selected"
        if not selected_path:
            accessible_name = "No topic selected"
        else:
            context_name = "topic filter" if showing_filter else "topic"
            accessible_name = f"Selected {context_name}: {heading}"
        self.set_heading(
            heading,
            accessible_name,
        )
        self._context_kind.setVisible(showing_filter)
        self._edit_button.setEnabled(subscription is not None)
        if subscription is None and self._edit_button.isChecked():
            self._edit_button.setChecked(False)

        self._publish_pane.render(
            view_model.topic,
            view_model.connection_status == "connected",
            view_model.is_busy("publish"),
        )
        self._showing_filter = showing_filter
        self._filter_summary.setVisible(showing_filter)
        for widget in (
            self._metadata,
            self._decoded_label,
            self._decoded_payload,
        ):
            widget.setVisible(not showing_filter)
        self._update_raw_visibility()
        if summary is not None:
            self._filter_labels["topics"].setText(
                str(summary.matching_topic_count)
            )
            self._filter_labels["messages"].setText(str(summary.message_count))
            state_counts = (
                ("Live", summary.live_count),
                ("Cached", summary.cached_count),
                ("Stale", summary.stale_count),
            )
            self._filter_labels["states"].setText(
                " · ".join(
                    f"{label} {count}"
                    for label, count in state_counts
                    if count
                )
                or "None"
            )
            self._filter_labels["retained"].setText(str(summary.retained_count))
            self._filter_topics.setRowCount(len(summary.topics))
            for row, topic in enumerate(summary.topics):
                values = (
                    topic.topic,
                    topic.received_at,
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, topic.topic)
                    self._filter_topics.setItem(row, column, item)
            return

        detail = view_model.topic_detail
        self._metadata.render(detail)
        self._decoded_payload.setPlainText(detail.decoded_payload)
        self._raw_payload.setPlainText(detail.raw_payload)

    @property
    def is_editing_subscription(self) -> bool:
        return self._edit_button.isChecked()

    def focus_payload(self) -> None:
        self._decoded_payload.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_advanced_visible(self, visible: bool) -> None:
        self._advanced_visible = visible
        self._update_raw_visibility()

    def _update_raw_visibility(self) -> None:
        visible = (
            self._advanced_visible
            and not self._showing_filter
            and not self._publish_mode
        )
        self._raw_label.setVisible(visible)
        self._raw_payload.setVisible(visible)

    def _set_mode(self, index: int) -> None:
        self._publish_mode = index == 1
        self._payload_content.setVisible(not self._publish_mode)
        self._publish_pane.setVisible(self._publish_mode)
        self._update_raw_visibility()

    def _toggle_subscription_editing(self, editing: bool) -> None:
        self._edit_button.setToolTip(
            "Hide topic settings"
            if editing
            else "Show subscription and expectation settings"
        )
        self.subscription_editing_changed.emit(editing)

    def _select_filter_topic(self, row: int, _column: int) -> None:
        item = self._filter_topics.item(row, 0)
        if item is not None:
            self.topic_selected.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 600;")
        return label

    @staticmethod
    def _plain_label(name: str) -> QLabel:
        label = QLabel("0")
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        return label


# Backward-compatible name while the pane acts as the primary content component.
TopicContentPane = TopicDetailsPane
