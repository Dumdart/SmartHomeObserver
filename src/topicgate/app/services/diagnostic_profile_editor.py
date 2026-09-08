from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from topicgate.app.services.diagnostic_profile_preview import DiagnosticPreviewFinding, DiagnosticProfilePreview
from topicgate.app.services.diagnostic_profile_service import DiagnosticProfileService, ProfilePreparation
from topicgate.core.interfaces import PackReference
from topicgate.core.models.diagnostic_profile import DiagnosticProfile
from topicgate.core.models.health import HealthExpectation


@dataclass(frozen=True)
class DraftValidation:
    errors: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors


class DiagnosticProfileEditor:
    """UI-independent draft controller. Only ``apply`` writes a profile."""

    def __init__(self, profiles: DiagnosticProfileService, health_evaluator) -> None:
        self._profiles = profiles
        self._health_evaluator = health_evaluator
        self._broker_id: UUID | None = None
        self._baseline: DiagnosticProfile | None = None
        self._draft: DiagnosticProfile | None = None
        self._is_new = False
        self.validation = DraftValidation()
        self.preview_result: DiagnosticProfilePreview | None = None

    @property
    def draft(self) -> DiagnosticProfile | None:
        return self._draft

    @property
    def baseline(self) -> DiagnosticProfile | None:
        return self._baseline

    @property
    def is_dirty(self) -> bool:
        return self._draft is not None and (self._is_new or self._draft != self._baseline)

    @property
    def profiles(self) -> tuple[DiagnosticProfile, ...]:
        return () if self._broker_id is None else self._profiles.list_profiles(self._broker_id)

    @property
    def pack_references(self) -> tuple[PackReference, ...]:
        return self._profiles.list_pack_references()

    def open_broker(self, broker_id: UUID) -> DiagnosticProfile | None:
        available = self._profiles.list_profiles(broker_id)
        if not available:
            self._broker_id = broker_id
            self._baseline = self._draft = None
            return None
        return self.select(broker_id, available[0].profile_id)

    def validate(self) -> ProfilePreparation:
        return self._refresh_validation()

    def select(self, broker_id: UUID, profile_id: UUID) -> DiagnosticProfile:
        profile = self._profiles.get_profile(broker_id, profile_id)
        self._broker_id = broker_id
        self._baseline = profile
        self._draft = _copy_profile(profile)
        self._is_new = False
        self._refresh_validation()
        return self._draft

    def create(self, broker_id: UUID, name: str) -> DiagnosticProfile:
        now = datetime.now().astimezone()
        self._broker_id = broker_id
        self._baseline = None
        self._draft = DiagnosticProfile(uuid4(), broker_id, name, created_at=now, updated_at=now)
        self._is_new = True
        self._refresh_validation()
        return self._draft

    def copy(self, name: str) -> DiagnosticProfile:
        draft = self._require_draft()
        now = datetime.now().astimezone()
        self._baseline = None
        self._draft = replace(_copy_profile(draft), profile_id=uuid4(), name=name, created_at=now, updated_at=now)
        self._is_new = True
        self._refresh_validation()
        return self._draft

    def replace_draft(self, draft: DiagnosticProfile) -> DiagnosticProfile:
        if self._broker_id is not None and draft.broker_id != self._broker_id:
            raise ValueError("The diagnostic profile does not belong to this broker.")
        self._broker_id = draft.broker_id
        self._draft = _copy_profile(draft)
        self._refresh_validation()
        return self._draft

    def set_pack(self, reference: PackReference | None) -> DiagnosticProfile:
        draft = self._require_draft()
        # Rebuilding from the selected pack deliberately drops overrides for rules
        # that do not exist in the replacement version.
        return self.replace_draft(replace(draft, pack_reference=reference, pack_overrides={}))

    def set_custom_rules(self, rules: tuple[HealthExpectation, ...]) -> DiagnosticProfile:
        return self.replace_draft(replace(self._require_draft(), custom_rules=rules))

    def set_pack_override(self, rule_id: str, override: dict[str, Any]) -> DiagnosticProfile:
        draft = self._require_draft()
        overrides = {key: dict(value) for key, value in draft.pack_overrides.items()}
        overrides[rule_id] = dict(override)
        return self.replace_draft(replace(draft, pack_overrides=overrides))

    def reset_pack_override(self, rule_id: str) -> DiagnosticProfile:
        draft = self._require_draft()
        overrides = {key: dict(value) for key, value in draft.pack_overrides.items()}
        overrides.pop(rule_id, None)
        return self.replace_draft(replace(draft, pack_overrides=overrides))

    def stage_suggestion(self, profile: DiagnosticProfile | None = None, *, rules: tuple[HealthExpectation, ...] | None = None) -> DiagnosticProfile:
        """Stage externally supplied advice; it never persists on its own."""
        candidate = profile if profile is not None else replace(self._require_draft(), custom_rules=rules or ())
        return self.replace_draft(candidate)

    def preview(self, *, evaluated_at: datetime | None = None) -> DiagnosticProfilePreview | None:
        draft = self._require_draft()
        prepared = self._refresh_validation()
        if not prepared.is_valid:
            self.preview_result = None
            return None
        report = self._health_evaluator.preview_broker(
            draft.broker_id, prepared.expectations, evaluated_at=evaluated_at
        )
        by_id = {item.expectation_id: item for item in prepared.expectations}
        self.preview_result = DiagnosticProfilePreview(
            report,
            tuple(
                DiagnosticPreviewFinding(by_id[item.expectation_id], item)
                for item in report.topic_findings
                if item.expectation_id in by_id
            ),
        )
        return self.preview_result

    def apply(self) -> DiagnosticProfile:
        draft = self._require_draft()
        prepared = self._refresh_validation()
        if not prepared.is_valid or prepared.profile is None:
            raise ValueError("\n".join(self.validation.errors))
        saved = self._profiles.create(prepared.profile) if self._is_new else self._profiles.update_profile(draft.broker_id, prepared.profile)
        self._baseline = saved
        self._draft = _copy_profile(saved)
        self._is_new = False
        self._refresh_validation()
        return saved

    def revert(self) -> None:
        if self._baseline is None:
            self._draft = None
            self._is_new = False
            self.validation = DraftValidation()
            self.preview_result = None
            return
        self._draft = _copy_profile(self._baseline)
        self._refresh_validation()

    def delete(self) -> None:
        draft = self._require_draft()
        if self._is_new:
            self.revert()
            return
        self._profiles.delete_profile(draft.broker_id, draft.profile_id)
        self._baseline = None
        self._draft = None
        self.preview_result = None

    def _refresh_validation(self) -> ProfilePreparation:
        draft = self._require_draft()
        prepared = self._profiles.resolve_for_broker_with_draft(draft.broker_id, draft)
        errors = list(prepared.errors)
        if any(
            item.profile_id != draft.profile_id
            and item.normalized_name == draft.normalized_name
            for item in self._profiles.list_profiles(draft.broker_id)
        ):
            errors.append("A diagnostic profile with that name already exists.")
        self.validation = DraftValidation(tuple(errors))
        self.preview_result = None
        return ProfilePreparation(prepared.profile, prepared.expectations, tuple(errors))

    def _require_draft(self) -> DiagnosticProfile:
        if self._draft is None:
            raise RuntimeError("No diagnostic profile is selected.")
        return self._draft


def _copy_profile(profile: DiagnosticProfile) -> DiagnosticProfile:
    return replace(profile, pack_overrides={key: dict(value) for key, value in profile.pack_overrides.items()})
