from enum import StrEnum


class HealthTransition(StrEnum):
    NEW_FAILURE = "new_failure"
    ONGOING_FAILURE = "ongoing_failure"
    RECOVERY = "recovery"
