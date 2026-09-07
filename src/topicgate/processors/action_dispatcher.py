import logging

from topicgate.core.models.health import ActionKind
from topicgate.core.models.health.health_action_context import HealthActionContext
from topicgate.processors.health_action_registry import HealthActionRegistry


logger = logging.getLogger(__name__)


class ActionDispatcher:
    def __init__(self, registry: HealthActionRegistry) -> None:
        self._registry = registry

    def dispatch(
        self, action_kinds: frozenset[ActionKind], context: HealthActionContext
    ) -> None:
        for action_kind in action_kinds:
            handler = self._registry.resolve(action_kind)
            if handler is None:
                logger.warning(
                    "No health action handler is registered for %s.",
                    action_kind.value,
                )
                continue
            try:
                handler.execute(context)
            except Exception:
                logger.exception(
                    "Health action %s failed for expectation %s.",
                    action_kind.value,
                    context.expectation_id,
                )
