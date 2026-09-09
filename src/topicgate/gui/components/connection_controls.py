from collections.abc import Callable
from uuid import UUID

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSizePolicy, QToolButton

from topicgate.core.models.broker_summary import BrokerSummary


class ConnectionControls(QObject):
    """Keep global broker selection and connection actions synchronized."""

    broker_selected = Signal(object)
    edit_profile_requested = Signal(object)
    connect_requested = Signal()
    reconnect_requested = Signal()
    disconnect_requested = Signal()
    details_requested = Signal()

    _STATUS_LABELS = {
        "connected": "Connected",
        "connecting": "Connecting…",
        "reconnecting": "Reconnecting…",
        "disconnected": "Disconnected",
    }
    _STATUS_COLORS = {
        "connected": "#168a55",
        "connecting": "#b66a00",
        "reconnecting": "#b66a00",
        "disconnected": "#737b85",
    }

    def __init__(
        self,
        add_profile_action: QAction,
        edit_profile_action: QAction,
        delete_profile_action: QAction,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._status = "disconnected"
        self._profile_actions: dict[UUID, QAction] = {}
        self._edit_profile_actions: list[QAction] = []
        self._add_profile_action = add_profile_action
        self._edit_profile_action = edit_profile_action
        self._delete_profile_action = delete_profile_action

        self.lifecycle_action = self._create_action(
            "&Connect",
            "connectionLifecycleAction",
            "Connect to the active MQTT broker",
            self._request_lifecycle_operation,
        )
        self.disconnect_action = self._create_action(
            "&Disconnect",
            "disconnectAction",
            "Disconnect from the active MQTT broker",
            self.disconnect_requested.emit,
        )
        self.endpoint_action = QAction(self)
        self.endpoint_action.setObjectName("activeBrokerEndpointAction")
        self.endpoint_action.setEnabled(False)

        self.button = QToolButton()
        self.button.setObjectName("brokerConnectionButton")
        self.button.setAutoRaise(True)
        self.button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.button.setIconSize(QSize(8, 8))
        self.button.setMaximumWidth(320)
        self.button.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self.edit_menu = QMenu("&Edit broker profile...", self.button)
        self.edit_menu.setObjectName("editBrokerProfileMenu")
        self.menu = QMenu(self.button)
        self.menu.setObjectName("brokerConnectionMenu")
        self.button.setMenu(self.menu)
        self.button.clicked.connect(self.details_requested)
        self._populate_corner_menu()

    @property
    def actions(self) -> tuple[QAction, QAction]:
        return (self.lifecycle_action, self.disconnect_action)

    @property
    def profile_actions(self) -> tuple[QAction, ...]:
        return tuple(self._profile_actions.values())

    def render(
        self,
        profiles: tuple[BrokerSummary, ...],
        active: BrokerSummary,
        status: str,
        busy: bool = False,
    ) -> None:
        self._status = status.lower()
        self._sync_profile_actions(profiles)
        self._sync_edit_actions(profiles, active)

        endpoint = self._endpoint(active)
        state_label = self._STATUS_LABELS.get(
            self._status,
            self._status.replace("_", " ").title(),
        )
        broker_name = self.button.fontMetrics().elidedText(
            active.name,
            Qt.TextElideMode.ElideRight,
            170,
        )
        self.button.setText(f"{broker_name} · {state_label}")
        self.button.setIcon(
            self._status_icon(
                self._STATUS_COLORS.get(self._status, "#737b85")
            )
        )
        self.button.setAccessibleName(
            f"{active.name}, MQTT connection {state_label}"
        )
        description = self._connection_description(endpoint)
        self.button.setAccessibleDescription(description)
        self.button.setToolTip(description)
        self.endpoint_action.setText(f"Active endpoint: {endpoint}")
        self.endpoint_action.setToolTip(endpoint)

        lifecycle_text, lifecycle_enabled = self._lifecycle_presentation(busy)
        self.lifecycle_action.setText(lifecycle_text)
        self.lifecycle_action.setEnabled(lifecycle_enabled)
        self.disconnect_action.setEnabled(
            self._status in {"connecting", "connected", "reconnecting"}
            and not busy
        )
        broker_actions_enabled = (
            not busy
            and self._status not in {"connecting", "reconnecting"}
        )
        for profile_id, action in self._profile_actions.items():
            action.setChecked(profile_id == active.id)
            action.setEnabled(
                profile_id != active.id and broker_actions_enabled
            )
        self._add_profile_action.setEnabled(broker_actions_enabled)
        self.edit_menu.setEnabled(broker_actions_enabled)
        for action in self._edit_profile_actions:
            action.setEnabled(broker_actions_enabled)
        self._edit_profile_action.setEnabled(broker_actions_enabled)
        self._delete_profile_action.setEnabled(
            len(profiles) > 1 and broker_actions_enabled
        )

    def _populate_corner_menu(self) -> None:
        self.menu.clear()
        self.menu.addAction(self.endpoint_action)
        self.menu.addSeparator()
        self.menu.addActions(self.profile_actions)
        self.menu.addSeparator()
        self.menu.addAction(self._add_profile_action)
        self.menu.addMenu(self.edit_menu)
        self.menu.addAction(self._delete_profile_action)
        self.menu.addSeparator()
        self.menu.addAction(self.lifecycle_action)
        self.menu.addAction(self.disconnect_action)

    def _sync_profile_actions(
        self,
        profiles: tuple[BrokerSummary, ...],
    ) -> None:
        signature = tuple((profile.id, profile.name) for profile in profiles)
        current = tuple(
            (profile_id, action.text())
            for profile_id, action in self._profile_actions.items()
        )
        if signature == current:
            return

        for action in self._profile_actions.values():
            action.deleteLater()
        self._profile_actions.clear()
        for profile in profiles:
            action = QAction(profile.name, self)
            action.setObjectName("brokerProfileAction")
            action.setCheckable(True)
            action.setData(profile.id)
            action.setToolTip(f"Switch to broker profile {profile.name}")
            action.triggered.connect(
                lambda _checked=False, profile_id=profile.id: (
                    self.broker_selected.emit(profile_id)
                )
            )
            self._profile_actions[profile.id] = action
        self._populate_corner_menu()

    def _sync_edit_actions(
        self,
        profiles: tuple[BrokerSummary, ...],
        active: BrokerSummary,
    ) -> None:
        self.edit_menu.clear()
        for action in self._edit_profile_actions:
            action.deleteLater()
        self._edit_profile_actions.clear()
        for profile in profiles:
            if profile.id == active.id:
                self._edit_profile_action.setText(profile.name)
                self.edit_menu.addAction(self._edit_profile_action)
                continue
            action = QAction(profile.name, self)
            action.setObjectName("editBrokerProfileAction")
            action.triggered.connect(
                lambda _checked=False, profile_id=profile.id: (
                    self.edit_profile_requested.emit(profile_id)
                )
            )
            self._edit_profile_actions.append(action)
            self.edit_menu.addAction(action)

    def _request_lifecycle_operation(self) -> None:
        if self._status == "disconnected":
            self.connect_requested.emit()
        elif self._status == "connected":
            self.reconnect_requested.emit()

    def _lifecycle_presentation(self, busy: bool) -> tuple[str, bool]:
        if self._status == "connecting":
            return "Connecting…", False
        if self._status == "reconnecting":
            return "Reconnecting…", False
        if self._status == "connected":
            return "&Reconnect", not busy
        return "&Connect", not busy

    def _connection_description(self, endpoint: str) -> str:
        if self._status == "connected":
            return (
                f"Active endpoint {endpoint}. Reconnect renews the connection "
                "and refreshes observations."
            )
        if self._status in {"connecting", "reconnecting"}:
            return (
                f"Active endpoint {endpoint}. The connection operation is in "
                "progress."
            )
        return (
            f"Active endpoint {endpoint}. Open Connection for broker details, "
            "or use the arrow menu for quick actions."
        )

    @staticmethod
    def _endpoint(active: BrokerSummary) -> str:
        scheme = "mqtts" if active.config.use_tls else "mqtt"
        return f"{scheme}://{active.config.host}:{active.config.port}"

    @staticmethod
    def _status_icon(color: str) -> QIcon:
        pixmap = QPixmap(8, 8)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(0, 0, 8, 8)
        painter.end()
        return QIcon(pixmap)

    def _create_action(
        self,
        text: str,
        object_name: str,
        tooltip: str,
        requested: Callable[[], None],
    ) -> QAction:
        action = QAction(text, self)
        action.setObjectName(object_name)
        action.setToolTip(tooltip)
        action.setStatusTip(tooltip)
        action.triggered.connect(lambda _checked=False: requested())
        return action
