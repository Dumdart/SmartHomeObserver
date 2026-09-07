import logging

from topicgate.core.models.health.health_action_context import HealthActionContext


logger = logging.getLogger("topicgate.health")


class LogHealthAction:
    def execute(self, context: HealthActionContext) -> None:
        logger.warning(
            "Health expectation %s transition=%s severity=%s failure=%s",
            context.expectation_id,
            context.transition.value,
            context.severity.value,
            None if context.failure is None else context.failure.failure_id,
        )
