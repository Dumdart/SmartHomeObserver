from enum import StrEnum


class ConditionKind(StrEnum):
    EQUAL = "equal"
    IN_RANGE = "in_range"
    OUTSIDE = "outside"
