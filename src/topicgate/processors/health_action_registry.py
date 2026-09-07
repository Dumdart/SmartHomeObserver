from collections.abc import Mapping

from topicgate.core.interfaces.health_action import HealthAction
from topicgate.core.models.health import ActionKind


class HealthActionRegistry:
    def __init__(self, handlers: Mapping[ActionKind, HealthAction]) -> None:
        self._handlers = dict(handlers)

    def resolve(self, action_kind: ActionKind) -> HealthAction | None:
        return self._handlers.get(action_kind)
