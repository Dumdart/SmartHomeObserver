from typing import Protocol

from topicgate.core.models.health.health_action_context import HealthActionContext


class HealthAction(Protocol):
    def execute(self, context: HealthActionContext) -> None: ...
