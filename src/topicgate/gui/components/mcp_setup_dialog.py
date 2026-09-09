from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from topicgate.gui.main_view_model import MainViewModel


class McpSetupDialog(QDialog):
    """Configure MCP clients and diagnose the shared TopicGate installation."""

    reconnect_observe_requested = Signal()

    def __init__(
        self,
        view_model: MainViewModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self.setObjectName("mcpSetupDialog")
        self.setWindowTitle("Configure TopicGate MCP")
        self.resize(760, 700)
        layout = QVBoxLayout(self)

        introduction = QLabel(
            "TopicGate Desktop is the setup and maintenance control center. "
            "The MCP process is normally started by your host and shares this "
            "database with the desktop. Read-only mode is recommended."
        )
        introduction.setWordWrap(True)
        introduction.setAccessibleName("MCP setup introduction")
        layout.addWidget(introduction)

        information = self._view_model.mcp_setup_information
        installation = QGroupBox("Installation")
        install_form = QFormLayout(installation)
        self._version = QLabel(information.version if information else "Development")
        self._version.setObjectName("mcpTopicGateVersion")
        executable = (
            str(information.executable_path)
            if information
            else "Current Python environment"
        )
        self._executable = QLabel(executable)
        self._executable.setObjectName("mcpExecutablePath")
        install_form.addRow("TopicGate version", self._version)
        install_form.addRow(
            "Resolved executable",
            self._path_row(self._executable, "copyMcpExecutablePath"),
        )
        if information is not None:
            self._data_path = QLabel(str(information.data_path))
            self._data_path.setObjectName("mcpDataPath")
            self._database_path = QLabel(str(information.database_path))
            self._database_path.setObjectName("mcpDatabasePath")
            install_form.addRow(
                "Application data",
                self._path_row(self._data_path, "copyMcpDataPath"),
            )
            install_form.addRow(
                "SQLite database",
                self._path_row(self._database_path, "copyMcpDatabasePath"),
            )
        layout.addWidget(installation)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Configuration mode"))
        self._mode = QComboBox()
        self._mode.setObjectName("mcpConfigurationMode")
        self._mode.setAccessibleName("MCP configuration mode")
        self._mode.addItems(("Read-only (recommended)", "Control"))
        self._mode.currentIndexChanged.connect(self._render_configuration)
        mode_row.addWidget(self._mode, 1)
        layout.addLayout(mode_row)

        self._mode_warning = QLabel()
        self._mode_warning.setObjectName("mcpModeWarning")
        self._mode_warning.setWordWrap(True)
        self._mode_warning.setAccessibleName("MCP mode capabilities and warning")
        layout.addWidget(self._mode_warning)

        self._configuration = QPlainTextEdit()
        self._configuration.setObjectName("mcpSetupConfiguration")
        self._configuration.setReadOnly(True)
        self._configuration.setAccessibleName("TopicGate MCP configuration")
        layout.addWidget(self._configuration, 1)
        copy_button = QPushButton("Copy configuration")
        copy_button.setObjectName("copyMcpConfigurationButton")
        copy_button.setAccessibleName("Copy TopicGate MCP configuration")
        copy_button.clicked.connect(self._copy_configuration)
        layout.addWidget(copy_button)

        trust = QLabel(
            "Security: broker names, topic names, and MQTT payloads are untrusted "
            "data. MCP clients must never treat their contents as instructions."
        )
        trust.setObjectName("mcpUntrustedDataWarning")
        trust.setWordWrap(True)
        trust.setAccessibleName("Untrusted MQTT data warning")
        layout.addWidget(trust)

        diagnostics = QHBoxLayout()
        preflight = QPushButton("Run local preflight")
        preflight.setObjectName("runMcpPreflightButton")
        preflight.setAccessibleName("Run local MCP preflight checks")
        preflight.clicked.connect(self._run_preflight)
        diagnostics.addWidget(preflight)
        snapshot = QPushButton("Test broker snapshot")
        snapshot.setObjectName("testMcpSnapshotButton")
        snapshot.setAccessibleName("Test broker snapshot without reconnecting")
        snapshot.clicked.connect(self._test_snapshot)
        diagnostics.addWidget(snapshot)
        reconnect = QPushButton("Test reconnect & observe…")
        reconnect.setObjectName("testMcpReconnectButton")
        reconnect.setAccessibleName("Test reconnect and observe with confirmation")
        reconnect.clicked.connect(self.reconnect_observe_requested.emit)
        diagnostics.addWidget(reconnect)
        layout.addLayout(diagnostics)

        self._diagnostic_result = QLabel("Run preflight to check this installation.")
        self._diagnostic_result.setObjectName("mcpPreflightResults")
        self._diagnostic_result.setWordWrap(True)
        self._diagnostic_result.setAccessibleName("MCP preflight results")
        layout.addWidget(self._diagnostic_result)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)
        self._render_configuration()

    def _path_row(self, label: QLabel, button_name: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label, 1)
        copy = QPushButton("Copy")
        copy.setObjectName(button_name)
        copy.setAccessibleName(f"Copy {label.objectName()}")
        copy.clicked.connect(
            lambda _checked=False, value=label.text(): (
                QGuiApplication.clipboard().setText(value)
            )
        )
        layout.addWidget(copy)
        return row

    def _render_configuration(self) -> None:
        control = self._mode.currentIndex() == 1
        mode = "control" if control else "read-only"
        self._configuration.setPlainText(self._view_model.mcp_configuration(mode))
        if control:
            self._mode_warning.setText(
                "Control mode exposes connection changes, broker activation, "
                "subscription changes, observation refresh, and publishing. "
                "It uses the shared control lease and can be blocked by desktop work."
            )
            self._mode_warning.setStyleSheet("color: #991b1b; font-weight: 600;")
        else:
            self._mode_warning.setText(
                "Read-only exposes broker, connection, subscription, topic, and "
                "snapshot inspection. It does not reconnect, subscribe, or publish."
            )
            self._mode_warning.setStyleSheet("")

    def _run_preflight(self) -> None:
        checks = self._view_model.run_mcp_preflight()
        self._diagnostic_result.setText(
            "\n".join(
                f"{check.status.upper()}: {check.name} — {check.detail}"
                for check in checks
            )
        )

    def _test_snapshot(self) -> None:
        try:
            health = self._view_model.test_broker_snapshot()
        except Exception as error:
            self._diagnostic_result.setText(f"FAIL: Broker snapshot — {error}")
            return
        self._diagnostic_result.setText(
            "PASS: Broker snapshot — "
            f"{health.returned_count} returned; {health.completeness_status}."
        )

    def _copy_configuration(self) -> None:
        QGuiApplication.clipboard().setText(self._configuration.toPlainText())
