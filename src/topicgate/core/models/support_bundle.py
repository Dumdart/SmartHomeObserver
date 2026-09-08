from dataclasses import dataclass
from datetime import datetime


SUPPORT_BUNDLE_SCHEMA_VERSION = "1.0"
MAX_SUPPORT_EVIDENCE_LENGTH = 2_000


@dataclass(frozen=True)
class SupportBundleOptions:
    include_payloads: bool = False
    broker_limit: int = 20
    subscription_limit: int = 100
    topic_limit: int = 100
    health_limit: int = 50
    evidence_limit: int = 500
    payload_limit_bytes: int = 16_384

    def __post_init__(self) -> None:
        if not isinstance(self.include_payloads, bool):
            raise ValueError("include_payloads must be a boolean.")
        _validate_integer("broker_limit", self.broker_limit, minimum=1, maximum=100)
        _validate_integer(
            "subscription_limit",
            self.subscription_limit,
            minimum=1,
            maximum=1_000,
        )
        _validate_integer("topic_limit", self.topic_limit, minimum=1, maximum=1_000)
        _validate_integer("health_limit", self.health_limit, minimum=1, maximum=200)
        _validate_integer(
            "evidence_limit",
            self.evidence_limit,
            minimum=0,
            maximum=MAX_SUPPORT_EVIDENCE_LENGTH,
        )
        _validate_integer(
            "payload_limit_bytes",
            self.payload_limit_bytes,
            minimum=0,
            maximum=16_384,
        )


@dataclass(frozen=True)
class SupportApplication:
    version: str
    python_version: str
    operating_system: str


@dataclass(frozen=True)
class SupportDiagnosticCheck:
    name: str
    status: str


@dataclass(frozen=True)
class SupportSubscription:
    topic_alias: str
    qos: int
    retain_as_published: bool
    retain_handling: int


@dataclass(frozen=True)
class SupportPayload:
    encoding: str
    value: str
    original_size: int
    available_size: int
    rendered_size: int
    ingestion_truncated: bool
    rendering_truncated: bool


@dataclass(frozen=True)
class SupportTopicState:
    topic_alias: str
    qos: int
    retain: bool
    received_at: datetime
    age_seconds: float
    message_count: int
    source: str
    status: str


@dataclass(frozen=True)
class SupportTopicStateWithPayload(SupportTopicState):
    payload: SupportPayload


@dataclass(frozen=True)
class SupportFinding:
    finding_alias: str
    target: str
    target_kind: str
    status: str
    failure_code: str | None
    evidence_complete: bool
    evidence_summary: str | None = None
    evidence_truncated: bool = False


@dataclass(frozen=True)
class SupportHealth:
    evaluated_at: datetime
    aggregate_status: str
    observation_status: str
    evidence_complete: bool
    active_failure_count: int
    findings: tuple[SupportFinding, ...]
    returned_count: int
    omitted_count: int


@dataclass(frozen=True)
class SupportResultLimit:
    limit: int
    total: int
    returned: int
    omitted: int


@dataclass(frozen=True)
class SupportBroker:
    broker_alias: str
    port: int
    use_tls: bool
    connection_status: str
    captured_at: datetime
    subscriptions: tuple[SupportSubscription, ...]
    subscription_results: SupportResultLimit
    topics: tuple[SupportTopicState | SupportTopicStateWithPayload, ...]
    topic_results: SupportResultLimit
    dropped_message_count: int
    health: SupportHealth | None
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class SupportBundle:
    schema_version: str
    bundle_id: str
    generated_at: datetime
    application: SupportApplication
    payloads_included: bool
    diagnostics: tuple[SupportDiagnosticCheck, ...]
    diagnostic_results: SupportResultLimit
    brokers: tuple[SupportBroker, ...]
    broker_results: SupportResultLimit
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class RedactionPolicy:
    category: str
    strategy: str
    occurrence_count: int


@dataclass(frozen=True)
class RedactionManifest:
    schema_version: str
    bundle_id: str
    policies: tuple[RedactionPolicy, ...]


@dataclass(frozen=True)
class SupportBundleArtifacts:
    json: str
    markdown: str
    manifest: str


def _validate_integer(
    name: str,
    value: int,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
