from importlib.metadata import PackageNotFoundError, version

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class AboutDialog(QDialog):
    """Describe the application, its data model, and project location."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aboutDialog")
        self.setWindowTitle("TopicGate")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        title = QLabel("TopicGate")
        title.setObjectName("aboutTitle")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel("Secure local access to your MQTT topics")
        subtitle.setObjectName("aboutSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 14px;")
        layout.addWidget(subtitle)

        version_label = QLabel(f"Version {self._application_version()}")
        version_label.setObjectName("aboutVersion")
        layout.addWidget(version_label)

        capabilities = QLabel(
            "Browse observed topics, inspect payload details, manage MQTT "
            "subscription filters, and maintain independent broker profiles."
        )
        capabilities.setObjectName("aboutCapabilities")
        capabilities.setWordWrap(True)
        layout.addWidget(capabilities)

        storage_card = QFrame()
        storage_card.setObjectName("aboutStorageCard")
        storage_card.setStyleSheet(
            "QFrame#aboutStorageCard {"
            "background: palette(alternate-base);"
            "border: 1px solid palette(mid);"
            "border-radius: 8px;"
            "}"
        )
        storage_layout = QVBoxLayout(storage_card)
        storage_layout.setContentsMargins(14, 12, 14, 12)
        storage_title = QLabel("Local by design")
        storage_title.setStyleSheet("font-weight: 700;")
        storage_text = QLabel(
            "Broker profiles, subscriptions, and each broker's latest "
            "observed MQTT values are stored in SQLite. "
            "Passwords are stored in your operating system's credential store. "
            "Snapshot views may include stored observations captured before "
            "the current connection or observation window."
        )
        storage_text.setObjectName("aboutStorageText")
        storage_text.setWordWrap(True)
        storage_layout.addWidget(storage_title)
        storage_layout.addWidget(storage_text)
        layout.addWidget(storage_card)

        project_link = QLabel(
            '<a href="https://github.com/Dumdart/TopicGate">'
            "github.com/Dumdart/TopicGate</a>"
        )
        project_link.setObjectName("aboutProjectLink")
        project_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        project_link.setOpenExternalLinks(True)
        layout.addWidget(project_link)

        license_label = QLabel("Released under the MIT License.")
        license_label.setObjectName("aboutLicense")
        layout.addWidget(license_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _application_version() -> str:
        try:
            return version("topicgate")
        except PackageNotFoundError:
            return "development"
