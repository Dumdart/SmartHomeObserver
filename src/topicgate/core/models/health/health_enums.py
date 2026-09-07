from enum import StrEnum


class HealthSeverity(StrEnum):
    CRITICAL = "critical"


class ActionKind(StrEnum):
    LOG = "log"
    STORE_FAILURE = "store_failure"


class HealthStatus(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    PROBLEM = "problem"
