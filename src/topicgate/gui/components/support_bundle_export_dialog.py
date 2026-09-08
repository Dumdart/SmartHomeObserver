import json
from datetime import datetime

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from topicgate.gui.main_view_model import MainViewModel


def suggested_support_bundle_filename(now: datetime | None = None) -> str:
    timestamp = now or datetime.now()
    return f"topicgate-support-{timestamp:%Y%m%d-%H%M%S}.zip"


class SupportBundleExportDialog(QDialog):
    """Explain and confirm a local redacted support-bundle export."""

    def __init__(
        self,
        view_model: MainViewModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self.setObjectName("supportBundleExportDialog")
        self.setWindowTitle("Export support bundle")
        self.resize(700, 650)
        layout = QVBoxLayout(self)

        introduction = QLabel(
            "The support bundle contains bounded TopicGate version and platform "
            "details, redacted broker and topic aliases, subscriptions, current "
            "state metadata, health findings, collection limits, and warnings."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        exclusions = QGroupBox("Always excluded")
        exclusions_layout = QVBoxLayout(exclusions)
        exclusions_text = QLabel(
            "Credentials, passwords, connection secrets, credential-store "
            "identifiers, broker names and IDs, hosts, usernames, and local "
            "filesystem/configuration paths are structurally excluded and cannot "
            "be exported. Topic names are replaced with per-bundle aliases."
        )
        exclusions_text.setObjectName("supportBundleStructuralExclusions")
        exclusions_text.setWordWrap(True)
        exclusions_layout.addWidget(exclusions_text)
        layout.addWidget(exclusions)

        self._include_payloads = QCheckBox("Include MQTT payloads")
        self._include_payloads.setObjectName("includeSupportBundlePayloads")
        self._include_payloads.setChecked(False)
        self._include_payloads.setToolTip(
            "Payloads may contain personal data, tokens, device identifiers, or "
            "other secrets. A second confirmation is required."
        )
        self._include_payloads.toggled.connect(self._refresh_manifest)
        layout.addWidget(self._include_payloads)

        payload_warning = QLabel(
            "Payloads are excluded by default. If included, they are bounded but "
            "their content is not made harmless; review the archive before sharing. "
            "Metadata such as timestamps, ports, TLS use, status, and counts can "
            "still reveal details about your environment."
        )
        payload_warning.setObjectName("supportBundlePrivacyWarning")
        payload_warning.setWordWrap(True)
        layout.addWidget(payload_warning)

        manifest_label = QLabel(
            "Redaction manifest preview (occurrence counts are calculated during "
            "collection):"
        )
        layout.addWidget(manifest_label)
        self._manifest = QPlainTextEdit()
        self._manifest.setObjectName("supportBundleManifestPreview")
        self._manifest.setReadOnly(True)
        self._manifest.setAccessibleName(
            "Support bundle redaction manifest preview"
        )
        layout.addWidget(self._manifest, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Choose destination..."
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_manifest()

    @property
    def include_payloads(self) -> bool:
        return self._include_payloads.isChecked()

    def _refresh_manifest(self) -> None:
        preview = self._view_model.support_bundle_manifest_preview(
            include_payloads=self.include_payloads
        )
        self._manifest.setPlainText(
            json.dumps(json.loads(preview), indent=2, ensure_ascii=False)
        )
