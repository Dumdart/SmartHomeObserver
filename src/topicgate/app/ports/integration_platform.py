from typing import Protocol

from topicgate.app.models.integration import (
    IntegrationPlan,
    IntegrationResult,
    IntegrationSpec,
    PlatformIntegrationState,
)


class IntegrationPlatform(Protocol):
    @property
    def name(self) -> str: ...

    def inspect(self) -> PlatformIntegrationState: ...

    def plan(self, desired: IntegrationSpec) -> IntegrationPlan: ...

    def apply(self, plan: IntegrationPlan) -> IntegrationResult: ...

    def remove(self) -> IntegrationResult: ...
