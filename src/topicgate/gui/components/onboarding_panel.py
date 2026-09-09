from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class OnboardingPanel(QFrame):
    """Guide a first-time desktop user through the safe setup sequence."""

    configure_broker_requested = Signal()
    test_connection_requested = Signal()
    add_subscription_requested = Signal()
    observe_requested = Signal()
    configure_mcp_requested = Signal()
    dismissed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("firstRunChecklist")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "#firstRunChecklist { background: #eff6ff; border: 1px solid #93c5fd; "
            "border-radius: 6px; color: #172554; }"
            "#firstRunChecklist QPushButton { color: #172554; background: #ffffff; "
            "border: 1px solid #60a5fa; border-radius: 4px; padding: 4px 8px; }"
            "#firstRunChecklist QPushButton:hover { background: #dbeafe; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        title_row = QHBoxLayout()
        title = QLabel("Get started with TopicGate")
        title.setObjectName("firstRunChecklistTitle")
        title.setStyleSheet("font-weight: 600; color: #172554;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        dismiss = QPushButton("Dismiss")
        dismiss.setObjectName("dismissFirstRunChecklistButton")
        dismiss.setAccessibleName("Dismiss first-run checklist")
        dismiss.clicked.connect(self.dismissed.emit)
        title_row.addWidget(dismiss)
        layout.addLayout(title_row)
        self._rows: dict[str, QLabel] = {}
        self._buttons: dict[str, QPushButton] = {}
        for key, text, button_text, signal in (
            ("broker", "Configure a broker profile", "Configure", self.configure_broker_requested),
            ("connection", "Test the broker connection", "Connect", self.test_connection_requested),
            ("subscription", "Add a subscription filter", "Add filter", self.add_subscription_requested),
            ("observe", "Observe values with a fresh snapshot", "Observe", self.observe_requested),
            ("mcp", "Configure the MCP integration", "MCP setup", self.configure_mcp_requested),
        ):
            row = QHBoxLayout()
            label = QLabel()
            label.setObjectName(f"firstRun{key.title()}Status")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet("color: #172554;")
            label.setProperty("checklistText", text)
            row.addWidget(label, 1)
            button = QPushButton(button_text)
            button.setObjectName(f"firstRun{key.title()}Button")
            button.setAccessibleName(text)
            button.clicked.connect(signal.emit)
            row.addWidget(button)
            layout.addLayout(row)
            self._rows[key] = label
            self._buttons[key] = button
        self.setAccessibleName("First-run checklist")

    def render(self, completed: dict[str, bool], busy: bool = False) -> None:
        for key, label in self._rows.items():
            done = completed.get(key, False)
            text = str(label.property("checklistText"))
            label.setText(("Done: " if done else "Next: ") + text)
            # Check the visible text is mirrored in accessibility state.
            label.setAccessibleName(label.text())
            self._buttons[key].setEnabled(not done and not busy)
        self.setVisible(not all(completed.values()))
