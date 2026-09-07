from typing import Protocol

from topicgate.core.models.health import ExpectationEvaluation
from topicgate.core.models.topic_message import TopicMessage


class HealthObservationSink(Protocol):
    def evaluate_observation(
        self,
        topic_msg: TopicMessage,
    ) -> tuple[ExpectationEvaluation, ...]: ...
