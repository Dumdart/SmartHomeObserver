from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from topicgate.core.interfaces.diagnostic_pack import DiagnosticPackResolver, PackReference
from topicgate.core.models.diagnostic_profile import (
    DEFAULT_PROFILE_NAME,
    RULE_SCHEMA_VERSION,
    DiagnosticProfile,
    expectation_id_for_rule,
    normalize_rule_id,
)
from topicgate.core.models.health import ActionKind, HealthExpectation, HealthSeverity
from topicgate.infrastructure.database.mappers.health_expectation_mapper import HealthExpectationMapper


FORMAT_NAME = "topicgate.diagnostic-profile"
FORMAT_VERSION = 1
_PROFILE_FIELDS = {
    "kind", "format_version", "rule_schema_version", "name", "description",
    "pack", "pack_overrides", "custom_rules",
}
_OVERRIDE_FIELDS = {"enabled", "severity", "target", "condition", "actions", "name", "description"}


class DiagnosticProfileService:
    def __init__(
        self,
        profile_repository,
        health_expectation_repository,
        pack_resolver: DiagnosticPackResolver,
        transaction_manager,
        *,
        expectation_state_repository=None,
        expectation_failure_repository=None,
    ) -> None:
        self._profiles = profile_repository
        self._expectations = health_expectation_repository
        self._packs = pack_resolver
        self._transactions = transaction_manager
        self._states = expectation_state_repository
        self._failures = expectation_failure_repository

    def list_profiles(self, broker_id: UUID) -> tuple[DiagnosticProfile, ...]:
        return self._profiles.list_for_broker(broker_id)

    def get_profile(self, broker_id: UUID, profile_id: UUID) -> DiagnosticProfile:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise KeyError(f"Unknown diagnostic profile: {profile_id}")
        if profile.broker_id != broker_id:
            raise ValueError("The diagnostic profile does not belong to this broker.")
        return profile

    def create_profile(
        self,
        broker_id: UUID,
        name: str,
        *,
        description: str = "",
        pack_reference: PackReference | None = None,
        custom_rules: tuple[HealthExpectation, ...] = (),
        pack_overrides: dict[str, dict[str, Any]] | None = None,
        profile_id: UUID | None = None,
    ) -> DiagnosticProfile:
        now = datetime.now(timezone.utc)
        profile = DiagnosticProfile(
            profile_id=profile_id or uuid4(),
            broker_id=broker_id,
            name=name,
            description=description,
            pack_reference=pack_reference,
            custom_rules=custom_rules,
            pack_overrides=pack_overrides or {},
            created_at=now,
            updated_at=now,
        )
        return self.create(profile)

    def create(self, profile: DiagnosticProfile) -> DiagnosticProfile:
        canonical, resolved = self._validate_and_resolve(profile, new_identity=True)
        with self._transactions.transaction() as transaction:
            self._profiles.create(canonical, transaction=transaction)
            for expectation in resolved:
                self._expectations.create(expectation, transaction=transaction)
        return canonical

    def update_profile(self, broker_id: UUID, profile: DiagnosticProfile) -> DiagnosticProfile:
        current = self.get_profile(broker_id, profile.profile_id)
        if current.is_default and profile.name != DEFAULT_PROFILE_NAME:
            raise ValueError("The reserved default profile cannot be renamed.")
        candidate = replace(
            profile,
            broker_id=broker_id,
            is_default=current.is_default,
            created_at=current.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        canonical, desired = self._validate_and_resolve(candidate, new_identity=False)
        existing = {
            item.rule_id.casefold(): item
            for item in self._expectations.list_for_broker(broker_id)
            if item.profile_id == profile.profile_id
        }
        desired_by_id = {item.rule_id.casefold(): item for item in desired}
        with self._transactions.transaction() as transaction:
            self._profiles.update(canonical, transaction=transaction)
            for rule_id, old in existing.items():
                if rule_id not in desired_by_id:
                    self._close_active_failure(old, transaction)
                    self._expectations.delete(old.expectation_id, retain_history=True, transaction=transaction)
            for rule_id, item in desired_by_id.items():
                old = existing.get(rule_id)
                if old is None:
                    self._expectations.create(item, transaction=transaction)
                    continue
                behavior_changed = old.target != item.target or old.condition != item.condition
                updated = replace(
                    item,
                    expectation_id=old.expectation_id,
                    revision=old.revision + 1 if behavior_changed else old.revision,
                )
                if behavior_changed:
                    self._reset_state(old, updated.revision, transaction)
                self._expectations.update(updated, transaction=transaction)
        return canonical

    def delete_profile(self, broker_id: UUID, profile_id: UUID) -> None:
        profile = self.get_profile(broker_id, profile_id)
        if profile.is_default:
            raise ValueError("The reserved default profile cannot be deleted.")
        expectations = self.resolve_profile(profile)
        with self._transactions.transaction() as transaction:
            for expectation in expectations:
                self._close_active_failure(expectation, transaction)
            self._profiles.delete(profile_id, transaction=transaction)

    def resolve_profile(self, profile: DiagnosticProfile) -> tuple[HealthExpectation, ...]:
        _, resolved = self._validate_and_resolve(profile, new_identity=False)
        return resolved

    def resolve_for_broker(self, broker_id: UUID) -> tuple[HealthExpectation, ...]:
        return tuple(
            rule
            for profile in self.list_profiles(broker_id)
            for rule in self.resolve_profile(profile)
        )

    def export_profile(self, broker_id: UUID, profile_id: UUID) -> bytes:
        profile = self.get_profile(broker_id, profile_id)
        document: dict[str, Any] = {
            "kind": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "rule_schema_version": profile.rule_schema_version,
            "name": profile.name,
            "description": profile.description,
            "custom_rules": [
                self._rule_to_document(rule)
                for rule in sorted(profile.custom_rules, key=lambda item: normalize_rule_id(item.rule_id))
            ],
            "pack_overrides": {
                rule_id: self._portable_override(profile.pack_overrides[rule_id])
                for rule_id in sorted(profile.pack_overrides)
            },
        }
        if profile.pack_reference is not None:
            document["pack"] = {
                "pack_id": profile.pack_reference.pack_id,
                "version": profile.pack_reference.version,
            }
        return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

    def import_profile(self, target_broker_id: UUID, data: bytes | str) -> DiagnosticProfile:
        try:
            text = data.decode("utf-8") if isinstance(data, bytes) else data
            document = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Diagnostic profile import is not valid UTF-8 JSON.") from error
        if not isinstance(document, dict):
            raise ValueError("Diagnostic profile import must be a JSON object.")
        unknown = set(document) - _PROFILE_FIELDS
        if unknown:
            raise ValueError(f"Unknown diagnostic profile fields: {', '.join(sorted(unknown))}.")
        if document.get("kind") != FORMAT_NAME:
            raise ValueError("Unsupported diagnostic profile document kind.")
        if document.get("format_version") != FORMAT_VERSION:
            raise ValueError(f"Unsupported diagnostic profile format version: {document.get('format_version')!r}.")
        if document.get("rule_schema_version") != RULE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported diagnostic rule schema version: {document.get('rule_schema_version')!r}.")
        pack_value = document.get("pack")
        reference = None
        if pack_value is not None:
            if not isinstance(pack_value, dict) or set(pack_value) != {"pack_id", "version"}:
                raise ValueError("Diagnostic profile pack reference is malformed.")
            reference = PackReference(pack_value["pack_id"], pack_value["version"])
        raw_rules = document.get("custom_rules", [])
        raw_overrides = document.get("pack_overrides", {})
        if not isinstance(raw_rules, list) or not isinstance(raw_overrides, dict):
            raise ValueError("Diagnostic profile rules and overrides are malformed.")
        rules = tuple(self._rule_from_document(item, target_broker_id) for item in raw_rules)
        rebound_overrides = {
            rule_id: self._rebind_override(override, target_broker_id)
            for rule_id, override in raw_overrides.items()
        }
        return self.create_profile(
            target_broker_id,
            document.get("name"),
            description=document.get("description", ""),
            pack_reference=reference,
            custom_rules=rules,
            pack_overrides=rebound_overrides,
        )

    def _validate_and_resolve(self, profile: DiagnosticProfile, *, new_identity: bool) -> tuple[DiagnosticProfile, tuple[HealthExpectation, ...]]:
        pack_defaults: dict[str, HealthExpectation] = {}
        if profile.pack_reference is not None:
            pack = self._packs.resolve(profile.pack_reference)
            pack_defaults = {
                normalize_rule_id(item.rule_id): item
                for item in pack.build_expectations(profile.broker_id, profile.profile_id)
            }
        custom: list[HealthExpectation] = []
        custom_ids: set[str] = set()
        for rule in profile.custom_rules:
            rule_id = normalize_rule_id(rule.rule_id)
            if rule_id in custom_ids or rule_id in pack_defaults:
                raise ValueError(f"Diagnostic rule ID collides with another rule: {rule.rule_id}")
            if getattr(rule.target, "broker_id", None) != profile.broker_id:
                raise ValueError("Diagnostic rule target must belong to the profile broker.")
            custom_ids.add(rule_id)
            custom.append(replace(
                rule,
                expectation_id=expectation_id_for_rule(profile.profile_id, rule.rule_id),
                profile_id=profile.profile_id,
                rule_schema_version=profile.rule_schema_version,
                source_kind="custom",
                pack_override=None,
                revision=1 if new_identity else rule.revision,
            ))
        canonical_overrides: dict[str, dict[str, Any]] = {}
        pack_rules: list[HealthExpectation] = []
        for rule_id, override in profile.pack_overrides.items():
            normalized = normalize_rule_id(rule_id)
            if normalized not in pack_defaults:
                raise ValueError(f"Unknown diagnostic pack rule ID: {rule_id}")
            canonical_override = self._canonical_override(pack_defaults[normalized], override)
            if canonical_override:
                canonical_overrides[normalized] = canonical_override
        for normalized, base in pack_defaults.items():
            override = canonical_overrides.get(normalized, {})
            resolved = self._apply_override(base, override)
            if getattr(resolved.target, "broker_id", None) != profile.broker_id:
                raise ValueError("Diagnostic pack override target must belong to the profile broker.")
            pack_rules.append(replace(
                resolved,
                expectation_id=expectation_id_for_rule(profile.profile_id, base.rule_id),
                profile_id=profile.profile_id,
                source_kind="pack",
                pack_override=override,
                revision=1 if new_identity else resolved.revision,
            ))
        canonical = replace(profile, custom_rules=tuple(custom), pack_overrides=canonical_overrides)
        return canonical, tuple(sorted((*custom, *pack_rules), key=lambda item: normalize_rule_id(item.rule_id)))

    @staticmethod
    def _canonical_override(base: HealthExpectation, override: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(override, dict):
            raise ValueError("Diagnostic pack override must be an object.")
        unknown = set(override) - _OVERRIDE_FIELDS
        if unknown:
            raise ValueError(f"Unknown or immutable pack override fields: {', '.join(sorted(unknown))}.")
        canonical = dict(override)
        if "severity" in canonical:
            canonical["severity"] = HealthSeverity(canonical["severity"]).value
        if "actions" in canonical:
            if not isinstance(canonical["actions"], list):
                raise ValueError("Pack override actions must be a list.")
            canonical["actions"] = sorted(ActionKind(item).value for item in canonical["actions"])
        if "enabled" in canonical and not isinstance(canonical["enabled"], bool):
            raise ValueError("Pack override enabled must be a boolean.")
        if "target" in canonical:
            HealthExpectationMapper._dict_to_target(canonical["target"])
        if "condition" in canonical:
            HealthExpectationMapper._dict_to_condition(canonical["condition"])
        base_values = {
            "enabled": base.enabled,
            "severity": base.severity.value,
            "target": HealthExpectationMapper._target_to_dict(base.target),
            "condition": HealthExpectationMapper._condition_to_dict(base.condition),
            "actions": sorted(item.value for item in base.actions),
            "name": base.name,
            "description": base.description,
        }
        return {key: value for key, value in canonical.items() if value != base_values[key]}

    @staticmethod
    def _apply_override(base: HealthExpectation, override: dict[str, Any]) -> HealthExpectation:
        updates: dict[str, Any] = {}
        if "enabled" in override: updates["enabled"] = override["enabled"]
        if "severity" in override: updates["severity"] = HealthSeverity(override["severity"])
        if "target" in override: updates["target"] = HealthExpectationMapper._dict_to_target(override["target"])
        if "condition" in override: updates["condition"] = HealthExpectationMapper._dict_to_condition(override["condition"])
        if "actions" in override: updates["actions"] = frozenset(ActionKind(item) for item in override["actions"])
        for field in ("name", "description"):
            if field in override: updates[field] = override[field]
        return replace(base, **updates)

    @staticmethod
    def _rule_to_document(rule: HealthExpectation) -> dict[str, Any]:
        target = HealthExpectationMapper._target_to_dict(rule.target)
        target.pop("broker_id", None)
        return {
            "rule_id": rule.rule_id,
            "enabled": rule.enabled,
            "severity": rule.severity.value,
            "target": target,
            "condition": HealthExpectationMapper._condition_to_dict(rule.condition),
            "actions": sorted(item.value for item in rule.actions),
            "name": rule.name,
            "description": rule.description,
        }

    @staticmethod
    def _rule_from_document(value: Any, broker_id: UUID) -> HealthExpectation:
        allowed = {"rule_id", "enabled", "severity", "target", "condition", "actions", "name", "description"}
        if not isinstance(value, dict) or set(value) != allowed:
            raise ValueError("Custom diagnostic rule has missing or unknown fields.")
        target = value["target"]
        if not isinstance(target, dict):
            raise ValueError("Custom diagnostic rule target must be an object.")
        target = {**target, "broker_id": str(broker_id)}
        actions = value["actions"]
        if not isinstance(actions, list) or not isinstance(value["enabled"], bool):
            raise ValueError("Custom diagnostic rule values are malformed.")
        return HealthExpectation(
            expectation_id=uuid4(), revision=1, enabled=value["enabled"],
            severity=HealthSeverity(value["severity"]),
            target=HealthExpectationMapper._dict_to_target(target),
            condition=HealthExpectationMapper._dict_to_condition(value["condition"]),
            actions=frozenset(ActionKind(item) for item in actions),
            name=value["name"], description=value["description"], rule_id=value["rule_id"],
        )

    @staticmethod
    def _portable_override(override: dict[str, Any]) -> dict[str, Any]:
        result = dict(override)
        if "target" in result:
            result["target"] = dict(result["target"])
            result["target"].pop("broker_id", None)
        return result

    @staticmethod
    def _rebind_override(override: Any, broker_id: UUID) -> dict[str, Any]:
        if not isinstance(override, dict):
            raise ValueError("Diagnostic pack override must be an object.")
        result = dict(override)
        if "target" in result:
            if not isinstance(result["target"], dict):
                raise ValueError("Diagnostic pack override target must be an object.")
            result["target"] = {**result["target"], "broker_id": str(broker_id)}
        return result

    def _close_active_failure(self, expectation: HealthExpectation, transaction: object) -> None:
        if self._states is None or self._failures is None:
            return
        state = self._states.get(expectation.expectation_id, transaction=transaction)
        if state is None or state.active_failure_id is None:
            return
        failure = self._failures.get(state.active_failure_id, transaction=transaction)
        if failure is not None and failure.recovered_at is None:
            self._failures.upsert(replace(failure, recovered_at=datetime.now(timezone.utc)), transaction=transaction)

    def _reset_state(self, expectation: HealthExpectation, revision: int, transaction: object) -> None:
        self._close_active_failure(expectation, transaction)
        if self._states is None:
            return
        state = self._states.get(expectation.expectation_id, transaction=transaction)
        if state is not None:
            from topicgate.core.models.health import HealthStatus
            self._states.upsert(replace(state, expectation_revision=revision, current_status=HealthStatus.UNKNOWN, active_failure_id=None), transaction=transaction)
