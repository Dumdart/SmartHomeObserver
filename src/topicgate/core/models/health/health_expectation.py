from dataclasses import dataclass
from typing import Any
from uuid import UUID

from topicgate.core.models.health.condition import Condition
from topicgate.core.models.health.condition import EqualCondition
from topicgate.core.models.health.condition import FreshnessCondition
from topicgate.core.models.health.condition import NumericRangeCondition
from topicgate.core.models.health.condition import TopicAbsentCondition
from topicgate.core.models.health.condition import TopicExistsCondition
from topicgate.core.models.health.expectation_failure import ExpectationFailure
from topicgate.core.models.health.expectation_state import ExpectationState
from topicgate.core.models.health.expectation_target import BrokerTarget
from topicgate.core.models.health.expectation_target import ExpectationTarget
from topicgate.core.models.health.expectation_target import TopicTarget
from topicgate.core.models.health.health_enums import ActionKind
from topicgate.core.models.health.health_enums import HealthSeverity
from topicgate.core.models.health.health_enums import HealthStatus


@dataclass
class HealthExpectation:
    expectation_id: UUID
    revision: int
    enabled: bool
    severity: HealthSeverity
    target: ExpectationTarget
    condition: Condition
    actions: frozenset[ActionKind]
    name: str = ""
    description: str = ""
    profile_id: UUID | None = None
    rule_id: str = ""
    rule_schema_version: int = 1
    source_kind: str = "custom"
    pack_override: dict[str, Any] | None = None


__all__ = [
    "ActionKind",
    "BrokerTarget",
    "Condition",
    "EqualCondition",
    "FreshnessCondition",
    "ExpectationFailure",
    "ExpectationState",
    "ExpectationTarget",
    "HealthExpectation",
    "HealthSeverity",
    "HealthStatus",
    "NumericRangeCondition",
    "TopicAbsentCondition",
    "TopicExistsCondition",
    "TopicTarget",
]
