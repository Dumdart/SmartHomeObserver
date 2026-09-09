from PySide6.QtCore import QModelIndex, QSize, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QHBoxLayout,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QToolButton,
    QTreeView,
    QWidget,
)

from topicgate.core.models.subscription import Subscription
from topicgate.app.models.broker_snapshot import BrokerSnapshot
from topicgate.presentation.snapshot_presentation import SnapshotQuery
from topicgate.gui.components.workspace_pane import WorkspacePane
from topicgate.gui.icons import delete_icon
from topicgate.presentation.topic_presentation import TopicTreeNode

TOPIC_ROLE = Qt.ItemDataRole.UserRole + 1


class ObserverTreePane(WorkspacePane):
    """Searchable tree of configured and dynamically observed MQTT topics."""

    topic_selected = Signal(str)
    add_filter_requested = Signal()
    remove_filter_requested = Signal(object)
    empty_state_action_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__("Observer Tree")
        self._advanced_mode = True
        self._scope_context = None
        self._items: dict[str, QStandardItem] = {}
        self._rendering = False

        controls = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter displayed topics…")
        self._search_edit.setAccessibleName("Search observed topics")
        self._search_edit.setClearButtonEnabled(True)
        controls.addWidget(self._search_edit, 1)

        add_button = QToolButton()
        add_button.setText("Add subscription")
        add_button.setToolTip("Add an MQTT subscription filter")
        add_button.setAccessibleName("Add MQTT subscription filter")
        add_button.clicked.connect(self.add_filter_requested)
        controls.addWidget(add_button)

        self.content_layout.addLayout(controls)
        self._scope = QLabel()
        self._scope.setObjectName("observerDisplayScope")
        self._scope.setTextFormat(Qt.TextFormat.PlainText)
        self._scope.setWordWrap(True)
        self.content_layout.addWidget(self._scope)
        self._search_status = QLabel()
        self._search_status.setObjectName("observerSearchStatus")
        self._search_status.setWordWrap(True)
        self.content_layout.addWidget(self._search_status)

        self._model = QStandardItemModel(self)
        self._model.setHorizontalHeaderLabels(["Topic", "", "State"])
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setRecursiveFilteringEnabled(True)
        self._proxy.setFilterKeyColumn(0)

        self._tree = QTreeView()
        self._tree.setObjectName("observerTree")
        self._tree.setModel(self._proxy)
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        self._tree.header().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Fixed,
        )
        self._tree.header().resizeSection(1, 34)
        self._tree.header().setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.Fixed,
        )
        self._tree.header().resizeSection(2, 136)
        self._tree.selectionModel().currentChanged.connect(
            self._selection_changed
        )
        self._tree.clicked.connect(self._topic_activated)
        self._tree.activated.connect(self._topic_activated)
        self._search_edit.textChanged.connect(self._proxy.setFilterFixedString)
        self._search_edit.textChanged.connect(self._render_search_status)
        self.content_layout.addWidget(self._tree, 1)
        self._empty_state = QFrame()
        self._empty_state.setObjectName("observerEmptyState")
        self._empty_state.setFrameShape(QFrame.Shape.StyledPanel)
        empty_layout = QHBoxLayout(self._empty_state)
        self._empty_state_text = QLabel()
        self._empty_state_text.setObjectName("observerEmptyStateText")
        self._empty_state_text.setWordWrap(True)
        empty_layout.addWidget(self._empty_state_text, 1)
        self._empty_state_action = QToolButton()
        self._empty_state_action.setObjectName("observerEmptyStateAction")
        self._empty_state_action.clicked.connect(
            lambda: self.empty_state_action_requested.emit(
                str(self._empty_state_action.property("action") or "")
            )
        )
        empty_layout.addWidget(self._empty_state_action)
        self.content_layout.addWidget(self._empty_state)

    def render(
        self,
        topic_paths: list[str],
        selected_topic: str,
        subscriptions: tuple[Subscription, ...] = (),
    ) -> None:
        self._rendering = True
        try:
            expanded_paths = {
                path
                for path, item in self._items.items()
                if self._tree.isExpanded(
                    self._proxy.mapFromSource(item.index())
                )
            }
            self._model.removeRows(0, self._model.rowCount())
            self._items.clear()

            for topic in topic_paths:
                self._add_topic(topic)

            for subscription in subscriptions:
                self._add_remove_button(subscription)

            if expanded_paths:
                self._restore_expanded_paths(expanded_paths)
            else:
                self._tree.expandToDepth(1)
            self.select_topic(selected_topic)
        finally:
            self._rendering = False

    def render_empty_state(
        self,
        connection_status: str,
        subscriptions: tuple[Subscription, ...],
        query_is_filtered: bool,
        has_cached_values: bool,
        has_topics: bool,
    ) -> None:
        """Explain why the workspace has no immediately useful live values."""
        if has_topics and not has_cached_values:
            self._empty_state.setVisible(False)
            return
        if not subscriptions:
            message, action, label = (
                "No subscriptions. Add a subscription to observe values.",
                "add-filter",
                "Add subscription",
            )
        elif connection_status == "disconnected":
            message, action, label = (
                "Broker disconnected. Stored values may be stale.",
                "connect",
                "Connect",
            )
        elif has_topics and has_cached_values:
            message, action, label = (
                "These snapshot values were restored from storage. Their receive times may predate this connection.",
                "",
                "",
            )
        elif query_is_filtered and not has_topics:
            message, action, label = (
                "No observed values match the current snapshot filters. Subscription rows remain visible. Clear display filters to inspect available values.",
                "clear-filters",
                "Clear filters",
            )
        else:
            message, action, label = (
                "No observed values in this snapshot. Subscription rows describe what TopicGate listens for; they are not received values.",
                "observe",
                "Reconnect & observe",
            )
        self._empty_state_text.setText(message)
        self._empty_state_text.setAccessibleName(message)
        self._empty_state_action.setText(label)
        self._empty_state_action.setAccessibleName(label)
        self._empty_state_action.setProperty("action", action)
        self._empty_state_action.setVisible(bool(action))
        self._empty_state.setVisible(True)

    def render_scope(
        self, query: SnapshotQuery, snapshot: BrokerSnapshot, subscription_count: int,
    ) -> None:
        self._scope_context = (query, snapshot, subscription_count)
        stored = sum(item.source.value == "stored" for item in snapshot.topics)
        age = "Unlimited" if query.max_age_seconds is None else f"{query.max_age_seconds:g} seconds"
        self._scope.setText(
            f"{subscription_count} subscriptions · {len(snapshot.topics)} snapshot values "
            f"({stored} previously stored).\n"
            f"Display: {query.topic_filter} · Maximum age: {age} · "
            f"Up to {query.result_limit} values. Live means received this session, not necessarily recent."
        )
        if not self._advanced_mode:
            bounds = []
            if query.topic_filter != "#":
                bounds.append(f"filter {query.topic_filter}")
            if query.max_age_seconds is not None:
                bounds.append(f"age up to {age}")
            if query.result_limit != SnapshotQuery().result_limit or snapshot.results.omitted:
                bounds.append(f"up to {query.result_limit} values")
            if query.payload_limit_bytes != SnapshotQuery().payload_limit_bytes:
                bounds.append(f"payload preview {query.payload_limit_bytes} bytes")
            self._scope.setText(
                f"{subscription_count} subscriptions · {len(snapshot.topics)} values ({stored} stored)."
                + ("\nView limits active: " + ", ".join(bounds) + ". Edit in View > Advanced mode." if bounds else "")
            )
        self._render_search_status()

    def set_advanced_mode(self, advanced: bool) -> None:
        self._advanced_mode = advanced
        if self._scope_context is not None:
            self.render_scope(*self._scope_context)

    def _render_search_status(self) -> None:
        active = bool(self._search_edit.text())
        self._search_status.setText(
            "No displayed topics match this text. Clear the search to restore the tree."
            if active and self._proxy.rowCount() == 0
            else "Text filter active — snapshot counts above are before this text filter."
        )
        self._search_status.setVisible(active)

    def render_tree(
        self,
        nodes: tuple[TopicTreeNode, ...],
        selected_topic: str,
        subscriptions: tuple[Subscription, ...] = (),
    ) -> None:
        paths: list[str] = []

        def append(items: tuple[TopicTreeNode, ...]) -> None:
            for item in items:
                paths.append(item.path)
                append(item.children)

        append(nodes)
        self.render(paths, selected_topic, subscriptions)

        def apply_node_presentation(items: tuple[TopicTreeNode, ...]) -> None:
            for node in items:
                item = self._items[node.path]
                if node.is_subscription:
                    item.setText(f"Subscription: {node.label}")
                item.setToolTip(
                    f"{node.path}\n"
                    + ("MQTT subscription. " if node.is_subscription else "")
                    + ("Observed value in this snapshot." if node.is_observed else "No observed value on this row.")
                )
                item.setSelectable(node.selectable)
                item.setData(node.path if node.selectable else None, TOPIC_ROLE)
                if node.badges:
                    self._set_badges(node)
                apply_node_presentation(node.children)

        apply_node_presentation(nodes)

    def select_topic(self, topic: str) -> None:
        item = self._items.get(topic)
        if item is None:
            return
        proxy_index = self._proxy.mapFromSource(item.index())
        if proxy_index.isValid():
            self._tree.setCurrentIndex(proxy_index)
            self._tree.scrollTo(proxy_index)

    def expand_all(self) -> None:
        self._tree.expandAll()

    def collapse_all(self) -> None:
        self._tree.collapseAll()

    def focus_search(self) -> None:
        self._search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_connection_busy(self, busy: bool) -> None:
        action = str(self._empty_state_action.property("action") or "")
        if action in {"connect", "observe"}:
            self._empty_state_action.setEnabled(not busy)

    def _add_topic(self, topic: str) -> None:
        parent = self._model.invisibleRootItem()
        partial_path: list[str] = []
        for segment in topic.split("/"):
            partial_path.append(segment)
            path = "/".join(partial_path)
            item = self._items.get(path)
            if item is None:
                item = QStandardItem(segment or "/")
                item.setEditable(False)
                item.setData(path, TOPIC_ROLE)
                state_item = QStandardItem()
                state_item.setEditable(False)
                action_item = QStandardItem()
                action_item.setEditable(False)
                parent.appendRow([item, state_item, action_item])
                self._items[path] = item
            parent = item

    def _add_remove_button(self, subscription: Subscription) -> None:
        item = self._items.get(subscription.topic_filter)
        if item is None:
            return

        action_widget = QWidget()
        action_layout = QHBoxLayout(action_widget)
        action_layout.setContentsMargins(5, 0, 5, 0)
        action_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button = QToolButton(action_widget)
        button.setObjectName("removeSubscriptionButton")
        button.setFixedSize(24, 18)
        button.setIconSize(QSize(12, 12))
        button.setIcon(delete_icon())
        button.setStyleSheet(
            "QToolButton {"
            " background-color: transparent;"
            " border: 1px solid transparent;"
            " border-radius: 4px;"
            " padding: 0;"
            "}"
            "QToolButton:hover {"
            " background-color: #fff5f5;"
            " border-color: #d7a4a4;"
            "}"
            "QToolButton:pressed {"
            " background-color: #fee2e2;"
            " border-color: #c77d7d;"
            "}"
        )
        button.setToolTip(f"Remove subscription {subscription.topic_filter}")
        button.setAccessibleName(
            f"Remove subscription {subscription.topic_filter}"
        )
        button.clicked.connect(
            lambda _checked=False, subscription=subscription: (
                self.remove_filter_requested.emit(subscription)
            )
        )
        action_layout.addWidget(button)
        action_index = item.index().siblingAtColumn(1)
        self._tree.setIndexWidget(
            self._proxy.mapFromSource(action_index),
            action_widget,
        )

    def _set_badges(self, node: TopicTreeNode) -> None:
        item = self._items.get(node.path)
        if item is None:
            return
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(3)
        colors = {
            "success": ("#dcfce7", "#166534"),
            "info": ("#dbeafe", "#1e40af"),
            "warning": ("#fef3c7", "#92400e"),
            "neutral": ("#e5e7eb", "#374151"),
            "filter": ("#ede9fe", "#5b21b6"),
        }
        for badge in node.badges:
            badge_widget: QLabel | QToolButton
            target_path = badge.target_path
            if target_path is not None:
                button = QToolButton()
                button.setObjectName("topicFilterBadgeButton")
                button.setText(badge.label)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setProperty("targetPath", target_path)
                button.clicked.connect(
                    lambda _checked=False, path=target_path: (
                        self.topic_selected.emit(path)
                    )
                )
                badge_widget = button
            else:
                badge_widget = QLabel(badge.label)
                badge_widget.setObjectName("topicStateBadge")
            badge_widget.setProperty("badgeKey", badge.key)
            if badge.key == "filter-reference":
                filter_label = f"Filter {badge.label.removeprefix('F')}"
                badge_widget.setToolTip(
                    f"Go to {filter_label} ({target_path})"
                )
                badge_widget.setAccessibleName(
                    f"Go to {filter_label}, {target_path}"
                )
            elif badge.key == "filter":
                badge_widget.setToolTip(
                    f"Go to {badge.label} ({target_path})"
                )
                badge_widget.setAccessibleName(
                    f"Go to {badge.label}, {target_path}"
                )
            background, foreground = colors[badge.tone]
            if target_path is not None:
                badge_widget.setStyleSheet(
                    "QToolButton {"
                    f" background: {background}; color: {foreground};"
                    " border: 0; border-radius: 5px; padding: 1px 4px;"
                    " font-size: 10px;"
                    "}"
                    "QToolButton:hover { background: #ddd6fe; }"
                    "QToolButton:pressed { background: #c4b5fd; }"
                )
            else:
                badge_widget.setStyleSheet(
                    f"background: {background}; color: {foreground}; "
                    "border-radius: 5px; padding: 1px 4px; font-size: 10px;"
                )
            layout.addWidget(badge_widget)
        layout.addStretch(1)
        state_index = item.index().siblingAtColumn(2)
        self._tree.setIndexWidget(self._proxy.mapFromSource(state_index), widget)

    def _restore_expanded_paths(self, expanded_paths: set[str]) -> None:
        for path in expanded_paths:
            item = self._items.get(path)
            if item is not None:
                self._tree.setExpanded(
                    self._proxy.mapFromSource(item.index()),
                    True,
                )

    def _selection_changed(
        self,
        current: QModelIndex,
        _previous: QModelIndex,
    ) -> None:
        if self._rendering:
            return
        source_index = self._proxy.mapToSource(current)
        topic = self._model.data(source_index, TOPIC_ROLE) or ""
        self.topic_selected.emit(str(topic))

    def _topic_activated(self, index: QModelIndex) -> None:
        if self._rendering:
            return
        source_index = self._proxy.mapToSource(index)
        topic = self._model.data(source_index, TOPIC_ROLE) or ""
        self.topic_selected.emit(str(topic))


TopicNavigationPane = ObserverTreePane
