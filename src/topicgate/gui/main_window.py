import asyncio
from collections.abc import Coroutine
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.subscription import Subscription
from topicgate.gui.components.about_dialog import AboutDialog
from topicgate.gui.components.add_subscription_dialog import AddSubscriptionDialog
from topicgate.gui.components.broker_settings_dialog import (
    BrokerSettingsDialog,
)
from topicgate.gui.components.broker_connection import BrokerConnectionPane
from topicgate.gui.components.connection_controls import ConnectionControls
from topicgate.gui.components.log_console import LogConsoleDock
from topicgate.gui.components.expectation_editor import ExpectationEditor
from topicgate.gui.components.health_inspector import HealthInspector
from topicgate.gui.components.diagnostic_profile_editor import DiagnosticProfileEditorWindow
from topicgate.gui.components.mcp_setup_dialog import McpSetupDialog
from topicgate.gui.components.observer_tree import ObserverTreePane
from topicgate.gui.components.onboarding_panel import OnboardingPanel
from topicgate.gui.components.subscription_settings import (
    SubscriptionSettingsPane,
)
from topicgate.gui.components.stored_observations_dialog import (
    StoredObservationsDialog,
)
from topicgate.gui.components.support_bundle_export_dialog import (
    SupportBundleExportDialog,
    suggested_support_bundle_filename,
)
from topicgate.gui.components.topic_details import TopicDetailsPane
from topicgate.gui.components.workspace_pane import (
    WORKSPACE_CONTROL_HEIGHT,
    WorkspacePane,
)
from topicgate.gui.components.snapshot_panel import SnapshotPanel
from topicgate.gui.main_view_model import MainViewModel
from topicgate.gui.settings_migration import migrate_legacy_settings
from topicgate.gui.theme import LIGHT_THEME
from topicgate.presentation.snapshot_presentation import SnapshotQuery
from topicgate.paths import asset_path


class _FullWidthTabWidget(QTabWidget):
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.tabBar().setFixedWidth(self.contentsRect().width())


