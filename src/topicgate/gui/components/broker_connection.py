from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from topicgate.core.models.broker_summary import BrokerSummary
from topicgate.gui.components.workspace_pane import (
    WORKSPACE_CONTROL_HEIGHT,
    WorkspacePane,
)
from topicgate.gui.icons import delete_icon, edit_icon
from topicgate.gui.main_view_model import MainViewModel


class _BrokerProfileRow(QWidget):
    selected = Signal(object)
    edit_requested = Signal(object)
    delete_requested = Signal(object)

    def __init__(
        self,
        profile_id: object,
        name: str,
        active: bool,
        delete_enabled: bool,
    ) -> None:
        super().__init__()
        self.setObjectName("brokerProfilePopupRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(4)

        select_button = QToolButton()
        select_button.setObjectName("selectBrokerProfileButton")
        select_button.setText(name)
        select_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        select_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        select_button.setAccessibleName(f"Select broker profile {name}")
        if active:
            font = QFont(select_button.font())
            font.setBold(True)
            select_button.setFont(font)
            select_button.setAccessibleDescription("Active broker profile")
        select_button.clicked.connect(
            lambda: self.selected.emit(profile_id)
        )

        edit_button = QToolButton()
        edit_button.setObjectName("editBrokerProfileButton")
        edit_button.setIcon(edit_icon())
        edit_button.setIconSize(QSize(14, 14))
        edit_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        edit_button.setText("Edit")
        edit_button.setAccessibleName(f"Edit broker profile {name}")
        edit_button.clicked.connect(
            lambda: self.edit_requested.emit(profile_id)
        )

        delete_button = QToolButton()
        delete_button.setObjectName("deleteBrokerProfileButton")
        delete_button.setIcon(delete_icon())
        delete_button.setIconSize(QSize(14, 14))
        delete_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        delete_button.setText("Delete")
        delete_button.setProperty("danger", True)
        delete_button.setAccessibleName(f"Delete broker profile {name}")
        delete_button.setEnabled(delete_enabled)
        delete_button.clicked.connect(
            lambda: self.delete_requested.emit(profile_id)
        )

        row.addWidget(select_button, 1)
        row.addWidget(edit_button)
        row.addWidget(delete_button)


class BrokerProfileSelector(QComboBox):
    edit_requested = Signal(object)
    delete_requested = Signal(object)
    add_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._profiles: tuple[BrokerSummary, ...] = ()
        self._management_enabled = True
        self._popup_menu = QMenu(self)
        self._popup_menu.setObjectName("brokerProfileSelectorMenu")
        self._popup_menu.aboutToHide.connect(self._reset_popup_state)

    def render_profiles(
        self,
        profiles: tuple[BrokerSummary, ...],
        active_profile_id: object,
        management_enabled: bool,
    ) -> None:
        self._profiles = profiles
        self._management_enabled = management_enabled
        signals_were_blocked = self.blockSignals(True)
        self.clear()
        for profile in profiles:
            self.addItem(profile.name, profile.id)
        active_index = next(
            (
                index
                for index, profile in enumerate(profiles)
                if profile.id == active_profile_id
            ),
            0 if profiles else -1,
        )
        self.setCurrentIndex(active_index)
        self.blockSignals(signals_were_blocked)
        self._rebuild_popup(active_profile_id)

    def showPopup(self) -> None:
        if not self.isEnabled():
            return
        self._popup_menu.setMinimumWidth(max(self.width(), 320))
        popup_position = self.mapToGlobal(QPoint(0, self.height()))
        self._popup_menu.popup(popup_position)

    def hidePopup(self) -> None:
        self._popup_menu.hide()
        QComboBox.hidePopup(self)

    def _rebuild_popup(self, active_profile_id: object) -> None:
        self._popup_menu.clear()
        delete_enabled = self._management_enabled and len(self._profiles) > 1
        for profile in self._profiles:
            row = _BrokerProfileRow(
                profile.id,
                profile.name,
                profile.id == active_profile_id,
                delete_enabled,
            )
            row.setEnabled(self._management_enabled)
            row.selected.connect(self._select_profile)
            row.edit_requested.connect(self._request_edit)
            row.delete_requested.connect(self._request_delete)
            action = QWidgetAction(self._popup_menu)
            action.setObjectName("brokerProfilePopupRowAction")
            action.setDefaultWidget(row)
            self._popup_menu.addAction(action)

        self._popup_menu.addSeparator()
        add_action = QAction("+ Add Broker", self._popup_menu)
        add_action.setObjectName("addBrokerProfilePaneAction")
        add_action.setEnabled(self._management_enabled)
        add_action.triggered.connect(self._request_add)
        self._popup_menu.addAction(add_action)

    def _select_profile(self, profile_id: object) -> None:
        self.hidePopup()
        index = self.findData(profile_id)
        if index >= 0:
            self.setCurrentIndex(index)

    def _request_edit(self, profile_id: object) -> None:
        self.hidePopup()
        self.edit_requested.emit(profile_id)

    def _request_delete(self, profile_id: object) -> None:
        self.hidePopup()
        self.delete_requested.emit(profile_id)

    def _request_add(self) -> None:
        self.hidePopup()
        self.add_requested.emit()

    def _reset_popup_state(self) -> None:
        QComboBox.hidePopup(self)


class BrokerConnectionPane(WorkspacePane):
    """Keep broker selection and connection actions above the observer tree."""

    broker_selected = Signal(object)
    edit_profile_requested = Signal(object)
    add_profile_requested = Signal()
    delete_profile_requested = Signal(object)
    connect_requested = Signal()
    reconnect_requested = Signal()
    disconnect_requested = Signal()
    health_requested = Signal()

    _STATUS_LABELS = {
        "connected": "Connected",
        "connecting": "Connecting…",
        "reconnecting": "Reconnecting…",
        "disconnected": "Disconnected",
    }

    def __init__(self) -> None:
        super().__init__("Broker", minimum_hint_width=320)
        self.setObjectName("brokerConnectionPane")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setMaximumHeight(112)
        self._status = "disconnected"

        self._status_badge = QLabel("Disconnected")
        self._status_badge.setObjectName("brokerConnectionStatus")
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_badge.setAccessibleName("MQTT connection status")
        self.heading.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Preferred,
        )
        self.header_layout.setStretch(0, 0)
        self.header_layout.addWidget(self._status_badge)
        self.header_layout.addStretch(1)
        self._manage_button = QToolButton()
        self._manage_button.setText("Profiles...")
        self._manage_button.setAccessibleName("Manage broker profiles")
        self._manage_button.setFixedHeight(WORKSPACE_CONTROL_HEIGHT)
        self._manage_button.setObjectName("manageBrokersButton")
        self._manage_button.clicked.connect(lambda: self._profile_selector.showPopup())

        broker_grid = QGridLayout()
        broker_grid.setSpacing(8)
        broker_grid.setColumnStretch(0, 1)
        broker_grid.setColumnStretch(1, 0)
        self._profile_selector = BrokerProfileSelector()
        self._profile_selector.setObjectName("connectionBrokerSelector")
        self._profile_selector.setFixedHeight(WORKSPACE_CONTROL_HEIGHT)
        self._profile_selector.setAccessibleName("Active broker profile")
        self._profile_selector.setMinimumWidth(160)
        self._profile_selector.currentIndexChanged.connect(
            self._select_profile
        )
        self._profile_selector.edit_requested.connect(
            self.edit_profile_requested.emit
        )
        self._profile_selector.delete_requested.connect(
            self.delete_profile_requested.emit
        )
        self._profile_selector.add_requested.connect(
            self.add_profile_requested.emit
        )

        self._lifecycle_button = QPushButton("Connect")
        self._lifecycle_button.setObjectName("brokerLifecycleButton")
        self._lifecycle_button.setFixedHeight(WORKSPACE_CONTROL_HEIGHT)
        self._lifecycle_button.setProperty("primary", True)
        self._lifecycle_button.clicked.connect(self._request_lifecycle_operation)

        self._health_button = QToolButton()
        self._health_button.setText("Health: Not evaluated")
        self._health_button.setAutoRaise(True)
        self._health_button.setObjectName("brokerHealthSummary")
        self._health_button.setFixedHeight(WORKSPACE_CONTROL_HEIGHT)
        self._health_button.setAccessibleName("Inspect broker health")
        self._health_button.clicked.connect(self.health_requested.emit)
        broker_grid.addWidget(self._profile_selector, 0, 0)
        broker_grid.addWidget(self._manage_button, 0, 1)
        broker_grid.addWidget(self._lifecycle_button, 1, 1)
        broker_grid.addWidget(self._health_button, 1, 0)
        self.content_layout.addLayout(broker_grid)
        self.setMaximumHeight(152)

    def render(self, view_model: MainViewModel, busy: bool = False) -> None:
        profiles = view_model.broker_profiles
        active = view_model.active_broker_profile
        self._status = view_model.connection_status.lower()
        status_label = self._STATUS_LABELS.get(
            self._status,
            self._status.replace("_", " ").title(),
        )

        self._status_badge.setText(status_label)
        self._status_badge.setProperty("connectionState", self._status)
        self._status_badge.style().unpolish(self._status_badge)
        self._status_badge.style().polish(self._status_badge)

        switching = self._status in {"connecting", "reconnecting"}
        management_enabled = not busy and not switching
        self._profile_selector.render_profiles(
            profiles,
            active.id,
            management_enabled,
        )
        self._profile_selector.setEnabled(management_enabled)
        self._manage_button.setEnabled(management_enabled)
        lifecycle_text, lifecycle_enabled = self._lifecycle_presentation(busy)
        self._lifecycle_button.setText(lifecycle_text)
        self._lifecycle_button.setEnabled(lifecycle_enabled)
        health = view_model.health_summary
        counts = self._compact_health_counts(health.label, health.counts)
        suffix = f" · {counts}" if counts else ""
        self._health_button.setText(f"Health: {health.label}{suffix}")
        self._health_button.setToolTip(health.explanation)
        self._health_button.setProperty("healthTone", health.tone)
        self._health_button.style().unpolish(self._health_button)
        self._health_button.style().polish(self._health_button)

    @staticmethod
    def _compact_health_counts(label: str, counts: str) -> str:
        parts = counts.split(" · ") if counts else []
        compact = []
        for part in parts:
            if part.startswith(f"{label} "):
                compact.append(part.removeprefix(f"{label} "))
            elif part.endswith(" not shown"):
                compact.append(part.removesuffix(" not shown"))
            else:
                compact.append(part)
        return " · ".join(compact)

    def _select_profile(self, index: int) -> None:
        if index < 0:
            return
        profile_id = self._profile_selector.itemData(index)
        if profile_id is not None:
            self.broker_selected.emit(profile_id)

    def _request_lifecycle_operation(self) -> None:
        if self._status == "disconnected":
            self.connect_requested.emit()
        elif self._status == "connected":
            self.disconnect_requested.emit()

    def _lifecycle_presentation(self, busy: bool) -> tuple[str, bool]:
        if self._status == "connecting":
            return "Connecting…", False
        if self._status == "reconnecting":
            return "Reconnecting…", False
        if self._status == "connected":
            return "Disconnect", not busy
        return "Connect", not busy
