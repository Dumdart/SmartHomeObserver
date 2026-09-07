from topicgate.core.interfaces.health_repositories import ExpectationFailureStore
from topicgate.core.models.health.health_action_context import HealthActionContext


class PersistFailureAction:
    def __init__(self, failures: ExpectationFailureStore) -> None:
        self._failures = failures

    def execute(self, context: HealthActionContext) -> None:
        if context.failure is not None:
            self._failures.upsert(context.failure)