class MainWindow(QMainWindow):
    """Compose and coordinate the observer workspace."""

    def __init__(
        self,
        view_model: MainViewModel,
        settings: QSettings | None = None,
        *,
        diagnostic_profile_editor=None,
    ) -> None:
        super().__init__()

        self.setWindowIcon(QIcon(asset_path("icon.png")))

        self._view_model = view_model
        self._diagnostic_profile_editor = diagnostic_profile_editor
        self._diagnostic_profile_editor_window = None
        self._operation_tasks: set[asyncio.Task[None]] = set()
        self._accepting_operations = True
        self._settings = settings or QSettings()
        self._stored_observations_dialog: StoredObservationsDialog | None = None
        self._mcp_setup_dialog: McpSetupDialog | None = None
        self._support_bundle_export_dialog: (
            SupportBundleExportDialog | None
        ) = None
        if settings is None:
            migrate_legacy_settings(self._settings)
        self.setWindowTitle(view_model.title)
        self.setObjectName("mainWindow")
        self.setStyleSheet(LIGHT_THEME)

        self._create_workspace()
        self._create_actions()
        self._create_menu_bar()
        self._create_log_dock()
        self._connect_view_model()
        self._restore_state()
        self._render_all()

    def _create_workspace(self) -> None:
        self._observer_tree = ObserverTreePane()
        self._snapshot_panel = SnapshotPanel()
        self._topic_details = TopicDetailsPane()
        self._health_inspector = HealthInspector(self._view_model)
        self._broker_connection = BrokerConnectionPane()
        self._subscription_settings = SubscriptionSettingsPane()
        self._topic_expectations = ExpectationEditor(
            self._view_model,
            "topic",
        )
        self._onboarding = OnboardingPanel()
        self._observer_tree.topic_selected.connect(self._select_topic)
        self._topic_details.topic_selected.connect(self._select_topic)
        self._topic_details.subscription_editing_changed.connect(
            self._set_context_panel_visible
        )
        self._topic_details.expectations_requested.connect(
            self._show_topic_expectations
        )
        self._health_inspector.topic_requested.connect(self._select_topic)
        self._health_inspector.expectation_edit_requested.connect(
            self._edit_health_expectation
        )
        self._observer_tree.add_filter_requested.connect(
            self._show_add_filter_dialog
        )
        self._observer_tree.remove_filter_requested.connect(
            self._remove_subscription
        )
        self._snapshot_panel.apply_requested.connect(
            self._apply_snapshot_query
        )
        self._snapshot_panel.reset_requested.connect(
            self._reset_snapshot_query
        )
        self._snapshot_panel.reconnect_observe_requested.connect(
            self._confirm_reconnect_and_observe
        )
        self._snapshot_panel.validation_failed.connect(
            lambda message: QMessageBox.warning(
                self,
                "Invalid snapshot controls",
                message,
            )
        )
        self._observer_tree.empty_state_action_requested.connect(
            self._handle_empty_state_action
        )
        self._onboarding.configure_broker_requested.connect(
            self._show_broker_settings_dialog
        )
        self._onboarding.test_connection_requested.connect(
            lambda: self._run_async(self._view_model.connect_to_broker())
        )
        self._onboarding.add_subscription_requested.connect(
            self._show_add_filter_dialog
        )
        self._onboarding.observe_requested.connect(
            self._confirm_reconnect_and_observe
        )
        self._onboarding.configure_mcp_requested.connect(self._show_mcp_setup)
        self._onboarding.dismissed.connect(self._dismiss_onboarding)
        self._subscription_settings.apply_requested.connect(
            self._apply_subscription
        )
        self._topic_details.publish_requested.connect(
            lambda topic, payload, encoding: self._run_async(
                self._view_model.publish_message(topic, payload, encoding)
            )
        )

        self._context_panel = WorkspacePane(
            "Settings",
            minimum_hint_width=220,
        )
        self._context_panel.setObjectName("contextPanel")
        self._settings_tabs = _FullWidthTabWidget()
        self._settings_tabs.setObjectName("topicSettingsTabs")
        self._settings_tabs.tabBar().setObjectName("topicSettingsTabs")
        self._settings_tabs.tabBar().setExpanding(True)
        self._settings_tabs.tabBar().setFixedHeight(WORKSPACE_CONTROL_HEIGHT)
        self._settings_tabs.addTab(self._subscription_settings, "Subscription")
        self._settings_tabs.addTab(self._topic_expectations, "Expectations")
        self._context_panel.content_layout.addWidget(self._settings_tabs)

        self._observer_workspace = QWidget()
        self._observer_workspace.setObjectName("observerWorkspace")
        observer_layout = QVBoxLayout(self._observer_workspace)
        observer_layout.setContentsMargins(0, 0, 0, 0)
        observer_layout.setSpacing(8)
        observer_layout.addWidget(self._broker_connection)
        observer_layout.addWidget(self._observer_tree, 1)

        self._topic_inspector = QWidget()
        self._topic_inspector.setObjectName("topicInspector")
        inspector_layout = QVBoxLayout(self._topic_inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        self._inspector_stack = QStackedWidget()
        self._inspector_stack.setObjectName("inspectorStack")
        snapshot_scroll = QScrollArea()
        snapshot_scroll.setObjectName("snapshotPanelScrollArea")
        snapshot_scroll.setWidgetResizable(True)
        snapshot_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        snapshot_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        snapshot_scroll.setWidget(self._snapshot_panel)
        self._inspector_stack.addWidget(snapshot_scroll)
        self._inspector_stack.addWidget(self._topic_details)
        self._inspector_stack.addWidget(self._health_inspector)
        inspector_layout.addWidget(self._inspector_stack)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setObjectName("workspaceSplitter")
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._observer_workspace)
        self._splitter.addWidget(self._topic_inspector)
        self._splitter.addWidget(self._context_panel)
        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 4)
        self._splitter.setStretchFactor(2, 2)
        root = QWidget()
        root.setObjectName("applicationRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)
        root_layout.addWidget(self._onboarding)
        root_layout.addWidget(self._splitter, 1)
        self.setCentralWidget(root)
        self.setMinimumSize(1024, 640)
        self.resize(1280, 800)
        self._splitter.setSizes([330, 580, 330])
        self._context_panel.setHidden(True)

    def _set_context_panel_visible(self, visible: bool) -> None:
        self._context_panel.setVisible(
            visible and self._inspector_stack.currentWidget() is self._topic_details
        )

    def _show_snapshot(self) -> None:
        self._inspector_stack.setCurrentIndex(0)
        self._context_panel.setHidden(True)

    def _show_topic_details(self) -> None:
        self._inspector_stack.setCurrentWidget(self._topic_details)
        self._context_panel.setVisible(
            self._topic_details.is_editing_subscription
        )

    def _show_health(self) -> None:
        self._inspector_stack.setCurrentWidget(self._health_inspector)
        self._context_panel.setHidden(True)
        self._schedule_health_refresh()

    def _show_diagnostic_profiles(self) -> None:
        if self._diagnostic_profile_editor is None:
            return
        broker_id = self._view_model.active_broker_profile.id
        if self._diagnostic_profile_editor_window is None:
            self._diagnostic_profile_editor_window = DiagnosticProfileEditorWindow(
                self._diagnostic_profile_editor, broker_id, self
            )
        else:
            self._diagnostic_profile_editor_window.set_broker(broker_id)
        self._diagnostic_profile_editor_window.show()
        self._diagnostic_profile_editor_window.raise_()
        self._diagnostic_profile_editor_window.activateWindow()

    def _show_topic_expectations(self) -> None:
        self._show_topic_details()
        self._topic_details.set_settings_visible(True)
        self._settings_tabs.setCurrentWidget(self._topic_expectations)

    def _edit_health_expectation(
        self,
        topic: str,
        expectation_id: object,
    ) -> None:
        if topic:
            self._select_topic(topic)
            self._show_topic_expectations()
            self._topic_expectations.select_expectation(expectation_id)
        else:
            self._health_inspector.select_broker_expectation(expectation_id)

    def _select_topic(self, topic: str) -> None:
        if topic:
            self._show_topic_details()
        else:
            self._show_snapshot()
        self._view_model.select_topic(topic)

    def _create_actions(self) -> None:
        self._broker_settings_action = QAction("&Edit broker profile...", self)
        self._broker_settings_action.setObjectName("brokerSettingsAction")
        self._broker_settings_action.setToolTip("Edit the active broker profile")
        self._broker_settings_action.triggered.connect(
            self._show_broker_settings_dialog
        )
        self._add_broker_profile_action = QAction("&Add broker profile...", self)
        self._add_broker_profile_action.setObjectName("addBrokerProfileAction")
        self._add_broker_profile_action.triggered.connect(
            self._show_create_broker_profile_dialog
        )
        self._delete_broker_profile_action = QAction(
            "&Delete broker profile...",
            self,
        )
        self._delete_broker_profile_action.setObjectName(
            "deleteBrokerProfileAction"
        )
        self._delete_broker_profile_action.triggered.connect(
            self._confirm_delete_broker_profile
        )
        self._connection_controls = ConnectionControls(
            self._add_broker_profile_action,
            self._broker_settings_action,
            self._delete_broker_profile_action,
            self,
        )
        self._connection_controls.broker_selected.connect(
            self._confirm_broker_profile_switch
        )
        self._connection_controls.edit_profile_requested.connect(
            self._show_broker_settings_dialog
        )
        self._connection_controls.connect_requested.connect(
            lambda: self._run_async(self._view_model.connect_to_broker())
        )
        self._connection_controls.reconnect_requested.connect(
            self._confirm_reconnect_and_observe
        )
        self._connection_controls.disconnect_requested.connect(
            lambda: self._run_async(self._view_model.disconnect_from_broker())
        )
        self._broker_connection.broker_selected.connect(
            self._confirm_broker_profile_switch
        )
        self._broker_connection.edit_profile_requested.connect(
            self._show_broker_settings_dialog
        )
        self._broker_connection.add_profile_requested.connect(
            self._show_create_broker_profile_dialog
        )
        self._broker_connection.delete_profile_requested.connect(
            self._confirm_delete_broker_profile
        )
        self._broker_connection.connect_requested.connect(
            lambda: self._run_async(self._view_model.connect_to_broker())
        )
        self._broker_connection.reconnect_requested.connect(
            self._confirm_reconnect_and_observe
        )
        self._broker_connection.disconnect_requested.connect(
            lambda: self._run_async(self._view_model.disconnect_from_broker())
        )
        self._broker_connection.inspect_snapshot_requested.connect(
            self._show_snapshot
        )
        self._broker_connection.health_requested.connect(self._show_health)

        self._add_filter_action = QAction("Add filter", self)
        self._add_filter_action.setShortcut("Ctrl+N")
        self._add_filter_action.setToolTip("Add an MQTT subscription filter")
        self._add_filter_action.triggered.connect(self._show_add_filter_dialog)

        self._expand_action = QAction("Expand all", self)
        self._expand_action.triggered.connect(self._observer_tree.expand_all)
        self._collapse_action = QAction("Collapse all", self)
        self._collapse_action.triggered.connect(self._observer_tree.collapse_all)

        self._console_action = QAction("Log console", self)
        self._console_action.setCheckable(True)

        self._stored_observations_action = QAction(
            "Stored observations",
            self,
        )
        self._stored_observations_action.setObjectName(
            "storedObservationsAction"
        )
        self._stored_observations_action.setShortcut("Ctrl+Shift+S")
        self._stored_observations_action.triggered.connect(
            self._show_stored_observations
        )

        self._health_action = QAction("Health", self)
        self._health_action.setObjectName("healthAction")
        self._health_action.setShortcut("Ctrl+Shift+H")
        self._health_action.triggered.connect(self._show_health)

        self._diagnostic_profiles_action = QAction("Diagnostic profiles...", self)
        self._diagnostic_profiles_action.setObjectName("diagnosticProfilesAction")
        self._diagnostic_profiles_action.setEnabled(
            self._diagnostic_profile_editor is not None
        )
        self._diagnostic_profiles_action.triggered.connect(
            self._show_diagnostic_profiles
        )

        self._quit_action = QAction("Quit", self)
        self._quit_action.setShortcut("Ctrl+Q")
        self._quit_action.triggered.connect(self.close)

        self._about_action = QAction("About TopicGate", self)
        self._about_action.setObjectName("aboutAction")
        self._about_action.triggered.connect(self._show_about_dialog)

        self._mcp_setup_action = QAction("MCP setup...", self)
        self._mcp_setup_action.setObjectName("mcpSetupAction")
        self._mcp_setup_action.setToolTip("Show TopicGate MCP client configuration")
        self._mcp_setup_action.setShortcut("Ctrl+Shift+M")
        self._mcp_setup_action.triggered.connect(self._show_mcp_setup)
        self._support_bundle_export_action = QAction(
            "Export support bundle…",
            self,
        )
        self._support_bundle_export_action.setObjectName(
            "supportBundleExportAction"
        )
        self._support_bundle_export_action.setToolTip(
            "Export bounded, redacted diagnostics for support"
        )
        self._support_bundle_export_action.triggered.connect(
            self._show_support_bundle_export
        )
        self._focus_topic_search_action = QAction("Focus topic search", self)
        self._focus_topic_search_action.setShortcut("Ctrl+F")
        self._focus_topic_search_action.triggered.connect(
            self._observer_tree.focus_search
        )
        self.addAction(self._focus_topic_search_action)

    def _create_menu_bar(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self._add_filter_action)
        file_menu.addSeparator()
        file_menu.addAction(self._quit_action)

        self.menuBar().addAction(self._stored_observations_action)
        self._view_menu: QMenu = self.menuBar().addMenu("&View")
        self._view_menu.addAction(self._health_action)
        self._view_menu.addAction(self._diagnostic_profiles_action)
        self._view_menu.addSeparator()
        self._view_menu.addAction(self._expand_action)
        self._view_menu.addAction(self._collapse_action)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._mcp_setup_action)
        help_menu.addAction(self._support_bundle_export_action)
        help_menu.addSeparator()
        help_menu.addAction(self._about_action)

    def _create_log_dock(self) -> None:
        self._log_dock = LogConsoleDock(self)
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea,
            self._log_dock,
        )
        self._console_action.setChecked(self._log_dock.isVisible())
        self._console_action.toggled.connect(self._log_dock.setVisible)
        self._log_dock.visibilityChanged.connect(self._console_action.setChecked)
        self._view_menu.addSeparator()
        self._view_menu.addAction(self._console_action)

    def _show_about_dialog(self) -> None:
        AboutDialog(self).open()

    def _show_mcp_setup(self) -> None:
        dialog = McpSetupDialog(self._view_model, self)
        self._mcp_setup_dialog = dialog
        dialog.reconnect_observe_requested.connect(
            self._confirm_reconnect_and_observe
        )
        dialog.finished.connect(self._mark_mcp_configured)
        dialog.destroyed.connect(
            lambda: setattr(self, "_mcp_setup_dialog", None)
        )
        dialog.open()

    def _show_support_bundle_export(self) -> None:
        dialog = SupportBundleExportDialog(self._view_model, self)
        self._support_bundle_export_dialog = dialog
        dialog.accepted.connect(
            lambda: self._choose_support_bundle_destination(
                dialog.include_payloads
            )
        )
        dialog.destroyed.connect(
            lambda: setattr(self, "_support_bundle_export_dialog", None)
        )
        dialog.open()

    def _choose_support_bundle_destination(self, include_payloads: bool) -> None:
        if include_payloads:
            confirmed = QMessageBox.question(
                self,
                "Include MQTT payloads?",
                "MQTT payloads can contain personal data, access tokens, device "
                "identifiers, and other secrets. TopicGate only bounds their size; "
                "it cannot determine whether their contents are safe to share.\n\n"
                "Include payloads in this support bundle?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmed != QMessageBox.StandardButton.Yes:
                return
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export support bundle",
            str(Path.home() / suggested_support_bundle_filename()),
            "ZIP archives (*.zip)",
        )
        if not destination:
            return
        self._run_async(
            self._export_support_bundle(Path(destination), include_payloads)
        )

    async def _export_support_bundle(
        self,
        destination: Path,
        include_payloads: bool,
    ) -> None:
        result = await self._view_model.export_support_bundle(
            destination,
            include_payloads=include_payloads,
        )
        details = (
            "No collection, omission, or truncation warnings were reported."
            if not result.warnings
            else "Warnings and limitations:\n- " + "\n- ".join(result.warnings)
        )
        QMessageBox.information(
            self,
            "Support bundle exported",
            f"Saved to:\n{result.destination}\n\n{details}\n\n"
            "Review the archive before sharing it.",
        )

    def _mark_mcp_configured(self, _result: int) -> None:
        self._settings.setValue("onboarding/mcpConfigured", True)
        self._render_onboarding()

    def _dismiss_onboarding(self) -> None:
        self._settings.setValue("onboarding/dismissed", True)
        self._onboarding.setVisible(False)

    def _handle_empty_state_action(self, action: str) -> None:
        if action == "add-filter":
            self._show_add_filter_dialog()
        elif action == "connect":
            self._run_async(self._view_model.connect_to_broker())
        elif action == "clear-filters":
            self._reset_snapshot_query()
        elif action == "observe":
            self._confirm_reconnect_and_observe()

    def _connect_view_model(self) -> None:
        self._view_model.state_changed.connect(self._render_details)
        self._view_model.topics_changed.connect(self._render_tree)
        self._view_model.subscriptions_changed.connect(self._render_settings)
        self._view_model.connection_changed.connect(self._render_connection)
        self._view_model.configuration_changed.connect(self._render_broker_profiles)
        self._view_model.operation_state_changed.connect(self._render_operation_state)
        self._view_model.operation_failed.connect(self._show_operation_error)
        self._view_model.log_message.connect(self._log_dock.append_message)
        self._view_model.health_changed.connect(
            self._render_health_presentations
        )
        self._health_refresh_timer = QTimer(self)
        self._health_refresh_timer.setObjectName("healthRefreshTimer")
        self._health_refresh_timer.setSingleShot(True)
        self._health_refresh_timer.setInterval(750)
        self._health_refresh_timer.timeout.connect(self._refresh_health_summary)
        self._view_model.topics_changed.connect(self._schedule_health_refresh)
        self._view_model.connection_changed.connect(self._schedule_health_refresh)
        self._view_model.configuration_changed.connect(
            self._schedule_health_refresh
        )

    def _render_all(self) -> None:
        self._snapshot_panel.render_query(
            self._view_model.snapshot_query
        )
        self._render_tree()
        self._render_details()
        self._render_settings()
        self._render_connection()
        self._render_broker_profiles()
        self._render_onboarding()
        self._schedule_health_refresh()

    def _render_tree(self) -> None:
        self._observer_tree.render_tree(
            self._view_model.topic_tree,
            self._view_model.topic,
            self._view_model.subscriptions,
        )
        self._snapshot_panel.render_health(
            self._view_model.snapshot_health
        )
        snapshot = self._view_model.broker_snapshot
        self._observer_tree.render_empty_state(
            self._view_model.connection_status,
            self._view_model.subscriptions,
            self._snapshot_query_is_filtered(),
            bool(snapshot.topics)
            and all(item.source.value == "stored" for item in snapshot.topics),
            bool(snapshot.topics),
        )
        self._observer_tree.set_connection_busy(
            self._view_model.is_busy("broker")
            or self._view_model.is_busy("connection")
        )
        self._render_broker_connection()

    def _render_details(self) -> None:
        self._topic_details.render(self._view_model)
        self._topic_expectations.render()
        if (
            not self._view_model.topic
            and self._inspector_stack.currentWidget() is self._topic_details
        ):
            self._show_snapshot()

    def _render_settings(self) -> None:
        self._subscription_settings.render(
            self._view_model.topic,
            self._view_model.selected_subscription,
        )
        self._topic_expectations.render()
        self._render_broker_connection()

    def _render_connection(self) -> None:
        if self._view_model.connection_status == "connected":
            self._settings.setValue("onboarding/connectionTested", True)
        self._render_connection_controls()
        self._snapshot_panel.render_connection_status(
            self._view_model.connection_status
        )
        self._topic_details.render(self._view_model)
        self._render_onboarding()

    def _render_broker_profiles(self) -> None:
        self._render_connection_controls()
        self._render_onboarding()

    def _render_connection_controls(self, busy: bool | None = None) -> None:
        if busy is None:
            busy = self._view_model.is_busy("broker") or self._view_model.is_busy(
                "connection"
            )
        self._connection_controls.render(
            self._view_model.broker_profiles,
            self._view_model.active_broker_profile,
            self._view_model.connection_status,
            busy,
        )
        self._render_broker_connection(busy)

    def _render_broker_connection(self, busy: bool | None = None) -> None:
        if busy is None:
            busy = self._view_model.is_busy("broker") or self._view_model.is_busy(
                "connection"
            )
        self._broker_connection.render(self._view_model, busy)

    def _render_health_presentations(self) -> None:
        self._render_broker_connection()
        self._topic_details.render(self._view_model)

    def _schedule_health_refresh(self) -> None:
        if self._view_model.health_reporting_available:
            self._health_refresh_timer.start()

    def _refresh_health_summary(self) -> None:
        try:
            self._view_model.refresh_health()
        except Exception as error:
            self._view_model.log_message.emit(
                f"Health evaluation unavailable: {error}"
            )

    def _render_operation_state(self) -> None:
        self._topic_details.render(self._view_model)
        busy = self._view_model.is_busy("subscription")
        self._subscription_settings.setEnabled(not busy)
        self._topic_expectations.setEnabled(not busy)
        exclusive_busy = (
            self._view_model.is_busy("broker")
            or self._view_model.is_busy("connection")
            or self._view_model.is_busy("stored-observations")
        )
        self._snapshot_panel.set_busy(
            exclusive_busy
        )
        self._observer_tree.set_connection_busy(exclusive_busy)
        self._stored_observations_action.setEnabled(
            not exclusive_busy
        )
        self._support_bundle_export_action.setEnabled(
            not self._view_model.any_operation_busy
        )
        self._render_connection_controls(exclusive_busy)
        self._render_onboarding()

    def _render_onboarding(self) -> None:
        if self._settings.value("onboarding/dismissed", False, type=bool):
            self._onboarding.setVisible(False)
            return
        snapshot = self._view_model.broker_snapshot
        self._onboarding.render(
            {
                "broker": self._settings.value(
                    "onboarding/brokerConfigured", False, type=bool
                ),
                "connection": self._settings.value(
                    "onboarding/connectionTested", False, type=bool
                ) or self._view_model.connection_status == "connected",
                "subscription": bool(self._view_model.subscriptions),
                "observe": bool(snapshot.topics),
                "mcp": self._settings.value(
                    "onboarding/mcpConfigured", False, type=bool
                ),
            },
            busy=(
                self._view_model.is_busy("broker")
                or self._view_model.is_busy("connection")
            ),
        )

    def _snapshot_query_is_filtered(self) -> bool:
        query = self._view_model.snapshot_query
        return (
            query.topic_filter != "#"
            or query.max_age_seconds is not None
            or query.result_limit != SnapshotQuery().result_limit
            or query.payload_limit_bytes != SnapshotQuery().payload_limit_bytes
        )

    def _show_stored_observations(self) -> None:
        dialog = self._stored_observations_dialog
        if dialog is None:
            dialog = StoredObservationsDialog(self._view_model, self)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.destroyed.connect(
                lambda: setattr(self, "_stored_observations_dialog", None)
            )
            dialog.save_policy_requested.connect(
                lambda policy: self._run_async(
                    self._preview_and_save_retention_policy(policy)
                )
            )
            dialog.broker_requested.connect(
                lambda broker_id: self._run_async(
                    self._view_model.load_stored_observations(broker_id)
                )
            )
            dialog.deletion_requested.connect(
                lambda scope, broker_id, topics: self._run_async(
                    self._preview_and_delete_cache(
                        scope,
                        broker_id,
                        topics,
                    )
                )
            )
            dialog.query_requested.connect(
                lambda broker_id, topic_filter, after, before, order, limit: (
                    self._run_async(
                        self._view_model.query_stored_observations(
                            broker_id,
                            topic_filter,
                            after,
                            before,
                            order,
                            limit,
                        )
                    )
                )
            )
            dialog.refresh_requested.connect(
                lambda broker_id: self._run_async(
                    self._view_model.load_stored_observations(broker_id)
                )
            )
            dialog.message_requested.connect(
                lambda message_id: self._run_async(
                    self._view_model.inspect_stored_observation(message_id)
                )
            )
            self._stored_observations_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._run_async(self._view_model.load_stored_observations())

    async def _preview_and_save_retention_policy(self, policy) -> None:
        preview = await self._view_model.preview_retention_policy(policy)
        if preview.has_deletions:
            reasons = "\n".join(
                f"- {group.reason.value.replace('_', ' ').title()}: "
                f"{len(group.entries)} observations, "
                f"{group.stored_payload_bytes} stored bytes from "
                f"{self._view_model.broker_name(group.broker_id)}"
                for group in preview.groups
            )
            confirmed = self._confirm_destructive(
                "Apply retention policy?",
                f"Enforcement will permanently remove "
                f"{preview.deletion.total_entries} observations "
                f"({preview.deletion.stored_payload_bytes} stored bytes).\n\n"
                f"{reasons}\n\nPer-topic payload limits affect new observations "
                "and do not retroactively truncate existing rows.",
                f"Delete {preview.deletion.total_entries} observations & save",
            )
            if not confirmed:
                return
        try:
            await self._view_model.confirm_retention_policy(preview)
        except ValueError as error:
            if "preview" not in str(error).lower():
                raise
            self._view_model.log_message.emit(
                "Retention policy preview invalidated by concurrent changes."
            )
            QMessageBox.warning(
                self,
                "Retention preview changed",
                "Persisted observations changed after the preview. "
                "TopicGate will build a fresh preview for confirmation.",
            )
            await self._preview_and_save_retention_policy(policy)

    async def _preview_and_delete_cache(
        self,
        scope: str,
        broker_id: UUID,
        topics: tuple[str, ...],
    ) -> None:
        preview = await self._view_model.preview_cache_deletion(
            scope,
            broker_id=broker_id,
            topics=topics,
        )
        if not preview.entries:
            QMessageBox.information(
                self,
                "Stored observations",
                "There is nothing to delete for this scope.",
            )
            return
        broker_names = ", ".join(
            self._view_model.broker_name(item)
            for item in preview.broker_ids
        )
        confirmed = self._confirm_destructive(
            "Delete stored observations?",
            f"Scope: {scope.replace('_', ' ').title()}\n"
            f"Brokers: {broker_names}\n"
            f"Entries: {preview.total_entries}\n"
            f"Stored bytes: {preview.stored_payload_bytes}\n"
            f"Oldest: {preview.oldest_received_at}\n"
            f"Newest: {preview.newest_received_at}\n\n"
            "Deletion is permanent. Observations updated after this preview "
            "will be skipped.",
            f"Delete {preview.total_entries} observations",
        )
        if confirmed:
            result = await self._view_model.confirm_cache_deletion(preview)
            if result.is_partial:
                QMessageBox.warning(
                    self,
                    "Partial deletion",
                    f"Deleted {result.deleted_count} of "
                    f"{result.previewed_count} observations; "
                    f"{result.skipped_count} changed after preview.",
                )

    def _confirm_destructive(
        self,
        title: str,
        message: str,
        button_text: str,
    ) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        delete_button = dialog.addButton(
            button_text,
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel_button = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        return dialog.clickedButton() is delete_button

    def _apply_snapshot_query(self, query: object) -> None:
        try:
            self._view_model.apply_snapshot_query(query)
            self._save_snapshot_preferences()
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "Invalid snapshot controls", str(error))

    def _reset_snapshot_query(self) -> None:
        self._view_model.reset_snapshot_query()
        self._save_snapshot_preferences()

    def _confirm_reconnect_and_observe(self, query: object = None) -> None:
        try:
            selected_query = (
                query
                if query is not None
                else self._snapshot_panel.query
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid snapshot controls",
                str(error),
            )
            return
        broker = self._view_model.active_broker_profile
        result = QMessageBox.question(
            self,
            "Reconnect and observe?",
            f"Reconnect to '{broker.name}' and capture a new snapshot?\n\n"
            "This interrupts the active MQTT connection, renews it using "
            "the selected broker profile, waits for fresh observations, "
            "and then captures snapshot state.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self._run_async(
                self._view_model.reconnect_and_observe(selected_query)
            )

    def _show_operation_error(self, title: str, message: str) -> None:
        dialog = QMessageBox(self)
        is_lifecycle_error = any(
            term in message.lower()
            for term in ("broker", "connection", "mqtt")
        )
        dialog.setWindowTitle(
            "Broker action needs attention" if is_lifecycle_error else title
        )
        dialog.setText(message)
        if not is_lifecycle_error:
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.exec()
            return
        dialog.setInformativeText(
            "Check the broker profile or retry when no other broker action is running."
        )
        retry = dialog.addButton(
            "Retry connection", QMessageBox.ButtonRole.ActionRole
        )
        edit = dialog.addButton(
            "Edit broker profile...", QMessageBox.ButtonRole.ActionRole
        )
        close = dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.setDefaultButton(close)
        dialog.exec()
        if dialog.clickedButton() is retry:
            self._run_async(self._view_model.connect_to_broker())
        elif dialog.clickedButton() is edit:
            self._show_broker_settings_dialog()

    def _apply_subscription(
        self,
        original_filter: str,
        subscription: Subscription,
    ) -> None:
        self._run_async(
            self._view_model.update_subscription(
                original_filter,
                subscription,
            )
        )

    def _show_add_filter_dialog(self) -> None:
        subscription = AddSubscriptionDialog(self).subscription()
        if subscription is not None:
            self._run_async(self._view_model.add_subscription(subscription))

    def _show_broker_settings_dialog(self, profile_id: object = None) -> None:
        selected_profile_id = profile_id if isinstance(profile_id, UUID) else None
        dialog = BrokerSettingsDialog(
            self._view_model,
            self,
            profile_id=selected_profile_id,
        )
        dialog.save_requested.connect(
            lambda: self._save_broker_settings(dialog)
        )
        dialog.apply_requested.connect(
            lambda: self._apply_broker_settings(dialog)
        )
        dialog.open()

    def _show_create_broker_profile_dialog(self) -> None:
        dialog = BrokerSettingsDialog(self._view_model, self, creating=True)
        dialog.apply_requested.connect(
            lambda: self._apply_create_broker_profile(dialog)
        )
        dialog.open()

    def _apply_create_broker_profile(
        self,
        dialog: BrokerSettingsDialog,
    ) -> None:
        try:
            self._view_model.create_broker_profile(
                dialog.profile_name,
                dialog.mqtt_config,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Profile creation failed", str(error))
            return
        self._settings.setValue("onboarding/brokerConfigured", True)
        dialog.accept()

    def _confirm_broker_profile_switch(self, profile_id: UUID) -> None:
        current_profile = self._view_model.active_broker_profile
        if profile_id == current_profile.id:
            return
        next_profile = next(
            profile
            for profile in self._view_model.broker_profiles
            if profile.id == profile_id
        )
        result = QMessageBox.question(
            self,
            "Switch broker profile?",
            f"Do you want to switch from '{current_profile.name}' to "
            f"'{next_profile.name}'?\n\n"
            "This requires shutting down the current MQTT connection before "
            "connecting to the selected broker profile.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self._render_connection_controls(True)
            self._observer_tree.set_connection_busy(True)
            self._run_async(
                self._switch_broker_profile(profile_id, next_profile.config)
            )
        else:
            self._render_broker_connection()

    async def _switch_broker_profile(
        self,
        profile_id: UUID,
        mqtt_config: MqttConfig,
    ) -> None:
        previous_profile_id = self._view_model.active_broker_profile.id
        try:
            await self._view_model.activate_broker_profile(profile_id, mqtt_config)
        finally:
            if self._view_model.active_broker_profile.id != previous_profile_id:
                self._show_snapshot()
            self._render_connection_controls()
            self._observer_tree.set_connection_busy(False)

    def _confirm_delete_broker_profile(
        self,
        profile_id: object = None,
    ) -> None:
        profiles = self._view_model.broker_profiles
        selected_profile_id = (
            profile_id
            if isinstance(profile_id, UUID)
            else self._view_model.active_broker_profile.id
        )
        profile = next(
            item for item in profiles if item.id == selected_profile_id
        )
        if len(profiles) == 1:
            return
        deleting_active = profile.id == self._view_model.active_broker_profile.id
        consequence = (
            "\n\nThe application will connect to another broker profile first."
            if deleting_active
            else ""
        )
        result = QMessageBox.question(
            self,
            "Delete broker profile?",
            f"Delete '{profile.name}' and its observer workspace?{consequence}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self._render_connection_controls(True)
            self._observer_tree.set_connection_busy(True)
            self._run_async(self._delete_broker_profile(profile.id))

    async def _delete_broker_profile(self, profile_id: UUID) -> None:
        try:
            await self._view_model.delete_broker_profile(profile_id)
        finally:
            self._render_connection_controls()
            self._observer_tree.set_connection_busy(False)

    def _apply_broker_settings(self, dialog: BrokerSettingsDialog) -> None:
        try:
            profile_name = dialog.profile_name
            mqtt_config = dialog.mqtt_config
        except ValueError:
            return
        profile_id = dialog.profile_id
        if profile_id is None:
            return
        dialog.set_applying(True)
        self._run_async(
            self._apply_broker_settings_async(
                dialog,
                profile_id,
                profile_name,
                mqtt_config,
            )
        )

    def _save_broker_settings(self, dialog: BrokerSettingsDialog) -> None:
        try:
            profile_name = dialog.profile_name
            mqtt_config = dialog.mqtt_config
            profile_id = dialog.profile_id
            if profile_id is None:
                return
            self._view_model.save_broker_profile(
                profile_id,
                mqtt_config,
                profile_name,
            )
            self._settings.setValue("onboarding/brokerConfigured", True)
        except ValueError as error:
            QMessageBox.warning(self, "Broker update failed", str(error))
            return
        dialog.accept()

    async def _apply_broker_settings_async(
        self,
        dialog: BrokerSettingsDialog,
        profile_id: UUID,
        profile_name: str,
        mqtt_config: MqttConfig,
    ) -> None:
        try:
            await self._view_model.activate_broker_profile(
                profile_id,
                mqtt_config,
                profile_name,
            )
        except Exception as error:
            dialog.set_applying(False)
            if self._view_model.active_broker_profile.id == profile_id:
                self._settings.setValue("onboarding/brokerConfigured", True)
                QMessageBox.warning(
                    self,
                    "Broker selected but offline",
                    "The broker profile was saved and selected, but TopicGate "
                    f"could not connect: {error}",
                )
            else:
                QMessageBox.warning(self, "Broker update failed", str(error))
            return
        self._settings.setValue("onboarding/brokerConfigured", True)
        self._settings.setValue("onboarding/connectionTested", True)
        dialog.accept()

    def _remove_subscription(self, subscription: Subscription) -> None:
        self._run_async(self._view_model.remove_subscription(subscription))

    def _run_async(self, operation: Coroutine[Any, Any, None]) -> None:
        if not self._accepting_operations:
            operation.close()
            return
        task = asyncio.create_task(operation)
        self._operation_tasks.add(task)

        def report_error(completed: asyncio.Task[None]) -> None:
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                self._view_model.report_operation_error(
                    "Operation failed",
                    error,
                )

        task.add_done_callback(report_error)
        task.add_done_callback(self._operation_tasks.discard)

    async def cancel_pending_operations(self) -> None:
        """Cancel window-owned commands before runtime services stop."""
        self._accepting_operations = False
        tasks = tuple(self._operation_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._operation_tasks.clear()

    def _restore_state(self) -> None:
        splitter_state = self._settings.value("workspace/splitter")
        if isinstance(splitter_state, QByteArray):
            self._splitter.restoreState(splitter_state)
        selected_topic = str(
            self._settings.value(
                "workspace/selectedTopic",
                self._view_model.topic,
            )
            or ""
        )
        if selected_topic:
            self._view_model.select_topic(selected_topic)
        self._restore_snapshot_preferences()
        if self._view_model.topic:
            self._show_topic_details()
        else:
            self._show_snapshot()
        log_visible = self._settings.value(
            "workspace/logVisible",
            False,
            type=bool,
        )
        self._log_dock.setVisible(log_visible)

    def _restore_snapshot_preferences(self) -> None:
        topic_filter = str(
            self._settings.value("workspace/snapshotTopicFilter", "#") or "#"
        )
        age_value = self._settings.value("workspace/snapshotMaximumAge", "")
        try:
            age = None if age_value in (None, "") else float(age_value)
            query = SnapshotQuery(
                topic_filter=topic_filter,
                max_age_seconds=age,
                result_limit=self._settings.value(
                    "workspace/snapshotResultLimit",
                    SnapshotQuery().result_limit,
                    type=int,
                ),
                payload_limit_bytes=self._settings.value(
                    "workspace/snapshotPayloadLimit",
                    SnapshotQuery().payload_limit_bytes,
                    type=int,
                ),
            )
            self._view_model.apply_snapshot_query(query)
        except (TypeError, ValueError):
            self._view_model.reset_snapshot_query()
        self._snapshot_panel.set_advanced_visible(
            self._settings.value("workspace/snapshotExpanded", False, type=bool)
        )

    def _save_snapshot_preferences(self) -> None:
        query = self._view_model.snapshot_query
        self._settings.setValue(
            "workspace/snapshotTopicFilter", query.topic_filter
        )
        self._settings.setValue(
            "workspace/snapshotMaximumAge",
            "" if query.max_age_seconds is None else query.max_age_seconds,
        )
        self._settings.setValue(
            "workspace/snapshotResultLimit", query.result_limit
        )
        self._settings.setValue(
            "workspace/snapshotPayloadLimit", query.payload_limit_bytes
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self._accepting_operations = False
        self._settings.setValue(
            "workspace/splitter",
            self._splitter.saveState(),
        )
        self._settings.setValue(
            "workspace/selectedTopic",
            self._view_model.topic,
        )
        self._settings.setValue(
            "workspace/logVisible",
            self._log_dock.isVisible(),
        )
        self._settings.setValue(
            "workspace/snapshotExpanded",
            self._snapshot_panel.is_advanced_visible,
        )
        self._save_snapshot_preferences()
        self._settings.sync()
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._topic_details.focus_payload()
