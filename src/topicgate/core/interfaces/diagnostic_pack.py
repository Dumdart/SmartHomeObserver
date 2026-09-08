from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from topicgate.core.models.health import HealthExpectation


@dataclass(frozen=True)
class PackReference:
    pack_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.pack_id.strip():
            raise ValueError("Diagnostic pack ID must not be blank.")
        if not self.version.strip():
            raise ValueError("Diagnostic pack version must not be blank.")


@runtime_checkable
class DiagnosticPack(Protocol):
    @property
    def reference(self) -> PackReference: ...

    def build_expectations(
        self,
        broker_id: UUID,
        profile_id: UUID | None = None,
    ) -> tuple[HealthExpectation, ...]: ...


@runtime_checkable
class DiagnosticPackResolver(Protocol):
    def resolve(self, reference: PackReference) -> DiagnosticPack: ...
