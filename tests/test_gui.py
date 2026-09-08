import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QSettings, Qt
from PySide6.QtGui import QAction, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QSplitter,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QToolBar,
    QToolButton,
    QTreeView,
    QWidget,
    QWidgetAction,
)

from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.interfaces import PackReference
from topicgate.app.models.expectation_health_report import (
    FailureHistoryItem,
    FailureHistoryResult,
)
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.models.mqtt_message import MqttMessage
from topicgate.core.models.current_topic import CurrentTopic
from topicgate.core.models.observation_status import ObservationStatus
from topicgate.core.models.topic_message import TopicMessage
from topicgate.core.models.broker_profile import BrokerProfile
from topicgate.core.models.diagnostic_profile import DiagnosticProfile
from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.core.models.health.condition import InRangeCondition
from topicgate.core.models.health.condition import FreshnessCondition
from topicgate.core.models.health.condition import NumericRangeCondition
from topicgate.core.models.health.condition import TopicExistsCondition
from topicgate.core.models.health.condition_kind import ConditionKind
from topicgate.core.models.health import (
    BrokerTarget,
    EqualCondition,
    HealthExpectation,
    HealthSeverity,
    HealthStatus,
    ObservationFindingCode,
    ObservationHealthFinding,
)
from topicgate.core.models.mqtt_observation import MqttObservation as TopicState
from topicgate.core.models.observer_workspace import ObserverWorkspace
from topicgate.core.models.subscription import Subscription
from topicgate.core.models.observation_retention_policy import (
    ObservationRetentionPolicy,
)
from topicgate.core.models.observation_cache_administration import (
    BrokerCacheUsage,
    CacheUsageSummary,
    ObservationDeletionResult,
)
from topicgate.core.models.observation_deletion_preview import (
    ObservationDeletionEntry,
    ObservationDeletionPreview,
)
from topicgate.gui.components.about_dialog import AboutDialog
from topicgate.gui.components.broker_connection import BrokerConnectionPane
from topicgate.gui.components.connection_controls import ConnectionControls
from topicgate.gui.components.broker_settings_dialog import (
    BrokerSettingsDialog,
)
from topicgate.gui.components.observer_tree import ObserverTreePane
from topicgate.gui.components.publish_pane import PublishPane
from topicgate.gui.components.snapshot_panel import SnapshotPanel
from topicgate.gui.components.stored_observations_dialog import (
    StoredObservationsDialog,
)
from topicgate.gui.components.health_inspector import HealthInspector
from topicgate.gui.components.expectation_editor import ExpectationEditor
from topicgate.gui.components.diagnostic_profile_editor import (
    DiagnosticProfileEditorWindow,
)
from topicgate.gui.components.topic_details import TopicDetailsPane
from topicgate.gui.components.workspace_pane import (
    WORKSPACE_CONTROL_HEIGHT,
    WorkspacePane,
)
from topicgate.gui.gui import MainWindow
from topicgate.gui.main_view_model import MainViewModel
from topicgate.gui.theme import LIGHT_THEME, apply_light_theme
from topicgate.presentation.snapshot_presentation import (
    BrokerSnapshotHealth,
    SnapshotQuery,
)
from topicgate.presentation.topic_presentation import build_topic_tree


def test_diagnostic_profile_rules_show_only_the_selected_draft() -> None:
    application = QApplication.instance() or QApplication([])
    broker_id = uuid4()
    other_profile = DiagnosticProfile(uuid4(), broker_id, "Other")
    draft = DiagnosticProfile(uuid4(), broker_id, "Draft")

    def rules_for(profile: DiagnosticProfile) -> tuple[HealthExpectation, ...]:
        if profile.pack_reference is None:
            return ()
        return tuple(
            HealthExpectation(
                uuid4(),
                1,
                True,
                HealthSeverity.WARNING,
                BrokerTarget(broker_id),
                EqualCondition(b"online"),
                frozenset(),
                name=f"Rule {index}",
                profile_id=profile.profile_id,
                rule_id=f"rule-{index}",
                source_kind="pack",
            )
            for index in range(7)
        )

    class FakeDiagnosticProfileEditor:
        def __init__(self) -> None:
            self.draft = None
            self.validation = SimpleNamespace(errors=(), is_valid=True)
            self.is_dirty = False
            self.profiles = (draft, other_profile)
            self.pack_references = (PackReference("zigbee2mqtt", "1.0.0"),)

        def open_broker(self, requested_broker_id) -> None:
            assert requested_broker_id == broker_id
            self.draft = draft

        def validate(self):
            return SimpleNamespace(
                expectations=(*rules_for(other_profile), *rules_for(self.draft))
            )

        def set_pack(self, reference) -> None:
            self.draft = replace(self.draft, pack_reference=reference)
            self.is_dirty = True

        def replace_draft(self, replacement) -> None:
            self.draft = replacement

    editor = FakeDiagnosticProfileEditor()
    other_profile = replace(other_profile, pack_reference=editor.pack_references[0])
    dialog = DiagnosticProfileEditorWindow(editor, broker_id)
    table = dialog.findChild(QTableWidget, "diagnosticProfileRules")
    pack = dialog.findChild(QComboBox, "diagnosticPackSelector")

    assert table is not None
    assert pack is not None
    assert table.rowCount() == 0

    pack.setCurrentIndex(1)
    application.processEvents()

    assert table.rowCount() == 7
    assert {table.item(row, 0).text() for row in range(table.rowCount())} == {
        f"rule-{index}" for index in range(7)
    }
    editor.is_dirty = False
    dialog.close()
    application.processEvents()


def test_health_action_opens_broker_scoped_inspector() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    window = MainWindow(
        MainViewModel(runtime_for(repository), repository.state.topic)
    )

    action = window.findChild(QAction, "healthAction")
    assert action is not None
    assert action.shortcut().toString() == "Ctrl+Shift+H"

    action.trigger()
    application.processEvents()

    inspector = window.findChild(HealthInspector, "healthInspector")
    stack = window.findChild(QStackedWidget, "inspectorStack")
    assert inspector is not None
    assert stack.currentWidget() is inspector
    assert inspector.findChild(QTableWidget, "currentBrokerHealthTable") is not None
    assert inspector.findChild(QTableWidget, "currentTopicHealthTable") is not None
    assert inspector.findChild(QTableWidget, "findingDeltaTable") is not None
    assert inspector.findChild(QWidget, "brokerExpectationEditor") is not None
    assert inspector.findChild(QTableWidget, "healthHistoryTable") is not None
    window.close()
    application.processEvents()


def test_settings_and_health_tabs_reuse_visible_topic_tab_style() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    window = MainWindow(
        MainViewModel(runtime_for(repository), repository.state.topic)
    )
    settings_tabs = window.findChild(QTabWidget, "topicSettingsTabs")
    assert settings_tabs is not None
    assert settings_tabs.tabBar().objectName() == "topicSettingsTabs"
    assert settings_tabs.tabBar().expanding()
    window.findChild(QToolButton, "topicEditButton").click()
    window.show()
    application.processEvents()
    assert settings_tabs.tabBar().width() == settings_tabs.contentsRect().width()

    action = window.findChild(QAction, "healthAction")
    assert action is not None
    action.trigger()
    application.processEvents()
    inspector = window.findChild(HealthInspector, "healthInspector")
    assert inspector is not None
    health_tabs = inspector.findChild(QTabWidget, "healthTabs")
    assert health_tabs is not None
    assert health_tabs.tabBar().objectName() == "healthTabs"
    assert "QTabBar#topicSettingsTabs::tab:selected" in LIGHT_THEME
    assert "QTabBar#healthTabs::tab:selected" in LIGHT_THEME
    window.close()
    application.processEvents()


def test_topic_expectation_editor_creates_utf8_rule() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    management = MagicMock()
    management.list_expectations.return_value = ()
    management.create_expectation.side_effect = lambda item, **_kwargs: item
    view_model = MainViewModel(
        runtime_for(repository),
        repository.state.topic,
        expectation_management_service=management,
    )
    window = MainWindow(view_model)
    editor = window.findChild(QWidget, "topicExpectationEditor")

    editor.findChild(QLineEdit, "expectationName").setText("Temperature")
    expected = editor.findChild(QComboBox, "expectationExpectedValue")
    expected.setEditText("21.5")
    editor.findChild(QPushButton, "saveExpectationButton").click()

    created = management.create_expectation.call_args.args[0]
    assert created.name == "Temperature"
    assert created.condition.expected_value == b"21.5"
    window.close()
    application.processEvents()


def test_topic_expectation_editor_selects_multi_value_condition() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    management = MagicMock()
    management.list_expectations.return_value = ()
    management.create_expectation.side_effect = lambda item, **_kwargs: item
    view_model = MainViewModel(
        runtime_for(repository),
        repository.state.topic,
        expectation_management_service=management,
    )
    window = MainWindow(view_model)
    editor = window.findChild(QWidget, "topicExpectationEditor")

    editor.findChild(QLineEdit, "expectationName").setText("Device state")
    condition_kind = editor.findChild(QComboBox, "expectationConditionKind")
    condition_kind.setCurrentIndex(
        condition_kind.findData(ConditionKind.IN_RANGE)
    )
    expected = editor.findChild(QPlainTextEdit, "expectationExpectedValues")
    expected.setPlainText("online,degraded")

    expected_editor = editor.findChild(QStackedWidget, "expectationExpectedEditor")
    assert expected_editor.currentWidget() is expected

    editor.findChild(QPushButton, "saveExpectationButton").click()

    created = management.create_expectation.call_args.args[0]
    assert created.condition == InRangeCondition((b"online", b"degraded"))
    assert ConditionKind(condition_kind.currentData()) is ConditionKind.IN_RANGE
    window.close()
    application.processEvents()


def test_snapshot_panel_applies_clears_and_renders_health() -> None:
    application = QApplication.instance() or QApplication([])
    pane = SnapshotPanel()
    requested: list[SnapshotQuery] = []
    observations: list[SnapshotQuery] = []
    resets: list[bool] = []
    pane.apply_requested.connect(requested.append)
    pane.reconnect_observe_requested.connect(observations.append)
    pane.reset_requested.connect(lambda: resets.append(True))
    advanced = pane.findChild(QToolButton, "snapshotAdvancedButton")
    advanced_content = pane.findChild(QWidget, "snapshotAdvancedContent")
    controls = pane.findChild(QWidget, "snapshotControls")
    assert isinstance(pane, WorkspacePane)
    assert pane.header_layout.indexOf(pane.heading) == 0
    assert pane.findChild(QToolButton, "snapshotToggleButton") is None
    assert pane.findChild(QWidget, "snapshotContent") is None
    assert not pane.is_advanced_visible
    assert not advanced.isChecked()
    assert not controls.isHidden()
    assert advanced_content.isHidden()
    assert pane.findChild(QLabel, "snapshotSummaryConnection").text() == (
        "Disconnected"
    )
    assert pane.findChild(
        QPushButton,
        "reconnectObserveButton",
    ).accessibleName() == "Reconnect & observe"
    assert pane.heading.text() == "Broker snapshot"
    assert advanced.text() == "Advanced"
    assert pane.header_layout.indexOf(advanced) >= 0
    assert pane.findChild(
        QPushButton,
        "reconnectObserveButton",
    ).property("primary")
    assert not advanced_content.isAncestorOf(
        pane.findChild(QLabel, "snapshotSummaryConnection")
    )

    advanced.setFocus()
    QTest.keyClick(advanced, Qt.Key.Key_Space)
    assert pane.is_advanced_visible
    assert not advanced_content.isHidden()
    assert advanced.text() == "Hide advanced"
    pane.findChild(QLineEdit, "snapshotTopicFilter").setText("home/#")
    pane.findChild(QLineEdit, "snapshotMaximumAge").setText("10.5")
    pane.findChild(QSpinBox, "snapshotResultLimit").setValue(12)
    pane.findChild(QSpinBox, "snapshotPayloadLimit").setValue(512)

    pane.findChild(QPushButton, "applySnapshotButton").click()
    assert requested[-1] == SnapshotQuery("home/#", 10.5, 12, 512)
    pane.findChild(QPushButton, "reconnectObserveButton").click()
    assert observations[-1] == SnapshotQuery("home/#", 10.5, 12, 512)

    pane.set_busy(True)
    assert not pane.findChild(QPushButton, "applySnapshotButton").isEnabled()
    assert not pane.findChild(
        QPushButton,
        "reconnectObserveButton",
    ).isEnabled()
    pane.set_busy(False)
    assert pane.findChild(QPushButton, "applySnapshotButton").isEnabled()

    health = BrokerSnapshotHealth(
        "captured",
        "connected",
        "observing",
        "4.0 seconds",
        3,
        2,
        1,
        1,
        5,
        "Limited",
        ("Current state only.",),
    )
    pane.render_health(health)
    assert pane.findChild(QLabel, "snapshotReturnedCount").text() == "3"
    assert pane.findChild(QLabel, "snapshotCompletenessStatus").text() == "Limited"
    assert pane.findChild(QLabel, "snapshotSummaryReturned").text() == (
        "Returned 3"
    )
    assert pane.findChild(QLabel, "snapshotSummaryDropped").text() == (
        "Dropped 5"
    )
    assert pane.findChild(QLabel, "snapshotSummaryCompleteness").text() == (
        "Limited"
    )

    pane.set_advanced_visible(False)
    assert pane.query == SnapshotQuery("home/#", 10.5, 12, 512)
    assert advanced_content.isHidden()
    assert not controls.isHidden()

    pane.findChild(QPushButton, "clearSnapshotFiltersButton").click()
    assert resets == [True]
    assert pane.query == SnapshotQuery()
    pane.deleteLater()
    application.processEvents()


