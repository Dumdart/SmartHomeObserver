from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from topicgate.core.models.health.health_expectation import HealthExpectation

if TYPE_CHECKING:
    from topicgate.core.interfaces.diagnostic_pack import PackReference


DEFAULT_PROFILE_NAME = "Default"
RULE_SCHEMA_VERSION = 1
_RULE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def normalize_profile_name(name: str) -> tuple[str, str]:
    if not isinstance(name, str):
        raise ValueError("Diagnostic profile name must be a string.")
    display_name = unicodedata.normalize("NFKC", name.strip())
    if not 1 <= len(display_name) <= 200:
        raise ValueError("Diagnostic profile name must contain 1 to 200 characters.")
    if any(unicodedata.category(character) == "Cc" for character in display_name):
        raise ValueError("Diagnostic profile name must not contain control characters.")
    return display_name, display_name.casefold()


def normalize_rule_id(rule_id: str) -> str:
    if not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id):
        raise ValueError(
            "Diagnostic rule ID must contain 1 to 128 ASCII letters, digits, dots, "
            "underscores, or hyphens and begin with a letter or digit."
        )
    return rule_id.casefold()


def default_profile_id(broker_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"topicgate:diagnostic-profile:default:{broker_id}")


def expectation_id_for_rule(profile_id: UUID, rule_id: str) -> UUID:
    return uuid5(profile_id, normalize_rule_id(rule_id))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DiagnosticProfile:
    profile_id: UUID
    broker_id: UUID
    name: str
    description: str = ""
    pack_reference: PackReference | None = None
    rule_schema_version: int = RULE_SCHEMA_VERSION
    custom_rules: tuple[HealthExpectation, ...] = ()
    pack_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    is_default: bool = False
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    normalized_name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name, self.normalized_name = normalize_profile_name(self.name)
        self.description = self.description.strip()
        if self.rule_schema_version != RULE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported diagnostic rule schema version: {self.rule_schema_version}."
            )
        if self.is_default and self.name != DEFAULT_PROFILE_NAME:
            raise ValueError("The reserved default profile must be named 'Default'.")
        for timestamp in (self.created_at, self.updated_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("Diagnostic profile timestamps must be timezone-aware.")

        seen: set[str] = set()
        normalized_rules: list[HealthExpectation] = []
        for rule in self.custom_rules:
            normalized = normalize_rule_id(rule.rule_id)
            if normalized in seen:
                raise ValueError(f"Duplicate diagnostic rule ID: {rule.rule_id}")
            seen.add(normalized)
            normalized_rules.append(rule)
        normalized_overrides: dict[str, dict[str, Any]] = {}
        for rule_id, override in self.pack_overrides.items():
            normalized = normalize_rule_id(rule_id)
            if normalized in seen or normalized in normalized_overrides:
                raise ValueError(f"Duplicate diagnostic rule ID: {rule_id}")
            if not isinstance(override, dict):
                raise ValueError("Diagnostic pack overrides must be objects.")
            normalized_overrides[normalized] = dict(override)
        self.custom_rules = tuple(normalized_rules)
        self.pack_overrides = normalized_overrides
