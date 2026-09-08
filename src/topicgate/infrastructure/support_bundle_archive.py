import os
from pathlib import Path
from tempfile import mkstemp
from zipfile import ZIP_DEFLATED, ZipFile

from topicgate.core.models.support_bundle import SupportBundleArtifacts


class SupportBundleArchiveWriter:
    """Package rendered support-bundle artifacts into an atomic ZIP file."""

    def write(
        self,
        destination: Path,
        artifacts: SupportBundleArtifacts,
    ) -> Path:
        target = destination.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("support-bundle.json", artifacts.json)
                archive.writestr("README.md", artifacts.markdown)
                archive.writestr("redaction-manifest.json", artifacts.manifest)
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return target