def test_snapshot_panel_reports_field_specific_age_errors() -> None:
    application = QApplication.instance() or QApplication([])
    pane = SnapshotPanel()
    requested: list[SnapshotQuery] = []
    errors: list[str] = []
    pane.apply_requested.connect(requested.append)
    pane.validation_failed.connect(errors.append)
    pane.findChild(QLineEdit, "snapshotMaximumAge").setText("invalid")

    pane.findChild(QPushButton, "applySnapshotButton").click()

    assert requested == []
    assert errors == [
        "Maximum age must be a non-negative number or blank."
    ]
    pane.deleteLater()
    application.processEvents()


def test_snapshot_panel_exposes_freshness_legend_and_accessible_filters() -> None:
    application = QApplication.instance() or QApplication([])
    pane = SnapshotPanel()

    legend = pane.findChild(QLabel, "snapshotFreshnessLegend")
    topic_filter = pane.findChild(QLineEdit, "snapshotTopicFilter")
    apply = pane.findChild(QPushButton, "applySnapshotButton")

    assert legend is not None
    assert "Live" in legend.text()
    assert "Cached" in legend.text()
    assert legend.accessibleName() == "Freshness and source legend"
    assert topic_filter.accessibleName() == "Snapshot topic filter"
    assert apply.accessibleName() == "Apply snapshot filters"
    pane.deleteLater()
    application.processEvents()


def test_about_dialog_describes_persisted_observations() -> None:
    application = QApplication.instance() or QApplication([])
    dialog = AboutDialog()

    text = dialog.findChild(QLabel, "aboutStorageText").text()

    assert text == (
        "Broker profiles, subscriptions, and each broker's latest observed "
        "MQTT values are stored in SQLite. Passwords are stored in your "
        "operating system's credential store. Snapshot views may include "
        "stored observations captured before the current connection or "
        "observation window."
    )
    dialog.deleteLater()
    application.processEvents()


def test_stored_observations_dialog_renders_policy_and_inline_validation() -> None:
    application = QApplication.instance() or QApplication([])
    view_model = MainViewModel(runtime_for(FakeGuiRepository()))
    view_model._retention_policy = ObservationRetentionPolicy()
    dialog = StoredObservationsDialog(view_model)

    dialog.render()

    assert dialog.findChild(QComboBox, "retentionPreset").currentText() == "Balanced"
    save = dialog.findChild(QPushButton, "saveRetentionPolicyButton")
    assert save.isEnabled()
    assert dialog.findChild(QLineEdit, "maxEntriesPerBroker").accessibleName() == (
        "Maximum entries per broker"
    )
    assert dialog.findChild(QTableWidget, "cacheUsageTable").accessibleName() == (
        "Cache usage by broker"
    )
    dialog.findChild(QLineEdit, "maxEntriesPerBroker").setText("20000")
    assert dialog.findChild(QComboBox, "retentionPreset").currentText() == "Custom"
    assert not save.isEnabled()
    assert "total" in dialog.findChild(
        QLabel,
        "max_entries_per_brokerError",
    ).text()

    dialog.findChild(QComboBox, "retentionPreset").setCurrentText(
        "Conservative"
    )
    assert dialog.draft_policy() == view_model.retention_presets[0].policy
    dialog.deleteLater()
    application.processEvents()


async def test_cache_deletion_cancellation_preserves_inactive_broker_state() -> None:
    application = QApplication.instance() or QApplication([])
    view_model = MainViewModel(runtime_for(FakeGuiRepository()))
    inactive = view_model.broker_profiles[1]
    entry = ObservationDeletionEntry(
        inactive.id,
        "stored/topic",
        uuid4(),
        datetime.now(timezone.utc),
        12,
    )
    preview = ObservationDeletionPreview(inactive.id, (entry,), "broker")
    view_model.preview_cache_deletion = AsyncMock(return_value=preview)
    view_model.confirm_cache_deletion = AsyncMock()
    window = MainWindow(view_model)

    with patch.object(window, "_confirm_destructive", return_value=False):
        await window._preview_and_delete_cache("broker", inactive.id, ())

    view_model.confirm_cache_deletion.assert_not_awaited()
    window.close()
    application.processEvents()


async def test_cache_deletion_confirmation_reports_partial_inactive_result() -> None:
    application = QApplication.instance() or QApplication([])
    view_model = MainViewModel(runtime_for(FakeGuiRepository()))
    inactive = view_model.broker_profiles[1]
    first = ObservationDeletionEntry(
        inactive.id,
        "stored/first",
        uuid4(),
        datetime.now(timezone.utc),
        12,
    )
    changed = ObservationDeletionEntry(
        inactive.id,
        "stored/changed",
        uuid4(),
        datetime.now(timezone.utc),
        8,
    )
    preview = ObservationDeletionPreview(inactive.id, (first, changed), "broker")
    result = ObservationDeletionResult((first, changed), (first,), (changed,))
    view_model.preview_cache_deletion = AsyncMock(return_value=preview)
    view_model.confirm_cache_deletion = AsyncMock(return_value=result)
    window = MainWindow(view_model)

    with (
        patch.object(window, "_confirm_destructive", return_value=True),
        patch("topicgate.gui.main_window.QMessageBox.warning") as warning,
    ):
        await window._preview_and_delete_cache("broker", inactive.id, ())

    view_model.confirm_cache_deletion.assert_awaited_once_with(preview)
    assert "Deleted 1 of 2" in warning.call_args.args[2]
    window.close()
    application.processEvents()


def test_window_keeps_broker_switching_above_observer_tree() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    view_model = MainViewModel(runtime_for(repository), repository.state.topic)
    settings = QSettings(
        str(Path(".pytest_cache/redesigned-window.ini").resolve()),
        QSettings.Format.IniFormat,
    )
    settings.clear()
    window = MainWindow(view_model, settings)

    assert window.minimumWidth() == 1024
    assert window.minimumHeight() == 640
    assert window.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is None
    assert window.findChild(QTabBar, "workspaceNavigation") is None
    assert window.findChild(QWidget, "workspaceStack") is None
    inspector = window.findChild(QWidget, "topicInspector")
    observer_workspace = window.findChild(QWidget, "observerWorkspace")
    broker = window.findChild(QWidget, "brokerConnectionPane")
    selector = window.findChild(QComboBox, "connectionBrokerSelector")
    assert inspector is not None
    assert observer_workspace is not None
    assert broker is not None
    assert selector is not None
    assert observer_workspace.layout().indexOf(broker) == 0
    assert observer_workspace.layout().indexOf(window._observer_tree) == 1
    inspector_stack = window.findChild(QStackedWidget, "inspectorStack")
    assert inspector.layout().indexOf(inspector_stack) == 0
    assert inspector_stack.indexOf(window._topic_details) == 1
    assert inspector.layout().indexOf(broker) == -1
    assert selector.currentText() == "Default"
    assert [selector.itemText(index) for index in range(selector.count())] == [
        "Default",
        "Local MQTT",
    ]
    assert window.findChild(QLabel, "activeBrokerEndpoint") is None
    assert window.findChild(QPushButton, "editBrokerProfileButton") is None
    assert window.findChild(QComboBox, "brokerSelector") is None
    assert window.findChild(QWidget, "applicationHeader") is None
    assert window.findChild(QToolButton, "brokerProfileButton") is None
    topic_heading = window._topic_details.heading
    assert topic_heading.text() == repository.state.topic
    assert topic_heading.textFormat() == Qt.TextFormat.PlainText
    assert window.findChild(QPlainTextEdit, "publishPayload") is not None
    publish_payload = window.findChild(QPlainTextEdit, "publishPayload")
    assert not window.findChild(QPushButton, "publishButton").isEnabled()
    publish_payload.setPlainText("open")
    assert window.findChild(QPushButton, "publishButton").isEnabled()
    assert "#f3f4f6" in window.styleSheet()
    assert "border: 1px solid #c8ced6" in window.styleSheet()
    assert "QTreeView::item:selected" in window.styleSheet()
    assert "border-left-color: #405d7a" in window.styleSheet()
    assert "QSplitter::handle:horizontal" in window.styleSheet()
    assert "color: #737b85" in window.styleSheet()

    window.close()
    application.processEvents()


def test_settings_button_reveals_subscription_and_expectation_settings() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    view_model = MainViewModel(runtime_for(repository), repository.state.topic)
    settings = QSettings(
        str(Path(".pytest_cache/topic-edit-panel.ini").resolve()),
        QSettings.Format.IniFormat,
    )
    settings.clear()
    window = MainWindow(view_model, settings)
    context = window.findChild(QWidget, "contextPanel")
    edit_button = window.findChild(QToolButton, "topicEditButton")

    assert context is not None
    assert edit_button is not None
    assert context.isHidden()
    assert edit_button.text() == "Settings"
    assert not edit_button.icon().isNull()
    assert (
        edit_button.toolButtonStyle()
        == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    )

    edit_button.click()

    assert not context.isHidden()
    assert edit_button.text() == "Settings"
    assert context.findChild(QWidget, "topicExpectationEditor") is not None
    assert context.findChild(QWidget, "topicPublishPane") is None
    assert context.findChild(QPushButton, "revertSubscriptionButton") is None

    edit_button.click()

    assert context.isHidden()
    assert edit_button.text() == "Settings"
    window.close()
    application.processEvents()


def test_workspace_headers_and_primary_controls_share_rows() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    settings = QSettings(
        str(Path(".pytest_cache/workspace-alignment.ini").resolve()),
        QSettings.Format.IniFormat,
    )
    settings.clear()
    window = MainWindow(
        MainViewModel(runtime_for(repository), repository.state.topic),
        settings,
    )
    window.findChild(QToolButton, "topicEditButton").click()
    window.show()
    application.processEvents()

    headers = (
        window._broker_connection.header,
        window._topic_details.header,
        window._context_panel.header,
    )
    controls = (
        window.findChild(QComboBox, "connectionBrokerSelector"),
        window.findChild(QTabBar, "topicDetailsMode"),
        window.findChild(QToolButton, "topicEditButton"),
        window._settings_tabs.tabBar(),
    )

    assert len(
        {widget.mapToGlobal(widget.rect().topLeft()).y() for widget in headers}
    ) == 1
    assert len({widget.height() for widget in headers}) == 1
    assert len(
        {widget.mapToGlobal(widget.rect().topLeft()).y() for widget in controls}
    ) == 1
    assert len({widget.height() for widget in controls}) == 1
    assert all(widget.height() >= widget.sizeHint().height() for widget in controls)
    assert all(
        tab_bar.tabRect(index).height() == WORKSPACE_CONTROL_HEIGHT
        for tab_bar in (controls[1], controls[3])
        for index in range(tab_bar.count())
    )

    window.close()
    application.processEvents()


