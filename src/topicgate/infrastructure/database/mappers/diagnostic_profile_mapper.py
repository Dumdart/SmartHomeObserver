from datetime import timezone

from topicgate.core.interfaces.diagnostic_pack import PackReference
from topicgate.core.models.diagnostic_profile import DiagnosticProfile
from topicgate.infrastructure.database.models.diagnostic_profile_row import DiagnosticProfileRow


class DiagnosticProfileMapper:
    @staticmethod
    def to_row(profile: DiagnosticProfile) -> DiagnosticProfileRow:
        reference = profile.pack_reference
        return DiagnosticProfileRow(
            profile_id=profile.profile_id,
            broker_id=profile.broker_id,
            name=profile.name,
            normalized_name=profile.normalized_name,
            description=profile.description,
            pack_id=None if reference is None else reference.pack_id,
            pack_version=None if reference is None else reference.version,
            rule_schema_version=profile.rule_schema_version,
            is_default=profile.is_default,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    @staticmethod
    def to_model(
        row: DiagnosticProfileRow,
        *,
        custom_rules=(),
        pack_overrides=None,
    ) -> DiagnosticProfile:
        reference = (
            None
            if row.pack_id is None
            else PackReference(row.pack_id, row.pack_version)
        )
        return DiagnosticProfile(
            profile_id=row.profile_id,
            broker_id=row.broker_id,
            name=row.name,
            description=row.description,
            pack_reference=reference,
            rule_schema_version=row.rule_schema_version,
            custom_rules=tuple(custom_rules),
            pack_overrides={} if pack_overrides is None else pack_overrides,
            is_default=row.is_default,
            created_at=(
                row.created_at.replace(tzinfo=timezone.utc)
                if row.created_at.tzinfo is None
                else row.created_at
            ),
            updated_at=(
                row.updated_at.replace(tzinfo=timezone.utc)
                if row.updated_at.tzinfo is None
                else row.updated_at
            ),
        )
