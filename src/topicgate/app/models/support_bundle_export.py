from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SupportBundleExportResult:
    destination: Path
    warnings: tuple[str, ...]