def test_desktop_onboarding_and_mcp_setup_guide_the_first_run() -> None:
    application = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(Path(".pytest_cache/first-run.ini").resolve()),
        QSettings.Format.IniFormat,
    )
    settings.clear()
    window = MainWindow(MainViewModel(runtime_for(FakeGuiRepository())), settings)

    checklist = window.findChild(QWidget, "firstRunChecklist")
    broker = window.findChild(QLabel, "firstRunBrokerStatus")
    mcp_action = window.findChild(QAction, "mcpSetupAction")
    assert checklist is not None
    assert broker.text() == "Next: Configure a broker profile"
    assert mcp_action.shortcut().toString() == "Ctrl+Shift+M"

    mcp_action.trigger()
    dialog = window.findChild(QDialog, "mcpSetupDialog")
    assert dialog is not None
    assert '"--mode", "read-only"' in dialog.findChild(
        QPlainTextEdit, "mcpSetupConfiguration"
    ).toPlainText()
    mode = dialog.findChild(QComboBox, "mcpConfigurationMode")
    mode.setCurrentIndex(1)
    assert '"--mode", "control"' in dialog.findChild(
        QPlainTextEdit, "mcpSetupConfiguration"
    ).toPlainText()
    assert "publishing" in dialog.findChild(QLabel, "mcpModeWarning").text()
    assert "untrusted" in dialog.findChild(
        QLabel, "mcpUntrustedDataWarning"
    ).text().lower()
    dialog.findChild(QPushButton, "runMcpPreflightButton").click()
    assert "WARNING" in dialog.findChild(QLabel, "mcpPreflightResults").text()
    dialog.findChild(QPushButton, "testMcpSnapshotButton").click()
    assert "PASS: Broker snapshot" in dialog.findChild(
        QLabel, "mcpPreflightResults"
    ).text()
    dialog.accept()
    assert settings.value("onboarding/mcpConfigured", False, type=bool)
    window.close()
    application.processEvents()


def test_desktop_persists_snapshot_preferences_and_focuses_search() -> None:
    application = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(Path(".pytest_cache/snapshot-preferences.ini").resolve()),
        QSettings.Format.IniFormat,
    )
    settings.clear()
    first = MainWindow(MainViewModel(runtime_for(FakeGuiRepository())), settings)
    first._apply_snapshot_query(SnapshotQuery("devices/#", 12.0, 9, 256))
    first._snapshot_panel.set_advanced_visible(True)
    first.close()

    second = MainWindow(MainViewModel(runtime_for(FakeGuiRepository())), settings)
    assert second._view_model.snapshot_query == SnapshotQuery("devices/#", 12.0, 9, 256)
    assert second._snapshot_panel.is_advanced_visible
    second.show()
    application.processEvents()
    second._focus_topic_search_action.trigger()
    assert second._observer_tree._search_edit.hasFocus()
    second.close()
    application.processEvents()


