from decimal import Decimal, InvalidOperation

from topicgate.core.models.health.condition import Condition
from topicgate.core.models.health.condition import EqualCondition
from topicgate.core.models.health.condition import FreshnessCondition
from topicgate.core.models.health.condition import InRangeCondition
from topicgate.core.models.health.condition import NumericRangeCondition
from topicgate.core.models.health.condition import OutSideCondition
from topicgate.core.models.health.condition import PayloadValue
from topicgate.core.models.health.condition import TopicAbsentCondition
from topicgate.core.models.health.condition import TopicExistsCondition
from topicgate.core.models.health.condition_kind import ConditionKind


class ConditionFactory:
    @staticmethod
    def build_condition(
        condition_kind: ConditionKind,
        values: tuple[PayloadValue, ...],
    ) -> Condition:
        if condition_kind is ConditionKind.EQUAL:
            if len(values) != 1:
                raise ValueError("Equal conditions require exactly one value.")

            return EqualCondition(values[0])

        if condition_kind is ConditionKind.IN_RANGE:
            return InRangeCondition(values)

        if condition_kind is ConditionKind.OUTSIDE:
            return OutSideCondition(values)

        if condition_kind is ConditionKind.NUMERIC_RANGE:
            if len(values) != 2:
                raise ValueError(
                    "Numeric range conditions require a minimum and maximum."
                )
            return NumericRangeCondition(
                ConditionFactory._decimal(values[0]),
                ConditionFactory._decimal(values[1]),
            )

        if condition_kind is ConditionKind.TOPIC_EXISTS:
            if values:
                raise ValueError("Topic existence conditions do not take values.")
            return TopicExistsCondition()

        if condition_kind is ConditionKind.TOPIC_ABSENT:
            if values:
                raise ValueError("Topic absence conditions do not take values.")
            return TopicAbsentCondition()

        if condition_kind is ConditionKind.FRESH_WITHIN:
            if len(values) != 1:
                raise ValueError(
                    "Freshness conditions require one maximum age in seconds."
                )
            try:
                max_age_seconds = float(ConditionFactory._text(values[0]))
            except ValueError as error:
                raise ValueError(
                    "Freshness maximum age must be a number of seconds."
                ) from error
            return FreshnessCondition(max_age_seconds)

        raise ValueError(f"Unsupported condition kind: {condition_kind}")

    @staticmethod
    def _decimal(value: PayloadValue) -> Decimal:
        try:
            parsed = Decimal(ConditionFactory._text(value).strip())
        except (InvalidOperation, UnicodeDecodeError) as error:
            raise ValueError("Numeric range bounds must be numbers.") from error
        if not parsed.is_finite():
            raise ValueError("Numeric range bounds must be finite.")
        return parsed

    @staticmethod
    def _text(value: PayloadValue) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else value
