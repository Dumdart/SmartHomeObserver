from topicgate.core.interfaces.diagnostic_pack import (
    DiagnosticPack,
    PackReference,
)


class DiagnosticPackRegistry:
    def __init__(self, packs: tuple[DiagnosticPack, ...] = ()) -> None:
        self._packs = {pack.reference: pack for pack in packs}

    def resolve(self, reference: PackReference) -> DiagnosticPack:
        try:
            return self._packs[reference]
        except KeyError as error:
            raise ValueError(
                "Diagnostic pack version is not installed: "
                f"{reference.pack_id}@{reference.version}."
            ) from error
