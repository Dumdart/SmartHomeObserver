from enum import StrEnum


class ConditionKind(StrEnum):
    EQUAL = "equal"
    IN_RANGE = "in_range"
    OUTSIDE = "outside"
    NUMERIC_RANGE = "numeric_range"
    TOPIC_EXISTS = "topic_exists"
    TOPIC_ABSENT = "topic_absent"
    FRESH_WITHIN = "fresh_within"
