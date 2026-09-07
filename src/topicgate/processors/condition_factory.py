from topicgate.core.models.health.condition import EqualCondition
from topicgate.core.models.health.condition import InRangeCondition
from topicgate.core.models.health.condition import OutSideCondition
from topicgate.core.models.health.condition import PayloadValue
from topicgate.core.models.health.condition_kind import ConditionKind


class ConditionFactory:
    @staticmethod
    def build_condition(
        condition_kind: ConditionKind,
        values: tuple[PayloadValue, ...],
    ) -> EqualCondition | InRangeCondition | OutSideCondition:
        if condition_kind is ConditionKind.EQUAL:
            if len(values) != 1:
                raise ValueError("Equal conditions require exactly one value.")

            return EqualCondition(values[0])

        if condition_kind is ConditionKind.IN_RANGE:
            return InRangeCondition(values)

        if condition_kind is ConditionKind.OUTSIDE:
            return OutSideCondition(values)

        raise ValueError(f"Unsupported condition kind: {condition_kind}")
