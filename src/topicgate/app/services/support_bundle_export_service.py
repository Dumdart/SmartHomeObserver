from topicgate.app.services.support_bundle_service import SupportBundleService
from topicgate.core.models.support_bundle import (
    SupportBundleArtifacts,
    SupportBundleOptions,
)
from topicgate.presentation.support_bundle_representation import (
    SupportBundleRepresentation,
)


class SupportBundleExporter:
    """Export a safe bundle to in-memory artifacts for any delivery adapter."""

    def __init__(
        self,
        support_bundle_service: SupportBundleService,
        support_bundle_representation: SupportBundleRepresentation | None = None,
    ) -> None:
        self._service = support_bundle_service
        self._representation = (
            support_bundle_representation or SupportBundleRepresentation()
        )

    def export(
        self,
        options: SupportBundleOptions | None = None,
    ) -> SupportBundleArtifacts:
        bundle = self._service.generate_support_bundle(options)
        manifest = self._service.redaction_manifest(bundle)
        return self._representation.build_detailed_presentation(bundle, manifest)

    def export_support_bundle(
        self,
        options: SupportBundleOptions | None = None,
    ) -> SupportBundleArtifacts:
        return self.export(options)

    def preview_redaction_manifest(
        self,
        options: SupportBundleOptions | None = None,
    ) -> str:
        manifest = self._service.redaction_manifest_preview(options)
        return self._representation.build_presentation_manifest(manifest)


SupportBundleExportService = SupportBundleExporter