def test_health_history_deletes_selected_episode_after_confirmation() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    runtime = runtime_for(repository)
    failure_id = uuid4()
    history_item = FailureHistoryItem(
        failure_id=failure_id,
        expectation_id=uuid4(),
        broker_id=runtime.active_broker.id,
        target="devices/status",
        first_failed_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        recovered_at=datetime.now(timezone.utc),
        occurrence_count=1,
        expected_revision=1,
        last_healthy_at=None,
        failure_code="MISMATCH",
        evidence_summary="unexpected value",
        evidence_truncated=False,
        evidence_limitations=(),
    )
    health_query = MagicMock()
    health_query.query_failure_history.return_value = FailureHistoryResult(
        (history_item,), None, 1
    )
    view_model = MainViewModel(runtime, health_query_service=health_query)
    inspector = HealthInspector(view_model)
    delete_button = inspector.findChild(
        QPushButton, "deleteHealthHistoryButton"
    )
    assert delete_button.property("danger") is True
    assert not delete_button.isEnabled()
    inspector.show_history()
    table = inspector.findChild(QTableWidget, "healthHistoryTable")
    table.selectRow(0)
    assert delete_button.isEnabled()

    with patch(
        "topicgate.gui.components.health_inspector.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        delete_button.click()

    health_query.delete_failure_history.assert_called_once_with(failure_id)
    assert table.rowCount() == 0
    inspector.deleteLater()
    application.processEvents()


@pytest.mark.parametrize(
    ("source_kind", "button_text", "service_method"),
    (
        ("custom", "Remove", "delete_expectation"),
        ("pack", "Disable", "disable_expectation"),
    ),
)
def test_health_overview_removes_selected_expectation_after_confirmation(
    source_kind: str,
    button_text: str,
    service_method: str,
) -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    runtime = runtime_for(repository)
    expectation_id = uuid4()
    finding = SimpleNamespace(
        expectation_id=expectation_id,
        name="Connected",
        target_kind="broker",
        target="broker",
        status=HealthStatus.PROBLEM,
        evidence_summary="actual=disconnected; expected=connected",
        evidence_truncated=False,
    )
    report = SimpleNamespace(
        broker_id=runtime.active_broker.id,
        evaluated_at=datetime.now(timezone.utc),
        aggregate_status=HealthStatus.PROBLEM,
        evidence_complete=True,
        observation_status=HealthStatus.HEALTHY,
        observation_findings=(),
        expectation_findings=(finding,),
        active_failure_count=1,
        returned_count=1,
        omitted_count=0,
        checkpoint=None,
        delta=None,
    )
    health_query = MagicMock()
    health_query.get_health_report.return_value = report
    management = MagicMock()
    management.list_expectations.return_value = (
        HealthExpectation(
            expectation_id,
            1,
            True,
            HealthSeverity.CRITICAL,
            BrokerTarget(runtime.active_broker.id),
            EqualCondition("connected"),
            frozenset(),
            "Connected",
            source_kind=source_kind,
        ),
    )
    view_model = MainViewModel(
        runtime,
        health_query_service=health_query,
        expectation_management_service=management,
    )
    inspector = HealthInspector(view_model)
    remove_button = inspector.findChild(
        QPushButton, "removeHealthExpectationButton"
    )
    assert remove_button.property("danger") is True
    assert not remove_button.isEnabled()

    view_model.refresh_health()
    inspector.findChild(QTableWidget, "currentBrokerHealthTable").selectRow(0)
    assert remove_button.isEnabled()
    assert remove_button.text() == button_text

    with patch(
        "topicgate.gui.components.health_inspector.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        remove_button.click()

    getattr(management, service_method).assert_called_once_with(
        expectation_id,
        broker_id=runtime.active_broker.id,
    )
    assert not remove_button.isEnabled()
    inspector.deleteLater()
    application.processEvents()


def test_health_navigation_preserves_topic_edits_and_same_topic_returns() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    window = MainWindow(
        MainViewModel(runtime_for(repository), repository.state.topic)
    )
    stack = window.findChild(QStackedWidget, "inspectorStack")
    editor = window.findChild(QLineEdit, "expectationName")
    editor.setText("Unfinished rule")

    window.findChild(QPushButton, "brokerHealthSummary").click()

    assert stack.currentWidget() is window._health_inspector
    assert window._view_model.topic == repository.state.topic
    assert editor.text() == "Unfinished rule"
    window._select_topic(repository.state.topic)
    assert stack.currentWidget() is window._topic_details
    assert editor.text() == "Unfinished rule"
    window.close()
    application.processEvents()


def test_topic_expectation_editor_configures_numeric_and_existence_conditions(
) -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    management = MagicMock()
    management.list_expectations.return_value = ()
    management.create_expectation.side_effect = lambda item, **_kwargs: item
    view_model = MainViewModel(
        runtime_for(repository),
        repository.state.topic,
        expectation_management_service=management,
    )
    window = MainWindow(view_model)
    editor = window.findChild(QWidget, "topicExpectationEditor")
    condition_kind = editor.findChild(QComboBox, "expectationConditionKind")
    expected_editor = editor.findChild(
        QStackedWidget,
        "expectationExpectedEditor",
    )
    expected_values = editor.findChild(
        QPlainTextEdit,
        "expectationExpectedValues",
    )
    encoding = editor.findChild(QComboBox, "expectationEncoding")

    editor.findChild(QLineEdit, "expectationName").setText("Temperature")
    condition_kind.setCurrentIndex(
        condition_kind.findData(ConditionKind.NUMERIC_RANGE)
    )
    expected_values.setPlainText("1.5,3")

    assert expected_editor.currentWidget() is expected_values
    assert encoding.isHidden()
    editor.findChild(QPushButton, "saveExpectationButton").click()
    created = management.create_expectation.call_args.args[0]
    assert created.condition == NumericRangeCondition(
        Decimal("1.5"),
        Decimal("3"),
    )

    editor.findChild(QLineEdit, "expectationName").setText("Required topic")
    condition_kind.setCurrentIndex(
        condition_kind.findData(ConditionKind.TOPIC_EXISTS)
    )

    assert expected_editor.isHidden()
    editor.findChild(QPushButton, "saveExpectationButton").click()
    created = management.create_expectation.call_args.args[0]
    assert created.condition == TopicExistsCondition()

    editor.findChild(QLineEdit, "expectationName").setText("Recent topic")
    condition_kind.setCurrentIndex(
        condition_kind.findData(ConditionKind.FRESH_WITHIN)
    )
    expected = editor.findChild(QComboBox, "expectationExpectedValue")
    expected.setEditText("60")

    assert expected_editor.currentWidget() is expected
    assert not expected_editor.isHidden()
    assert encoding.isHidden()
    editor.findChild(QPushButton, "saveExpectationButton").click()
    created = management.create_expectation.call_args.args[0]
    assert created.condition == FreshnessCondition(60)
    window.close()
    application.processEvents()


def test_broker_expectation_editor_excludes_topic_only_conditions() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    management = MagicMock()
    management.list_expectations.return_value = ()
    editor = ExpectationEditor(
        MainViewModel(
            runtime_for(repository),
            expectation_management_service=management,
        ),
        "broker",
    )
    selector = editor.findChild(QComboBox, "expectationConditionKind")
    kinds = {
        ConditionKind(selector.itemData(index))
        for index in range(selector.count())
    }

    assert kinds == {
        ConditionKind.EQUAL,
        ConditionKind.IN_RANGE,
        ConditionKind.OUTSIDE,
    }
    editor.close()
    application.processEvents()


def test_topic_health_badge_opens_existing_expectation_settings() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    window = MainWindow(
        MainViewModel(runtime_for(repository), repository.state.topic)
    )
    window.show()
    application.processEvents()
    badge = window.findChild(QPushButton, "topicHealthBadge")

    assert badge.isVisibleTo(window._topic_details)
    badge.click()

    assert window._context_panel.isVisible()
    assert window._settings_tabs.currentWidget() is window._topic_expectations
    window.close()
    application.processEvents()


def test_health_refreshes_while_inspector_is_closed_without_navigation() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    runtime = runtime_for(repository)
    broker_id = runtime.active_broker.id
    expectation = HealthExpectation(
        uuid4(),
        1,
        True,
        HealthSeverity.CRITICAL,
        BrokerTarget(broker_id),
        EqualCondition("connected"),
        frozenset(),
        "Connected",
    )
    finding = SimpleNamespace(
        expectation_id=expectation.expectation_id,
        name=expectation.name,
        target_kind="broker",
        target="broker",
        status=HealthStatus.PROBLEM,
        evidence_summary="actual=disconnected; expected=connected",
        evidence_truncated=False,
    )
    report = SimpleNamespace(
        broker_id=broker_id,
        evaluated_at=datetime.now(timezone.utc),
        aggregate_status=HealthStatus.PROBLEM,
        evidence_complete=True,
        observation_status=HealthStatus.HEALTHY,
        observation_findings=(),
        expectation_findings=(finding,),
        active_failure_count=0,
        returned_count=1,
        omitted_count=0,
        checkpoint=None,
        delta=None,
    )
    health_query = MagicMock()
    health_query.get_health_report.return_value = report
    management = MagicMock()
    management.list_expectations.return_value = (expectation,)
    window = MainWindow(
        MainViewModel(
            runtime,
            repository.state.topic,
            health_query_service=health_query,
            expectation_management_service=management,
        )
    )
    timer = window.findChild(QObject, "healthRefreshTimer")
    timer.setInterval(0)

    window._view_model.topics_changed.emit()
    application.processEvents()

    assert health_query.get_health_report.called
    assert "Failed" in window.findChild(
        QPushButton, "brokerHealthSummary"
    ).text()
    assert window._inspector_stack.currentWidget() is window._topic_details
    window.close()
    application.processEvents()


def test_health_overview_omits_synthetic_broker_disconnected_finding() -> None:
    application = QApplication.instance() or QApplication([])
    runtime = runtime_for(FakeGuiRepository())
    report = SimpleNamespace(
        broker_id=runtime.active_broker.id,
        evaluated_at=datetime.now(timezone.utc),
        aggregate_status=HealthStatus.PROBLEM,
        evidence_complete=True,
        observation_status=HealthStatus.PROBLEM,
        observation_findings=(
            ObservationHealthFinding(
                ObservationFindingCode.BROKER_DISCONNECTED,
                HealthStatus.PROBLEM,
                "Broker is not connected.",
            ),
            ObservationHealthFinding(
                ObservationFindingCode.DROPPED_MESSAGES,
                HealthStatus.PROBLEM,
                "One message was dropped.",
            ),
        ),
        expectation_findings=(),
        active_failure_count=0,
        returned_count=0,
        omitted_count=0,
        checkpoint=None,
        delta=None,
    )
    health_query = MagicMock()
    health_query.get_health_report.return_value = report
    inspector = HealthInspector(
        MainViewModel(runtime, health_query_service=health_query)
    )

    inspector.refresh_health()

    table = inspector.findChild(QTableWidget, "currentBrokerHealthTable")
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "Dropped Messages"
    inspector.deleteLater()
    application.processEvents()


def test_health_inspector_renders_latest_finding_delta() -> None:
    application = QApplication.instance() or QApplication([])
    runtime = runtime_for(FakeGuiRepository())
    broker_id = runtime.active_broker.id
    event = SimpleNamespace(
        kind="severity_change",
        rule_id="device.online",
        matched_topic="devices/kitchen/status",
        previous_status=HealthStatus.PROBLEM,
        current_status=HealthStatus.PROBLEM,
        previous_severity=HealthSeverity.WARNING,
        current_severity=HealthSeverity.CRITICAL,
    )
    report = SimpleNamespace(
        broker_id=broker_id,
        evaluated_at=datetime.now(timezone.utc),
        aggregate_status=HealthStatus.PROBLEM,
        evidence_complete=True,
        observation_status=HealthStatus.HEALTHY,
        observation_findings=(),
        expectation_findings=(),
        active_failure_count=1,
        returned_count=0,
        omitted_count=0,
        checkpoint=SimpleNamespace(complete=True, omitted_count=0),
        delta=SimpleNamespace(
            events=(event,),
            complete=True,
            returned_count=1,
            omitted_count=0,
        ),
    )
    health_query = MagicMock()
    health_query.get_health_report.return_value = report
    inspector = HealthInspector(
        MainViewModel(runtime, health_query_service=health_query)
    )

    inspector.refresh_health()

    table = inspector.findChild(QTableWidget, "findingDeltaTable")
    message = inspector.findChild(QLabel, "findingDeltaMessage")
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "Severity Change"
    assert table.item(0, 1).text() == "device.online"
    assert table.item(0, 2).text() == "devices/kitchen/status"
    assert table.item(0, 3).text() == "Failed"
    assert table.item(0, 4).text() == "Warning -> Critical"
    assert message.text() == "Showing 1 finding change event(s)."
    inspector.close()
    application.processEvents()


def test_inspector_starts_from_selection_and_owns_one_snapshot() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    empty_settings = QSettings(
        str(Path(".pytest_cache/inspector-empty.ini").resolve()),
        QSettings.Format.IniFormat,
    )
    empty_settings.clear()
    snapshot_window = MainWindow(
        MainViewModel(runtime_for(repository)),
        empty_settings,
    )

    stack = snapshot_window.findChild(QStackedWidget, "inspectorStack")
    snapshots = snapshot_window.findChildren(SnapshotPanel)
    scroll = snapshot_window.findChild(QScrollArea, "snapshotPanelScrollArea")
    assert stack.currentIndex() == 0
    assert len(snapshots) == 1
    assert snapshots[0] is snapshot_window._snapshot_panel
    assert snapshot_window._observer_tree.findChildren(SnapshotPanel) == []
    assert scroll.widget() is snapshot_window._snapshot_panel
    assert scroll.maximumHeight() == 16777215

    restored_settings = QSettings(
        str(Path(".pytest_cache/inspector-restored.ini").resolve()),
        QSettings.Format.IniFormat,
    )
    restored_settings.clear()
    restored_settings.setValue("workspace/selectedTopic", repository.state.topic)
    details_window = MainWindow(
        MainViewModel(runtime_for(repository)),
        restored_settings,
    )
    assert details_window.findChild(
        QStackedWidget,
        "inspectorStack",
    ).currentWidget() is details_window._topic_details

    snapshot_window.close()
    details_window.close()
    application.processEvents()


def test_inspector_navigation_preserves_snapshot_and_tree_editing_state() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    window = MainWindow(MainViewModel(runtime_for(repository)))
    stack = window.findChild(QStackedWidget, "inspectorStack")
    filter_edit = window.findChild(QLineEdit, "snapshotTopicFilter")
    search_edit = window._observer_tree._search_edit
    tree = window.findChild(QTreeView, "observerTree")

    window._apply_snapshot_query(SnapshotQuery("home/#", None, 9, 256))
    filter_edit.setText("unsubmitted/#")
    search_edit.setText("temperature")
    tree.expandAll()
    expanded = window._observer_tree._items["home"].index()
    expanded = window._observer_tree._proxy.mapFromSource(expanded)
    assert tree.isExpanded(expanded)

    selected = window._observer_tree._items[repository.state.topic].index()
    selected = window._observer_tree._proxy.mapFromSource(selected)
    tree.setCurrentIndex(selected)
    tree.clicked.emit(selected)
    assert stack.currentWidget() is window._topic_details
    window.findChild(QToolButton, "topicEditButton").setChecked(True)
    assert not window._context_panel.isHidden()

    inspect = window.findChild(QPushButton, "inspectSnapshotButton")
    requests: list[bool] = []
    window._broker_connection.inspect_snapshot_requested.connect(
        lambda: requests.append(True)
    )
    inspect.click()
    assert requests == [True]
    assert stack.currentIndex() == 0
    assert window._view_model.topic == repository.state.topic
    assert window._context_panel.isHidden()
    assert filter_edit.text() == "unsubmitted/#"
    assert window._view_model.snapshot_query == SnapshotQuery(
        "home/#", None, 9, 256
    )
    assert search_edit.text() == "temperature"
    assert tree.isExpanded(expanded)

    tree.clicked.emit(tree.currentIndex())
    assert stack.currentWidget() is window._topic_details
    assert not window._context_panel.isHidden()
    tree.activated.emit(tree.currentIndex())
    assert stack.currentWidget() is window._topic_details

    window._select_topic("")
    assert stack.currentIndex() == 0
    assert window._view_model.topic == ""
    window.close()
    application.processEvents()


def test_background_updates_preserve_the_explicit_inspector_view() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    view_model = MainViewModel(runtime_for(repository), repository.state.topic)
    window = MainWindow(view_model)
    stack = window.findChild(QStackedWidget, "inspectorStack")

    assert stack.currentWidget() is window._topic_details
    view_model.refresh_snapshot(clear_invalid_selection=False)
    view_model.connection_changed.emit()
    assert stack.currentWidget() is window._topic_details

    window._show_snapshot()
    view_model.refresh_snapshot(clear_invalid_selection=False)
    view_model.connection_changed.emit()
    assert stack.currentIndex() == 0
    assert view_model.topic == repository.state.topic

    window._show_health()
    view_model.refresh_snapshot(clear_invalid_selection=False)
    view_model.connection_changed.emit()
    assert stack.currentWidget() is window._health_inspector
    assert view_model.topic == repository.state.topic
    window.close()
    application.processEvents()


async def test_reconnect_preserves_the_current_inspector_view() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    view_model = MainViewModel(runtime_for(repository), repository.state.topic)
    view_model._snapshot_service._sleep = AsyncMock()
    window = MainWindow(view_model)
    stack = window.findChild(QStackedWidget, "inspectorStack")

    await view_model.reconnect_and_observe()
    assert stack.currentWidget() is window._topic_details

    window._show_snapshot()
    await view_model.reconnect_and_observe()
    assert stack.currentIndex() == 0
    assert view_model.topic == repository.state.topic

    window._show_health()
    await view_model.reconnect_and_observe()
    assert stack.currentWidget() is window._health_inspector
    assert view_model.topic == repository.state.topic
    window.close()
    application.processEvents()


def test_observer_empty_states_explain_recovery_actions() -> None:
    application = QApplication.instance() or QApplication([])
    pane = ObserverTreePane()
    pane.render_empty_state("disconnected", (), False, False, False)
    assert "No subscriptions" in pane.findChild(QLabel, "observerEmptyStateText").text()

    pane.render_empty_state("connected", (Subscription("devices/#"),), True, False, False)
    action = pane.findChild(QToolButton, "observerEmptyStateAction")
    assert "current snapshot filters" in pane.findChild(
        QLabel, "observerEmptyStateText"
    ).text()
    assert action.text() == "Clear filters"
    pane.deleteLater()
    application.processEvents()


def test_light_theme_keeps_dialog_and_empty_state_text_readable() -> None:
    application = QApplication.instance() or QApplication([])

    apply_light_theme(application)

    palette = application.palette()
    assert palette.color(QPalette.ColorRole.Window).name() == "#f3f4f6"
    assert palette.color(QPalette.ColorRole.WindowText).name() == "#202124"
    assert palette.color(QPalette.ColorRole.ButtonText).name() == "#202124"
    assert "QMessageBox QLabel" in LIGHT_THEME
    assert "QFrame#observerEmptyState" in LIGHT_THEME
    assert "QLabel#observerEmptyStateText" in LIGHT_THEME
    assert "QTabBar#topicDetailsMode::tab" in LIGHT_THEME
    assert "QTabBar#topicDetailsMode::tab:selected" in LIGHT_THEME
    assert "color: #ffffff; background: #405d7a" in LIGHT_THEME


def test_cache_administration_warns_when_limits_are_approached() -> None:
    application = QApplication.instance() or QApplication([])
    view_model = MainViewModel(runtime_for(FakeGuiRepository()))
    profile = view_model.active_broker_profile
    view_model._retention_policy = ObservationRetentionPolicy(
        max_entries_per_broker=10,
        max_entries_total=20,
        max_payload_bytes_per_topic=10,
        max_payload_bytes_per_broker=100,
        max_persisted_payload_database_bytes_total=200,
        warning_threshold=0.8,
    )
    view_model._cache_usage = CacheUsageSummary(
        (BrokerCacheUsage(profile.id, 8, 80, None, None),)
    )
    dialog = StoredObservationsDialog(view_model)
    dialog.render()

    warning = dialog.findChild(QLabel, "cacheRetentionWarningBanner")
    assert not warning.isHidden()
    assert "80%" in warning.text()
    dialog.deleteLater()
    application.processEvents()


def test_observation_history_renders_results_and_inspects_payload_as_plain_text() -> None:
    application = QApplication.instance() or QApplication([])
    view_model = MainViewModel(runtime_for(FakeGuiRepository()))
    broker_id = view_model.active_broker_profile.id
    message = TopicMessage(
        broker_id,
        "untrusted/<topic>",
        b"<b>plain MQTT payload</b>",
        2,
        True,
        datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        25,
        9,
        uuid4(),
    )
    view_model._stored_observation_results = (message,)
    dialog = StoredObservationsDialog(view_model)
    requested: list[UUID] = []
    dialog.message_requested.connect(requested.append)

    dialog.render()

    table = dialog.findChild(QTableWidget, "storedObservationResultsTable")
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "untrusted/<topic>"
    assert table.item(0, 2).text() == "25 bytes"
    assert table.item(0, 3).text() == "9"
    assert table.item(0, 4).text() == "2"
    assert table.item(0, 5).text() == "Yes"
    assert dialog.findChild(QLabel, "historyQueryState").text() == (
        "1 stored observation returned."
    )
    table.selectRow(0)
    assert requested == [message.observation_id]

    view_model._selected_stored_observation = message
    dialog.render()

    payload = dialog.findChild(QPlainTextEdit, "storedObservationPayload")
    assert payload.toPlainText() == "<b>plain MQTT payload</b>"
    dialog.deleteLater()
    application.processEvents()


def test_observation_history_renders_empty_results_and_accessible_controls() -> None:
    application = QApplication.instance() or QApplication([])
    dialog = StoredObservationsDialog(
        MainViewModel(runtime_for(FakeGuiRepository()))
    )

    dialog.render()

    assert dialog.findChild(QLineEdit, "historyTopicFilter").text() == "#"
    assert dialog.findChild(QSpinBox, "historyResultLimit").value() == 50
    assert dialog.findChild(
        QTableWidget,
        "storedObservationResultsTable",
    ).rowCount() == 0
    assert dialog.findChild(QLabel, "historyQueryState").text() == (
        "No stored observations match this query."
    )
    assert dialog.findChild(
        QPushButton,
        "queryStoredObservationsButton",
    ).accessibleName() == "Query stored observations"
    dialog.deleteLater()
    application.processEvents()


def test_cache_administration_keeps_global_totals_and_renders_scoped_summary() -> None:
    application = QApplication.instance() or QApplication([])
    view_model = MainViewModel(runtime_for(FakeGuiRepository()))
    selected, other = view_model.broker_profiles
    selected_usage = BrokerCacheUsage(selected.id, 2, 20, None, None)
    other_usage = BrokerCacheUsage(other.id, 3, 30, None, None)
    view_model._retention_policy = ObservationRetentionPolicy()
    view_model._cache_usage = CacheUsageSummary((selected_usage, other_usage))
    view_model._broker_cache_usage = CacheUsageSummary((selected_usage,))
    dialog = StoredObservationsDialog(view_model)

    dialog.render()

    table = dialog.findChild(QTableWidget, "cacheUsageTable")
    assert table.item(2, 0).text() == "All brokers"
    assert table.item(2, 2).text() == "5"
    assert table.item(2, 3).text() == "50 bytes"
    scoped = dialog.findChild(QLabel, "selectedBrokerStorageSummary").text()
    assert "Selected broker: 2 observations" in scoped
    assert "20 bytes stored payload" in scoped
    dialog.deleteLater()
    application.processEvents()


class FakeGuiRepository:
    connection_status = "connected"
    topic_update_interval = 0.0
    dropped_message_count = 0
    subscriptions = (Subscription("home/+/temperature"),)

    def __init__(self) -> None:
        self.subscriptions = type(self).subscriptions
        self.removed_subscriptions: list[Subscription] = []
        self.broker_configurations: list[MqttConfig] = []
        self.state = TopicState(
            name="temperature",
            topic="home/kitchen/temperature",
            payload=b"21.5",
            qos=1,
            retain=False,
            recieved_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )

    def get_state(self, topic: str) -> TopicState | None:
        return self.state if topic == self.state.topic else None

    async def messages(self) -> AsyncIterator[MqttMessage]:
        if False:
            yield MqttMessage("", b"", 0, False)

    async def connection_statuses(self) -> AsyncIterator[object]:
        if False:
            yield "connected"

    def drain_pending_messages(self) -> tuple[MqttMessage, ...]:
        return ()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def connect(self) -> None:
        pass

    async def reconnect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def remove_subscription(self, subscription: Subscription) -> None:
        self.removed_subscriptions.append(subscription)
        self.subscriptions = tuple(
            item for item in self.subscriptions if item != subscription
        )

    async def add_subscription(self, subscription: Subscription) -> None:
        self.subscriptions += (subscription,)

    async def update_subscription(
        self, original_filter: str, subscription: Subscription
    ) -> None:
        self.subscriptions = tuple(
            subscription if item.topic_filter == original_filter else item
            for item in self.subscriptions
        )

    async def update_broker(
        self,
        mqtt_config: MqttConfig,
        subscriptions: tuple[Subscription, ...] | None = None,
    ) -> None:
        self.broker_configurations.append(mqtt_config)


class FakeBrokerRepository:
    def __init__(self, mqtt_config: MqttConfig) -> None:
        default_profile = self._profile("Default", mqtt_config)
        local_profile = self._profile("Local MQTT", MqttConfig("localhost", 1883, "", ""))
        self._profiles = {
            default_profile.id: default_profile,
            local_profile.id: local_profile,
        }
        self._active_profile_id = default_profile.id
        self.updated_mqtt: list[MqttConfig] = []

    def get_mqtt(self) -> MqttConfig:
        return self.get_profile().config

    def update_mqtt(self, mqtt_config: MqttConfig) -> None:
        self.activate_profile(self._active_profile_id, mqtt_config)

    def get_profile(self, profile_id: UUID | None = None) -> BrokerProfile:
        return self._profiles[profile_id or self._active_profile_id]

    def get_all_profiles(self) -> tuple[BrokerProfile, ...]:
        return tuple(self._profiles.values())

    def create_profile(
        self,
        name: str,
        mqtt_config: MqttConfig,
    ) -> BrokerProfile:
        profile = self._profile(name.strip(), mqtt_config)
        self._profiles[profile.id] = profile
        return profile

    def update_profile(self, profile: BrokerProfile) -> None:
        self._profiles[profile.id] = profile

    def delete_profile(self, profile_id: UUID) -> BrokerProfile:
        return self._profiles.pop(profile_id)

    def activate_profile(
        self,
        profile_id: UUID,
        mqtt_config: MqttConfig | None = None,
    ) -> None:
        profile = self.get_profile(profile_id)
        if mqtt_config is not None:
            profile.config = mqtt_config
            self.updated_mqtt.append(mqtt_config)
        self._active_profile_id = profile_id

    def select_active_profile(self, profile_id: UUID) -> None:
        self.activate_profile(profile_id)
        self.updated_mqtt.append(self.get_profile(profile_id).config)

    def replace_subscriptions(
        self, workspace_id: UUID, subscriptions: tuple[Subscription, ...]
    ) -> None:
        profile = next(
            item for item in self._profiles.values() if item.workspace_id == workspace_id
        )
        profile.workspace.subscriptions = subscriptions

    def update_observer_workspace(self, workspace: ObserverWorkspace) -> None:
        self._profiles[workspace.profile_id].workspace = workspace

    @staticmethod
    def _profile(name: str, mqtt_config: MqttConfig) -> BrokerProfile:
        profile_id = uuid4()
        workspace = ObserverWorkspace(
            id=uuid4(),
            profile_id=profile_id,
        )
        return BrokerProfile(profile_id, name, mqtt_config, workspace.id, workspace)


class FakeTopicGateRuntime(TopicGateRuntime):
    """Runtime test double backed by in-memory repositories."""


class FakeCurrentTopicReader:
    def __init__(
        self,
        repositories: dict[UUID, FakeGuiRepository],
    ) -> None:
        self._repositories = repositories
        self._observation_ids: dict[tuple[UUID, str], UUID] = {}

    def get_current_topics(self, broker_id: UUID) -> tuple[CurrentTopic, ...]:
        repository = self._repositories.get(broker_id)
        if repository is None:
            return ()
        state = repository.state
        return (self._current_topic(broker_id, state),)

    def get_current_topic(
        self,
        broker_id: UUID,
        topic: str,
    ) -> CurrentTopic | None:
        repository = self._repositories.get(broker_id)
        state = None if repository is None else repository.get_state(topic)
        if state is None:
            return None
        return self._current_topic(broker_id, state)

    def _current_topic(self, broker_id: UUID, state: TopicState) -> CurrentTopic:
        observation_id = self._observation_ids.setdefault(
            (broker_id, state.topic),
            uuid4(),
        )
        return CurrentTopic(
            TopicMessage(
                broker_id=broker_id,
                topic=state.topic,
                payload=state.payload,
                qos=state.qos,
                retain=state.retain,
                received_at=state.received_at,
                payload_size=state.payload_size or len(state.payload),
                message_count=state.message_count,
                observation_id=observation_id,
            ),
            ObservationStatus.LIVE,
        )


def runtime_for(
    repository: FakeGuiRepository,
    broker_repository: FakeBrokerRepository | None = None,
) -> FakeTopicGateRuntime:
    brokers = broker_repository or FakeBrokerRepository(
        MqttConfig("broker", 1883, "", "")
    )
    profiles = brokers.get_all_profiles()
    repositories = {profile.id: repository for profile in profiles}
    return FakeTopicGateRuntime(
        brokers,
        repositories,
        brokers.get_profile().id,
        lambda _profile: repository,
        current_topics=FakeCurrentTopicReader(repositories),
    )


def test_main_window_builds_three_pane_workspace_and_collapsible_log() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    view_model = MainViewModel(
        runtime_for(
            repository,
            FakeBrokerRepository(MqttConfig("broker", 1883, "", "")),
        ),
        repository.state.topic,
    )
    settings = QSettings(
        str(Path(".pytest_cache/gui-layout.ini").resolve()),
        QSettings.Format.IniFormat,
    )
    settings.clear()

    window = MainWindow(view_model, settings)

    splitter = window.findChild(QSplitter, "workspaceSplitter")
    log_dock = window.findChild(QDockWidget, "logConsoleDock")
    assert window.windowTitle() == "TopicGate Desktop"
    assert window.findChild(QAction, "aboutAction").text() == "About TopicGate"
    assert splitter is not None
    assert splitter.count() == 3
    assert log_dock is not None
    assert window.findChild(QLabel, "settingsHint").text() == (
        "Matched by subscription: home/+/temperature"
    )
    assert (
        window.findChild(QLabel, "settingsHint").textFormat()
        == Qt.TextFormat.PlainText
    )
    connection_controls = window.findChild(ConnectionControls)
    connection_button = window.menuBar().cornerWidget(
        Qt.Corner.TopRightCorner
    )
    assert connection_controls is not None
    assert connection_button is None
    assert window.findChild(
        QComboBox,
        "connectionBrokerSelector",
    ).currentText() == "Default"
    status = window.findChild(QLabel, "brokerConnectionStatus")
    assert status.text() == "Connected"
    assert status.accessibleName() == "MQTT connection status"
    assert window._broker_connection.header_layout.indexOf(
        window._broker_connection.heading
    ) == 0
    assert window._broker_connection.header_layout.indexOf(status) == 1
    assert window.findChild(QLabel, "activeBrokerEndpoint") is None
    assert window.findChild(QToolButton, "brokerSettingsButton") is None
    assert window.findChild(QWidget, "applicationHeader") is None
    assert window.findChild(QToolButton, "brokerProfileButton") is None
    assert window.findChild(QToolBar, "observerToolbar") is None
    lifecycle_action = window.findChild(
        QAction, "connectionLifecycleAction"
    )
    assert lifecycle_action.text() == "&Reconnect"
    assert lifecycle_action.isEnabled()
    assert window.findChild(QAction, "disconnectAction").isEnabled()

    window.close()
    application.processEvents()


def _broker_profile_row(window: MainWindow, name: str) -> QWidget:
    menu = window.findChild(QMenu, "brokerProfileSelectorMenu")
    for action in menu.actions():
        if not isinstance(action, QWidgetAction):
            continue
        row = action.defaultWidget()
        select_button = row.findChild(QToolButton, "selectBrokerProfileButton")
        if select_button.text() == name:
            return row
    raise AssertionError(f"Broker profile row not found: {name}")


def test_compact_broker_pane_exposes_switching_and_connection_actions() -> None:
    application = QApplication.instance() or QApplication([])
    view_model = MainViewModel(
        runtime_for(
            FakeGuiRepository(),
            FakeBrokerRepository(MqttConfig("broker", 1883, "", "")),
        )
    )
    window = MainWindow(view_model)
    selector = window.findChild(QComboBox, "connectionBrokerSelector")
    lifecycle = window.findChild(QPushButton, "brokerLifecycleButton")
    health = window.findChild(QPushButton, "brokerHealthSummary")
    inspect_snapshot = window.findChild(QPushButton, "inspectSnapshotButton")
    profile_menu = window.findChild(QMenu, "brokerProfileSelectorMenu")

    assert [selector.itemText(index) for index in range(selector.count())] == [
        "Default",
        "Local MQTT",
    ]
    assert selector.currentText() == "Default"
    assert lifecycle.text() == "Disconnect"
    assert lifecycle.isEnabled()
    assert window.findChild(QPushButton, "brokerDisconnectButton") is None
    assert inspect_snapshot.isEnabled()
    assert inspect_snapshot.accessibleName() == "Inspect broker snapshot"
    window.resize(window.minimumSize())
    window.show()
    application.processEvents()
    assert selector.isVisible()
    assert health.isVisible()
    assert inspect_snapshot.isVisible()
    assert lifecycle.isVisible()
    assert lifecycle.text() == "Disconnect"
    assert selector.geometry().top() == lifecycle.geometry().top()
    assert health.geometry().top() == inspect_snapshot.geometry().top()
    assert selector.geometry().right() == health.geometry().right()
    assert lifecycle.geometry().left() == inspect_snapshot.geometry().left()
    assert lifecycle.width() == inspect_snapshot.width()
    assert selector.geometry().top() < health.geometry().top()
    assert [
        action.defaultWidget()
        .findChild(QToolButton, "selectBrokerProfileButton")
        .text()
        for action in profile_menu.actions()
        if isinstance(action, QWidgetAction)
    ] == [
        "Default",
        "Local MQTT",
    ]
    for profile_name in ("Default", "Local MQTT"):
        row = _broker_profile_row(window, profile_name)
        edit_button = row.findChild(QToolButton, "editBrokerProfileButton")
        delete_button = row.findChild(QToolButton, "deleteBrokerProfileButton")
        assert edit_button.text() == "Edit"
        assert not edit_button.icon().isNull()
        assert delete_button.text() == "Delete"
        assert not delete_button.icon().isNull()
    assert window.findChild(QAction, "addBrokerProfilePaneAction").text() == (
        "+ Add Broker"
    )
    assert window.findChild(QToolButton, "manageBrokerProfilesButton") is None
    assert window.findChild(QMenu, "editBrokerProfilePaneMenu") is None
    window._broker_connection.render(view_model, busy=True)
    assert not selector.isEnabled()
    assert not window.findChild(QAction, "addBrokerProfilePaneAction").isEnabled()
    assert all(
        not _broker_profile_row(window, profile_name).isEnabled()
        for profile_name in ("Default", "Local MQTT")
    )
    assert window.findChild(QMenu, "connectionMenu") is None
    assert [action.text() for action in window.menuBar().actions()] == [
        "&File",
        "Stored observations",
        "&View",
        "&Help",
    ]
    file_action = window.menuBar().actions()[0]
    file_menu = file_action.menu()
    assert file_menu is not None
    assert window.findChild(QAction, "storedObservationsAction") not in (
        file_menu.actions()
    )
    assert window.findChild(QAction, "healthAction") not in file_menu.actions()
    assert window.findChild(QAction, "healthAction") in window._view_menu.actions()

    assert window.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is None
    window.close()
    application.processEvents()


def test_broker_pane_lifecycle_button_connects_or_disconnects() -> None:
    application = QApplication.instance() or QApplication([])
    view_model = MainViewModel(runtime_for(FakeGuiRepository()))
    pane = BrokerConnectionPane()
    requests: list[str] = []
    pane.connect_requested.connect(lambda: requests.append("connect"))
    pane.disconnect_requested.connect(lambda: requests.append("disconnect"))
    lifecycle = pane.findChild(QPushButton, "brokerLifecycleButton")

    pane.render(view_model)
    assert lifecycle.text() == "Disconnect"
    assert lifecycle.isEnabled()
    lifecycle.click()

    view_model._connection_status = "disconnected"
    pane.render(view_model)
    assert lifecycle.text() == "Connect"
    lifecycle.click()

    assert requests == ["disconnect", "connect"]
    pane.deleteLater()
    application.processEvents()


def test_topic_details_renders_broker_topics_as_literal_plain_text() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    pane = TopicDetailsPane()
    topic_heading = pane.heading
    assert topic_heading.textFormat() == Qt.TextFormat.PlainText

    topics = (
        "home/kitchen/temperature",
        '<b>spoofed</b><img src="file:///C:/secret">',
        '\\\\attacker\\share\\<img src="//attacker/share/pixel.png">',
    )
    for topic in topics:
        pane.render(MainViewModel(runtime_for(repository), topic))
        assert topic_heading.text() == topic
        assert topic_heading.toolTip() == topic
        assert topic_heading.accessibleName() == f"Selected topic: {topic}"
        assert topic_heading.textFormat() == Qt.TextFormat.PlainText

    pane.deleteLater()
    application.processEvents()


def test_topic_metadata_hides_advanced_fields_until_requested() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    pane = TopicDetailsPane()
    pane.render(MainViewModel(runtime_for(repository), repository.state.topic))
    advanced = pane.findChild(QToolButton, "topicMetadataAdvancedButton")
    source = pane.findChild(QLabel, "observationSourceLabel")
    state = pane.findChild(QLabel, "topicStateStatusLabel")
    messages = pane.findChild(QLabel, "messageCountLabel")
    raw = pane.findChild(QPlainTextEdit, "rawPayload")

    assert advanced.text() == "Advanced"
    assert source.isHidden()
    assert raw.isHidden()
    assert not state.isHidden()
    assert not messages.isHidden()

    advanced.click()

    assert advanced.text() == "Hide advanced"
    assert not source.isHidden()
    assert not raw.isHidden()

    pane.deleteLater()
    application.processEvents()


def test_topic_details_elides_long_heading_but_preserves_full_topic() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    topic = "building/" + "/".join(
        f"very-long-segment-{index}" for index in range(20)
    )
    pane = TopicDetailsPane()
    pane.resize(320, 640)
    pane.render(MainViewModel(runtime_for(repository), topic))
    pane.show()
    application.processEvents()

    assert pane.heading.text() != topic
    assert "\N{HORIZONTAL ELLIPSIS}" in pane.heading.text()
    assert pane.heading.toolTip() == topic
    assert pane.heading.accessibleName() == f"Selected topic: {topic}"

    pane.close()
    pane.deleteLater()
    application.processEvents()


def test_topic_details_switches_between_payload_and_embedded_publish() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    view_model = MainViewModel(runtime_for(repository), repository.state.topic)
    pane = TopicDetailsPane()
    pane.render(view_model)
    pane.show()
    application.processEvents()

    modes = pane.findChild(QTabBar, "topicDetailsMode")
    decoded = pane.findChild(QPlainTextEdit, "decodedPayload")
    raw = pane.findChild(QPlainTextEdit, "rawPayload")
    advanced = pane.findChild(QToolButton, "topicMetadataAdvancedButton")
    publish = pane.findChild(QWidget, "topicPublishPane")
    publish_hint = pane.findChild(QLabel, "publishTopicHint")
    publish_payload = pane.findChild(QPlainTextEdit, "publishPayload")

    assert modes.currentIndex() == 0
    assert modes.expanding()
    assert not modes.drawBase()
    assert modes.tabText(0) == "Payload"
    assert modes.tabText(1) == "Publish"
    assert decoded.toPlainText() == "21.5"
    assert not decoded.isHidden()
    assert publish.isHidden()
    assert pane.heading.toolTip() == repository.state.topic
    assert publish_hint.isHidden()

    advanced.click()
    assert not raw.isHidden()
    publish_payload.setPlainText("outgoing draft")
    modes.setCurrentIndex(1)
    pane.render(view_model)

    assert pane.findChild(QWidget, "topicPayloadContent").isHidden()
    assert not publish.isHidden()
    assert pane.heading.toolTip() == repository.state.topic
    assert publish_hint.isHidden()
    assert publish_payload.toPlainText() == "outgoing draft"

    modes.setCurrentIndex(0)

    assert not decoded.isHidden()
    assert not raw.isHidden()
    assert advanced.isChecked()
    assert pane.heading.toolTip() == repository.state.topic
    assert publish_payload.toPlainText() == "outgoing draft"
    pane.close()
    pane.deleteLater()
    application.processEvents()


def test_publish_mode_displays_but_rejects_wildcard_or_empty_selection() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    wildcard = Subscription("home/+/temperature")
    repository.subscriptions = (wildcard,)
    view_model = MainViewModel(runtime_for(repository), wildcard.topic_filter)
    pane = TopicDetailsPane()
    pane.render(view_model)

    modes = pane.findChild(QTabBar, "topicDetailsMode")
    publish_hint = pane.findChild(QLabel, "publishTopicHint")
    publish_button = pane.findChild(QPushButton, "publishButton")
    edit_button = pane.findChild(QToolButton, "topicEditButton")
    context_kind = pane.findChild(QLabel, "topicContextKind")
    modes.setCurrentIndex(1)

    assert pane.heading.text() == wildcard.topic_filter
    assert context_kind.text() == "FILTER"
    assert not context_kind.isHidden()
    assert publish_hint.text() == (
        "Select a concrete topic from the Payload tab to publish."
    )
    assert not publish_button.isEnabled()
    assert edit_button.isEnabled()

    view_model.select_topic("")
    pane.render(view_model)

    assert modes.currentIndex() == 1
    assert pane.heading.text() == "No topic selected"
    assert context_kind.isHidden()
    assert publish_hint.text() == "Select a topic to publish a message."
    assert not publish_button.isEnabled()
    assert not edit_button.isEnabled()
    pane.deleteLater()
    application.processEvents()


def test_embedded_publish_preserves_validation_busy_state_and_signal() -> None:
    application = QApplication.instance() or QApplication([])
    pane = TopicDetailsPane()
    publisher = pane.findChild(PublishPane, "topicPublishPane")
    topic_hint = pane.findChild(QLabel, "publishTopicHint")
    payload = pane.findChild(QPlainTextEdit, "publishPayload")
    encoding = pane.findChild(QComboBox, "publishEncoding")
    button = pane.findChild(QPushButton, "publishButton")
    requested: list[tuple[str, str, str]] = []
    pane.publish_requested.connect(
        lambda selected_topic, value, selected_encoding: requested.append(
            (selected_topic, value, selected_encoding)
        )
    )

    publisher.render("devices/door", True, False)
    payload.setPlainText("b3Blbg==")
    encoding.setCurrentIndex(1)

    assert button.isEnabled()
    assert topic_hint.isHidden()
    button.click()
    assert requested == [("devices/door", "b3Blbg==", "base64")]

    publisher.render("devices/+", True, False)
    assert topic_hint.text() == (
        "Select a concrete topic from the Payload tab to publish."
    )
    assert not topic_hint.isHidden()
    assert not button.isEnabled()

    publisher.render("devices/door", True, True)
    assert button.text() == "Publishing…"
    assert not payload.isEnabled()
    assert not encoding.isEnabled()
    assert not button.isEnabled()
    pane.deleteLater()
    application.processEvents()


def test_topic_details_distinguishes_wildcard_filters_from_concrete_topics() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    repository.subscriptions = (Subscription("home/+/temperature"),)
    view_model = MainViewModel(
        runtime_for(repository),
        repository.subscriptions[0].topic_filter,
    )
    pane = TopicDetailsPane()
    context_kind = pane.findChild(QLabel, "topicContextKind")
    decoded = pane.findChild(QPlainTextEdit, "decodedPayload")
    raw = pane.findChild(QPlainTextEdit, "rawPayload")
    topics = pane.findChild(QTableWidget, "filterMatchingTopics")
    selected: list[str] = []
    pane.topic_selected.connect(selected.append)

    pane.render(view_model)

    assert pane.heading.text() == "home/+/temperature"
    assert pane.heading.accessibleName() == (
        "Selected topic filter: home/+/temperature"
    )
    assert not context_kind.isHidden()
    assert decoded.isHidden()
    assert raw.isHidden()
    assert topics.rowCount() == 1
    assert topics.columnCount() == 2
    assert topics.item(0, 0).text() == "home/kitchen/temperature"
    assert topics.horizontalHeaderItem(0).text() == "Topic"
    assert topics.horizontalHeaderItem(1).text() == "Last received"

    topics.cellClicked.emit(0, 0)

    assert selected == ["home/kitchen/temperature"]

    view_model.select_topic("home/kitchen/temperature")
    pane.render(view_model)

    assert pane.heading.text() == "home/kitchen/temperature"
    assert context_kind.isHidden()
    assert not decoded.isHidden()
    assert raw.isHidden()
    assert decoded.toPlainText() == "21.5"
    pane.deleteLater()
    application.processEvents()


def test_connection_controls_bundle_actions_and_request_signals() -> None:
    application = QApplication.instance() or QApplication([])
    controls = ConnectionControls(
        QAction("Add"),
        QAction("Edit"),
        QAction("Delete"),
    )
    broker = BrokerSummary(
        uuid4(),
        "Local broker",
        MqttConfig("broker.local", 1883, "", ""),
        False,
    )
    requests: list[str] = []
    controls.connect_requested.connect(lambda: requests.append("connect"))
    controls.reconnect_requested.connect(lambda: requests.append("reconnect"))
    controls.disconnect_requested.connect(lambda: requests.append("disconnect"))
    controls.details_requested.connect(lambda: requests.append("details"))

    controls.render((broker,), broker, "disconnected")
    controls.lifecycle_action.trigger()
    controls.render((broker,), broker, "connected")
    controls.lifecycle_action.trigger()
    controls.disconnect_action.trigger()
    controls.button.click()

    assert requests == ["connect", "reconnect", "disconnect", "details"]
    assert controls.button.text() == "Local broker · Connected"
    controls.deleteLater()
    application.processEvents()


def test_connection_control_prioritizes_actions_for_connection_state() -> None:
    application = QApplication.instance() or QApplication([])
    add_action = QAction("Add")
    edit_action = QAction("Edit")
    delete_action = QAction("Delete")
    controls = ConnectionControls(add_action, edit_action, delete_action)
    broker = BrokerSummary(
        uuid4(),
        "Local broker",
        MqttConfig("broker.local", 1883, "", ""),
        False,
    )

    controls.render((broker,), broker, "disconnected", False)
    assert controls.lifecycle_action.text() == "&Connect"
    assert controls.lifecycle_action.isEnabled()
    assert not controls.disconnect_action.isEnabled()

    controls.render((broker,), broker, "connected", False)
    assert controls.lifecycle_action.text() == "&Reconnect"
    assert controls.lifecycle_action.isEnabled()
    assert controls.disconnect_action.isEnabled()

    controls.render((broker,), broker, "connecting", False)
    assert controls.lifecycle_action.text() == "Connecting…"
    assert not controls.lifecycle_action.isEnabled()
    assert controls.disconnect_action.isEnabled()
    assert not add_action.isEnabled()
    assert not edit_action.isEnabled()

    controls.render((broker,), broker, "reconnecting", False)
    assert controls.lifecycle_action.text() == "Reconnecting…"
    assert not controls.lifecycle_action.isEnabled()
    assert controls.disconnect_action.isEnabled()
    assert not add_action.isEnabled()

    controls.render((broker,), broker, "reconnecting", True)
    assert not controls.disconnect_action.isEnabled()
    assert not add_action.isEnabled()
    assert not edit_action.isEnabled()
    assert not delete_action.isEnabled()
    controls.deleteLater()
    application.processEvents()


def test_connection_control_elides_long_broker_name_but_keeps_accessible_text() -> None:
    application = QApplication.instance() or QApplication([])
    controls = ConnectionControls(
        QAction("Add"),
        QAction("Edit"),
        QAction("Delete"),
    )
    name = (
        "A broker profile name that is intentionally much too long for the "
        "menu bar"
    )
    broker = BrokerSummary(
        uuid4(),
        name,
        MqttConfig("broker.local", 1883, "", ""),
        False,
    )

    controls.render((broker,), broker, "disconnected")

    assert "… · Disconnected" in controls.button.text()
    assert name in controls.button.accessibleName()
    assert "mqtt://broker.local:1883" in controls.button.accessibleDescription()
    assert controls.button.maximumWidth() == 320
    controls.deleteLater()
    application.processEvents()


def test_connection_actions_follow_the_rendered_connection_status() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    view_model = MainViewModel(
        runtime_for(
            repository,
            FakeBrokerRepository(MqttConfig("broker", 1883, "", "")),
        ),
    )
    window = MainWindow(view_model)

    lifecycle_action = window.findChild(QAction, "connectionLifecycleAction")
    disconnect_action = window.findChild(QAction, "disconnectAction")

    view_model._connection_status = "disconnected"
    view_model.connection_changed.emit()
    assert lifecycle_action.text() == "&Connect"
    assert lifecycle_action.isEnabled()
    assert not disconnect_action.isEnabled()

    view_model._connection_status = "reconnecting"
    view_model.connection_changed.emit()
    assert lifecycle_action.text() == "Reconnecting…"
    assert not lifecycle_action.isEnabled()
    assert disconnect_action.isEnabled()

    window.close()
    application.processEvents()


def test_unmatched_dynamic_topic_leaves_settings_disabled() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    view_model = MainViewModel(
        runtime_for(
            repository,
            FakeBrokerRepository(MqttConfig("broker", 1883, "", "")),
        ),
        "unmatched/topic",
    )
    settings = QSettings(
        str(Path(".pytest_cache/gui-unmatched.ini").resolve()),
        QSettings.Format.IniFormat,
    )
    settings.clear()

    window = MainWindow(view_model, settings)

    settings_hint = window.findChild(QLabel, "settingsHint")
    apply_button = window.findChild(QPushButton, "applySubscriptionButton")
    assert settings_hint.text() == "No editable subscription filter selected."
    assert not apply_button.isEnabled()
    window.close()
    application.processEvents()


def test_observer_tree_adds_trash_buttons_only_to_subscription_filters() -> None:
    application = QApplication.instance() or QApplication([])
    pane = ObserverTreePane()
    subscription = Subscription("home/+/temperature")
    requested: list[Subscription] = []
    pane.remove_filter_requested.connect(requested.append)

    pane.render(
        [subscription.topic_filter, "home/kitchen/temperature"],
        "",
        (subscription,),
    )

    buttons = pane.findChildren(QToolButton, "removeSubscriptionButton")
    tree = pane.findChild(QTreeView, "observerTree")
    assert len(buttons) == 1
    assert tree is not None
    assert tree.header().sectionSize(1) == 34
    assert buttons[0].size().width() == 24
    assert buttons[0].size().height() == 18
    assert not buttons[0].icon().isNull()
    assert "background-color: transparent" in buttons[0].styleSheet()
    assert "background-color: #fff5f5" in buttons[0].styleSheet()
    assert "QToolButton:hover" in buttons[0].styleSheet()
    assert buttons[0].toolTip() == (
        "Remove subscription home/+/temperature"
    )

    buttons[0].click()

    assert requested == [subscription]
    pane.deleteLater()


def test_observer_tree_filter_badges_route_to_the_wildcard_filter() -> None:
    application = QApplication.instance() or QApplication([])
    pane = ObserverTreePane()
    subscription = Subscription("home/+/temperature")
    nodes = build_topic_tree(
        (subscription.topic_filter, "home/kitchen/temperature"),
        (subscription,),
        ("home/kitchen/temperature",),
    )
    selected: list[str] = []
    pane.topic_selected.connect(selected.append)

    pane.render_tree(nodes, "home/kitchen/temperature", (subscription,))

    buttons = pane.findChildren(QToolButton, "topicFilterBadgeButton")
    reference = next(button for button in buttons if button.text() == "F1")
    assert reference.property("targetPath") == subscription.topic_filter
    assert "QToolButton:hover" in reference.styleSheet()
    assert "QTreeView#observerTree::item { border-left: 0; }" in LIGHT_THEME
    selected.clear()

    reference.click()

    assert selected == [subscription.topic_filter]
    pane.deleteLater()
    application.processEvents()


async def test_subscription_trash_button_runs_the_removal_workflow() -> None:
    async def scenario() -> None:
        application = QApplication.instance() or QApplication([])
        repository = FakeGuiRepository()
        view_model = MainViewModel(
            runtime_for(
                repository,
                FakeBrokerRepository(MqttConfig("broker", 1883, "", "")),
            ),
            repository.state.topic,
        )
        settings = QSettings(
            str(Path(".pytest_cache/gui-remove.ini").resolve()),
            QSettings.Format.IniFormat,
        )
        settings.clear()
        window = MainWindow(view_model, settings)
        button = window.findChild(QToolButton, "removeSubscriptionButton")

        assert button is not None
        button.click()
        await asyncio.sleep(0)

        assert repository.removed_subscriptions == [
            Subscription("home/+/temperature")
        ]
        assert repository.subscriptions == ()
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
        assert window.findChild(
            QToolButton, "removeSubscriptionButton"
        ) is None
        tree = window.findChild(QTreeView, "observerTree")
        assert tree is not None
        assert tree.model().rowCount() == 0
        window.close()
        application.processEvents()

    await scenario()


async def test_removing_exact_subscription_keeps_overlapping_observed_topic() -> None:
    async def scenario() -> None:
        application = QApplication.instance() or QApplication([])
        repository = FakeGuiRepository()
        wildcard = Subscription("home/+/temperature")
        exact = Subscription(repository.state.topic)
        repository.subscriptions = (wildcard, exact)
        view_model = MainViewModel(
            runtime_for(repository), repository.state.topic
        )
        window = MainWindow(view_model)
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
        exact_button = next(
            button
            for button in window.findChildren(
                QToolButton, "removeSubscriptionButton"
            )
            if button.toolTip() == f"Remove subscription {exact.topic_filter}"
        )

        assert [
            button.text()
            for button in window.findChildren(
                QToolButton,
                "topicFilterBadgeButton",
            )
        ].count("Filter 1") == 1

        exact_button.click()
        await asyncio.sleep(0)
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()

        assert repository.subscriptions == (wildcard,)
        assert view_model.topic_paths == [
            wildcard.topic_filter,
            repository.state.topic,
        ]
        assert [
            button.toolTip()
            for button in window.findChildren(
                QToolButton, "removeSubscriptionButton"
            )
        ] == [f"Remove subscription {wildcard.topic_filter}"]
        filter_badges = [
            button.text()
            for button in window.findChildren(
                QToolButton,
                "topicFilterBadgeButton",
            )
        ]
        state_badges = [
            label.text()
            for label in window.findChildren(QLabel, "topicStateBadge")
        ]
        assert filter_badges.count("Filter 1") == 1
        assert filter_badges.count("F1") == 1
        assert state_badges.count("Live") == 1
        reference = next(
            button
            for button in window.findChildren(
                QToolButton,
                "topicFilterBadgeButton",
            )
            if button.text() == "F1"
        )

        reference.click()
        application.processEvents()

        assert view_model.topic == wildcard.topic_filter
        summary = window.findChild(QTableWidget, "filterMatchingTopics")
        assert not summary.isHidden()
        assert summary.rowCount() == 1
        window.close()
        application.processEvents()

    await scenario()


def test_broker_settings_dialog_loads_current_configuration_and_validates_input() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    mqtt_config = MqttConfig("broker.local", 1883, "", "", False)
    view_model = MainViewModel(
        runtime_for(repository, FakeBrokerRepository(mqtt_config)),
    )
    dialog = BrokerSettingsDialog(view_model)

    host_edit = dialog.findChild(QLineEdit, "brokerHostEdit")
    port_edit = dialog.findChild(QLineEdit, "brokerPortEdit")
    password_edit = dialog.findChild(QLineEdit, "brokerPasswordEdit")
    username_edit = dialog.findChild(QLineEdit, "brokerUsernameEdit")
    tls_checkbox = dialog.findChild(QCheckBox, "brokerUseTlsCheckbox")
    security_error = dialog.findChild(QLabel, "brokerTransportSecurityError")
    apply_button = dialog.findChild(QPushButton, "applyBrokerSettingsButton")
    assert host_edit is not None
    assert port_edit is not None
    assert password_edit is not None
    assert username_edit is not None
    assert tls_checkbox is not None
    assert security_error is not None
    assert apply_button is not None
    assert host_edit.text() == "broker.local"
    assert port_edit.text() == "1883"
    assert password_edit.echoMode() == QLineEdit.EchoMode.Password
    assert not tls_checkbox.isChecked()
    assert security_error.isHidden()
    assert apply_button.isEnabled()
    host_edit.setText(" ")
    assert not apply_button.isEnabled()
    host_edit.setText("broker.local")
    port_edit.setText("not-a-port")
    assert not apply_button.isEnabled()
    port_edit.setText("65536")
    assert not apply_button.isEnabled()
    port_edit.setText("1883")
    assert apply_button.isEnabled()
    username_edit.setText("observer")
    assert apply_button.isEnabled()
    assert not security_error.isHidden()
    assert dialog.mqtt_config == MqttConfig(
        "broker.local", 1883, "observer", "", False
    )
    tls_checkbox.setChecked(True)
    assert security_error.isHidden()

    dialog.close()
    application.processEvents()


def test_broker_settings_dialog_masks_a_configured_password() -> None:
    application = QApplication.instance() or QApplication([])
    mqtt_config = MqttConfig(
        "broker.local",
        8883,
        "observer",
        "os-loaded-secret",
        True,
    )
    view_model = MainViewModel(
        runtime_for(FakeGuiRepository(), FakeBrokerRepository(mqtt_config)),
    )
    dialog = BrokerSettingsDialog(view_model)

    password_edit = dialog.findChild(QLineEdit, "brokerPasswordEdit")

    assert password_edit is not None
    assert password_edit.text() == ""
    assert password_edit.placeholderText() == "********"
    assert "os-loaded-secret" not in password_edit.text()

    dialog.close()
    application.processEvents()


def test_observer_tree_does_not_render_a_broker_profile_dropdown() -> None:
    application = QApplication.instance() or QApplication([])
    pane = ObserverTreePane()
    assert pane.findChild(QToolButton, "brokerProfileButton") is None
    pane.deleteLater()
    application.processEvents()


def test_broker_pane_creates_a_new_broker_profile() -> None:
    application = QApplication.instance() or QApplication([])
    broker_repository = FakeBrokerRepository(MqttConfig("broker", 1883, "", ""))
    view_model = MainViewModel(
        runtime_for(FakeGuiRepository(), broker_repository),
    )
    window = MainWindow(view_model)
    add_action = window.findChild(QAction, "addBrokerProfilePaneAction")
    assert add_action is not None

    add_action.trigger()
    dialog = window.findChild(BrokerSettingsDialog, "createBrokerProfileDialog")
    assert dialog is not None
    name_edit = dialog.findChild(QLineEdit, "brokerProfileNameEdit")
    host_edit = dialog.findChild(QLineEdit, "brokerHostEdit")
    apply_button = dialog.findChild(QPushButton, "applyBrokerSettingsButton")
    assert name_edit is not None
    assert host_edit is not None
    assert apply_button is not None
    assert not apply_button.isEnabled()

    name_edit.setText("Remote")
    host_edit.setText("remote.local")
    apply_button.click()

    assert [profile.name for profile in view_model.broker_profiles] == [
        "Default",
        "Local MQTT",
        "Remote",
    ]
    assert dialog.result() == QDialog.DialogCode.Accepted
    window.close()
    application.processEvents()


def test_about_action_opens_the_project_about_view() -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow(
        MainViewModel(
            runtime_for(
                FakeGuiRepository(),
                FakeBrokerRepository(MqttConfig("broker", 1883, "", "")),
            ),
        )
    )

    window.findChild(QAction, "aboutAction").trigger()

    dialog = window.findChild(AboutDialog, "aboutDialog")
    assert dialog is not None
    assert dialog.findChild(QLabel, "aboutTitle").text() == "TopicGate"
    assert dialog.findChild(QLabel, "aboutVersion").text().startswith("Version ")
    assert "SQLite" in dialog.findChild(QLabel, "aboutStorageText").text()
    assert "github.com/Dumdart/TopicGate" in dialog.findChild(
        QLabel,
        "aboutProjectLink",
    ).text()
    dialog.reject()
    window.close()
    application.processEvents()


def test_broker_pane_edits_an_inactive_profile_without_connecting() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    broker_repository = FakeBrokerRepository(MqttConfig("broker", 1883, "", ""))
    active_profile = broker_repository.get_profile()
    inactive_profile = broker_repository.get_all_profiles()[1]
    view_model = MainViewModel(runtime_for(repository, broker_repository))
    window = MainWindow(view_model)
    row = _broker_profile_row(window, inactive_profile.name)
    row.findChild(QToolButton, "editBrokerProfileButton").click()
    dialog = window.findChild(BrokerSettingsDialog, "brokerSettingsDialog")
    assert dialog is not None
    assert dialog.profile_id == inactive_profile.id
    dialog.findChild(QLineEdit, "brokerHostEdit").setText("fixed.local")
    dialog.findChild(QPushButton, "saveBrokerSettingsButton").click()

    assert repository.broker_configurations == []
    assert broker_repository.get_profile().id == active_profile.id
    assert (
        broker_repository.get_profile(active_profile.id).config.host
        != "fixed.local"
    )
    assert (
        broker_repository.get_profile(inactive_profile.id).config.host
        == "fixed.local"
    )
    assert dialog.result() == QDialog.DialogCode.Accepted
    window.close()
    application.processEvents()


async def test_broker_pane_deletes_the_active_profile_after_switching() -> None:
    async def scenario() -> None:
        application = QApplication.instance() or QApplication([])
        repository = FakeGuiRepository()
        broker_repository = FakeBrokerRepository(MqttConfig("broker", 1883, "", ""))
        view_model = MainViewModel(runtime_for(repository, broker_repository))
        window = MainWindow(view_model)
        row = _broker_profile_row(window, "Default")
        delete_button = row.findChild(QToolButton, "deleteBrokerProfileButton")

        with patch(
            "topicgate.gui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            delete_button.click()
            await asyncio.sleep(0)

        assert [profile.name for profile in view_model.broker_profiles] == [
            "Local MQTT"
        ]
        assert view_model.active_broker_profile.name == "Local MQTT"
        assert not window.findChild(
            QAction,
            "deleteBrokerProfileAction",
        ).isEnabled()
        remaining_delete = _broker_profile_row(
            window,
            "Local MQTT",
        ).findChild(QToolButton, "deleteBrokerProfileButton")
        assert not remaining_delete.isEnabled()
        window.close()
        application.processEvents()

    await scenario()


async def test_broker_pane_deletes_an_inactive_profile_without_switching() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    broker_repository = FakeBrokerRepository(MqttConfig("broker", 1883, "", ""))
    view_model = MainViewModel(runtime_for(repository, broker_repository))
    window = MainWindow(view_model)
    inactive_profile = broker_repository.get_all_profiles()[1]
    delete_button = _broker_profile_row(
        window,
        inactive_profile.name,
    ).findChild(QToolButton, "deleteBrokerProfileButton")

    with patch(
        "topicgate.gui.main_window.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ) as question:
        delete_button.click()
        await asyncio.sleep(0)

    assert [profile.name for profile in view_model.broker_profiles] == ["Default"]
    assert view_model.active_broker_profile.name == "Default"
    assert repository.broker_configurations == []
    assert "connect to another broker" not in question.call_args.args[2]
    window.close()
    application.processEvents()


async def test_broker_selector_confirms_before_shutting_down_and_switching() -> None:
    async def scenario() -> None:
        application = QApplication.instance() or QApplication([])
        repository = FakeGuiRepository()
        broker_repository = FakeBrokerRepository(MqttConfig("broker", 1883, "", ""))
        view_model = MainViewModel(
            runtime_for(repository, broker_repository),
            repository.state.topic,
        )
        window = MainWindow(view_model)
        stack = window.findChild(QStackedWidget, "inspectorStack")
        selector = window.findChild(QComboBox, "connectionBrokerSelector")
        assert selector is not None
        local_profile = broker_repository.get_all_profiles()[1]

        with patch(
            "topicgate.gui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as question:
            _broker_profile_row(window, local_profile.name).findChild(
                QToolButton,
                "selectBrokerProfileButton",
            ).click()
            await asyncio.sleep(0)

        assert repository.broker_configurations == [local_profile.config]
        assert broker_repository.get_profile().id == local_profile.id
        assert stack.currentIndex() == 0
        assert "Do you want to switch" in question.call_args.args[2]
        assert "shutting down the current MQTT connection" in question.call_args.args[2]
        window.close()
        application.processEvents()

    await scenario()


def test_cancelled_broker_switch_and_profile_updates_preserve_topic_details() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    broker_repository = FakeBrokerRepository(MqttConfig("broker", 1883, "", ""))
    view_model = MainViewModel(
        runtime_for(repository, broker_repository),
        repository.state.topic,
    )
    window = MainWindow(view_model)
    stack = window.findChild(QStackedWidget, "inspectorStack")
    active = view_model.active_broker_profile
    inactive = view_model.broker_profiles[1]

    with patch(
        "topicgate.gui.main_window.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ):
        window._confirm_broker_profile_switch(inactive.id)

    assert view_model.active_broker_profile.id == active.id
    assert stack.currentWidget() is window._topic_details

    view_model.save_broker_profile(active.id, active.config, active.name)
    assert stack.currentWidget() is window._topic_details
    window.close()
    application.processEvents()


def test_tls_defaults_port_only_until_the_user_changes_it() -> None:
    application = QApplication.instance() or QApplication([])
    repository = FakeGuiRepository()
    view_model = MainViewModel(
        runtime_for(
            repository,
            FakeBrokerRepository(MqttConfig("broker", 1883, "", "")),
        ),
    )
    dialog = BrokerSettingsDialog(view_model)
    port_edit = dialog.findChild(QLineEdit, "brokerPortEdit")
    tls_checkbox = dialog.findChild(QCheckBox, "brokerUseTlsCheckbox")
    assert port_edit is not None
    assert tls_checkbox is not None

    tls_checkbox.setChecked(True)
    assert port_edit.text() == "8883"
    port_edit.setFocus()
    port_edit.selectAll()
    QTest.keyClicks(port_edit, "1884")
    tls_checkbox.setChecked(False)
    tls_checkbox.setChecked(True)
    assert port_edit.text() == "1884"

    dialog.close()
    application.processEvents()


async def test_applying_broker_settings_updates_the_view_model_and_closes_dialog() -> None:
    async def scenario() -> None:
        application = QApplication.instance() or QApplication([])
        repository = FakeGuiRepository()
        broker_repository = FakeBrokerRepository(MqttConfig("old", 1883, "", ""))
        view_model = MainViewModel(runtime_for(repository, broker_repository))
        window = MainWindow(view_model)
        window.findChild(QAction, "brokerSettingsAction").trigger()
        dialog = window.findChild(BrokerSettingsDialog, "brokerSettingsDialog")
        assert dialog is not None
        dialog.findChild(QLineEdit, "brokerHostEdit").setText("new-broker")
        dialog.findChild(QLineEdit, "brokerPortEdit").setText("8883")
        dialog.findChild(QCheckBox, "brokerUseTlsCheckbox").setChecked(True)

        dialog.findChild(QPushButton, "applyBrokerSettingsButton").click()
        await asyncio.sleep(0)

        expected = MqttConfig("new-broker", 8883, "", "", True)
        assert repository.broker_configurations == [expected]
        assert broker_repository.updated_mqtt == [expected]
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert not dialog.isVisible()
        window.close()
        application.processEvents()

    await scenario()


async def test_failed_broker_update_keeps_saved_offline_profile_editable() -> None:
    class FailingGuiRepository(FakeGuiRepository):
        async def update_broker(
            self,
            mqtt_config: MqttConfig,
            subscriptions: tuple[Subscription, ...] | None = None,
        ) -> None:
            raise ConnectionError("broker unavailable")

    async def scenario() -> None:
        application = QApplication.instance() or QApplication([])
        broker_repository = FakeBrokerRepository(MqttConfig("old", 1883, "", ""))
        view_model = MainViewModel(
            runtime_for(FailingGuiRepository(), broker_repository),
        )
        window = MainWindow(view_model)
        window.findChild(QAction, "brokerSettingsAction").trigger()
        dialog = window.findChild(BrokerSettingsDialog, "brokerSettingsDialog")
        assert dialog is not None

        with patch(
            "topicgate.gui.main_window.QMessageBox.warning"
        ) as warning:
            dialog.findChild(QPushButton, "applyBrokerSettingsButton").click()
            await asyncio.sleep(0)

        assert dialog.isVisible()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert broker_repository.updated_mqtt == [
            MqttConfig("old", 1883, "", "")
        ]
        warning.assert_called_once_with(
            window,
            "Broker selected but offline",
            "The broker profile was saved and selected, but TopicGate could not "
            "connect: broker unavailable",
        )
        window.close()
        application.processEvents()

    await scenario()
