import json
from datetime import datetime
from typing import Any

from topicgate.core.models.support_bundle import (
    RedactionManifest,
    SupportBroker,
    SupportBundle,
    SupportBundleArtifacts,
    SupportFinding,
    SupportHealth,
    SupportResultLimit,
    SupportTopicState,
    SupportTopicStateWithPayload,
)


SupportBundleResult = SupportBundleArtifacts


class SupportBundleRepresentation:
    """Render only the already-redacted support-bundle model."""

    def build_detailed_presentation(
        self,
        support_bundle: SupportBundle,
        manifest: RedactionManifest,
    ) -> SupportBundleArtifacts:
        return SupportBundleArtifacts(
            json=self.build_presentation_json(support_bundle),
            markdown=self.build_presentation_markdown(support_bundle),
            manifest=self.build_presentation_manifest(manifest),
            warnings=_bundle_warnings(support_bundle),
        )

    def build_presentation_json(self, support_bundle: SupportBundle) -> str:
        return _json(_bundle_dict(support_bundle))

    def build_presentation_markdown(self, support_bundle: SupportBundle) -> str:
        lines = [
            "# TopicGate support bundle",
            "",
            f"- Schema version: `{_markdown(support_bundle.schema_version)}`",
            f"- Bundle ID: `{_markdown(support_bundle.bundle_id)}`",
            f"- Generated: `{_datetime(support_bundle.generated_at)}`",
            f"- TopicGate: `{_markdown(support_bundle.application.version)}`",
            f"- Python: `{_markdown(support_bundle.application.python_version)}`",
            (
                "- Operating system: "
                f"`{_markdown(support_bundle.application.operating_system)}`"
            ),
            (
                "- Payloads included: "
                f"`{'yes' if support_bundle.payloads_included else 'no'}`"
            ),
            (
                f"- Brokers: `{support_bundle.broker_results.returned}` returned, "
                f"`{support_bundle.broker_results.omitted}` omitted"
            ),
            "",
            "## Diagnostics",
            "",
            (
                f"Returned `{support_bundle.diagnostic_results.returned}`; "
                f"omitted `{support_bundle.diagnostic_results.omitted}`."
            ),
            "",
        ]
        if support_bundle.diagnostics:
            lines.extend(("| Check | Status |", "| --- | --- |"))
            lines.extend(
                f"| {_markdown(check.name)} | {_markdown(check.status)} |"
                for check in support_bundle.diagnostics
            )
        else:
            lines.append("No preflight diagnostics were available.")

        for broker in support_bundle.brokers:
            lines.extend(_broker_markdown(broker))

        if support_bundle.limitations:
            lines.extend(("", "## Bundle limitations", ""))
            lines.extend(
                f"- `{_markdown(item)}`" for item in support_bundle.limitations
            )
        lines.append("")
        return "\n".join(lines)

    def build_presentation_manifest(self, manifest: RedactionManifest) -> str:
        return _json(
            {
                "schema_version": manifest.schema_version,
                "bundle_id": manifest.bundle_id,
                "policies": [
                    {
                        "category": policy.category,
                        "strategy": policy.strategy,
                        "occurrence_count": policy.occurrence_count,
                    }
                    for policy in manifest.policies
                ],
            }
        )


def _bundle_dict(bundle: SupportBundle) -> dict[str, Any]:
    return {
        "schema_version": bundle.schema_version,
        "bundle_id": bundle.bundle_id,
        "generated_at": _datetime(bundle.generated_at),
        "application": {
            "version": bundle.application.version,
            "python_version": bundle.application.python_version,
            "operating_system": bundle.application.operating_system,
        },
        "payloads_included": bundle.payloads_included,
        "diagnostics": [
            {"name": check.name, "status": check.status}
            for check in bundle.diagnostics
        ],
        "diagnostic_results": _result_limit_dict(bundle.diagnostic_results),
        "brokers": [_broker_dict(broker) for broker in bundle.brokers],
        "broker_results": _result_limit_dict(bundle.broker_results),
        "limitations": list(bundle.limitations),
    }


def _bundle_warnings(bundle: SupportBundle) -> tuple[str, ...]:
    warnings = [f"Bundle limitation: {item}." for item in bundle.limitations]
    if bundle.diagnostic_results.omitted:
        warnings.append(
            f"Diagnostics omitted by bounds: {bundle.diagnostic_results.omitted}."
        )
    if bundle.broker_results.omitted:
        warnings.append(
            f"Brokers omitted by bounds: {bundle.broker_results.omitted}."
        )
    for broker in bundle.brokers:
        warnings.extend(
            f"{broker.broker_alias} limitation: {item}."
            for item in broker.limitations
        )
        if broker.subscription_results.omitted:
            warnings.append(
                f"{broker.broker_alias} subscriptions omitted by bounds: "
                f"{broker.subscription_results.omitted}."
            )
        if broker.topic_results.omitted:
            warnings.append(
                f"{broker.broker_alias} topics omitted by bounds: "
                f"{broker.topic_results.omitted}."
            )
        for topic in broker.topics:
            if isinstance(topic, SupportTopicStateWithPayload):
                if topic.payload.ingestion_truncated:
                    warnings.append(
                        f"Payload for {topic.topic_alias} was truncated during "
                        "ingestion."
                    )
                if topic.payload.rendering_truncated:
                    warnings.append(
                        f"Payload for {topic.topic_alias} was truncated by export "
                        "bounds."
                    )
        if broker.health is None:
            continue
        if broker.health.omitted_count:
            warnings.append(
                f"{broker.broker_alias} health findings omitted by bounds: "
                f"{broker.health.omitted_count}."
            )
        if not broker.health.evidence_complete:
            warnings.append(
                f"{broker.broker_alias} health evidence is incomplete."
            )
        warnings.extend(
            f"Evidence for {finding.finding_alias} was truncated by export bounds."
            for finding in broker.health.findings
            if finding.evidence_truncated
        )
    return tuple(dict.fromkeys(warnings))


def _broker_dict(broker: SupportBroker) -> dict[str, Any]:
    return {
        "broker_alias": broker.broker_alias,
        "port": broker.port,
        "use_tls": broker.use_tls,
        "connection_status": broker.connection_status,
        "captured_at": _datetime(broker.captured_at),
        "subscriptions": [
            {
                "topic_alias": item.topic_alias,
                "qos": item.qos,
                "retain_as_published": item.retain_as_published,
                "retain_handling": item.retain_handling,
            }
            for item in broker.subscriptions
        ],
        "subscription_results": _result_limit_dict(
            broker.subscription_results
        ),
        "topics": [_topic_dict(item) for item in broker.topics],
        "topic_results": _result_limit_dict(broker.topic_results),
        "dropped_message_count": broker.dropped_message_count,
        "health": None if broker.health is None else _health_dict(broker.health),
        "limitations": list(broker.limitations),
    }


def _topic_dict(topic: SupportTopicState) -> dict[str, Any]:
    result = {
        "topic_alias": topic.topic_alias,
        "qos": topic.qos,
        "retain": topic.retain,
        "received_at": _datetime(topic.received_at),
        "age_seconds": topic.age_seconds,
        "message_count": topic.message_count,
        "source": topic.source,
        "status": topic.status,
    }
    if isinstance(topic, SupportTopicStateWithPayload):
        result["payload"] = {
            "encoding": topic.payload.encoding,
            "value": topic.payload.value,
            "original_size": topic.payload.original_size,
            "available_size": topic.payload.available_size,
            "rendered_size": topic.payload.rendered_size,
            "ingestion_truncated": topic.payload.ingestion_truncated,
            "rendering_truncated": topic.payload.rendering_truncated,
        }
    return result


def _result_limit_dict(result: SupportResultLimit) -> dict[str, int]:
    return {
        "limit": result.limit,
        "total": result.total,
        "returned": result.returned,
        "omitted": result.omitted,
    }


def _health_dict(health: SupportHealth) -> dict[str, Any]:
    return {
        "evaluated_at": _datetime(health.evaluated_at),
        "aggregate_status": health.aggregate_status,
        "observation_status": health.observation_status,
        "evidence_complete": health.evidence_complete,
        "active_failure_count": health.active_failure_count,
        "findings": [_finding_dict(item) for item in health.findings],
        "returned_count": health.returned_count,
        "omitted_count": health.omitted_count,
    }


def _finding_dict(finding: SupportFinding) -> dict[str, Any]:
    result = {
        "finding_alias": finding.finding_alias,
        "target": finding.target,
        "target_kind": finding.target_kind,
        "status": finding.status,
        "failure_code": finding.failure_code,
        "evidence_complete": finding.evidence_complete,
    }
    if finding.evidence_summary is not None:
        result["evidence_summary"] = finding.evidence_summary
        result["evidence_truncated"] = finding.evidence_truncated
    return result


def _broker_markdown(broker: SupportBroker) -> list[str]:
    lines = [
        "",
        f"## Broker `{_markdown(broker.broker_alias)}`",
        "",
        f"- Connection: `{_markdown(broker.connection_status)}`",
        f"- Port: `{broker.port}`",
        f"- TLS: `{'yes' if broker.use_tls else 'no'}`",
        f"- Captured: `{_datetime(broker.captured_at)}`",
        f"- Dropped messages: `{broker.dropped_message_count}`",
        (
            f"- Topics: `{broker.topic_results.returned}` returned, "
            f"`{broker.topic_results.omitted}` omitted"
        ),
        "",
        "### Subscriptions",
        "",
        (
            f"Returned `{broker.subscription_results.returned}`; "
            f"omitted `{broker.subscription_results.omitted}`."
        ),
        "",
    ]
    if broker.subscriptions:
        lines.extend(("| Topic | QoS |", "| --- | ---: |"))
        lines.extend(
            f"| `{_markdown(item.topic_alias)}` | {item.qos} |"
            for item in broker.subscriptions
        )
    else:
        lines.append("No subscriptions.")
    lines.extend(("", "### Current topics", ""))
    if broker.topics:
        lines.extend(
            (
                "| Topic | Status | Source | Age (seconds) |",
                "| --- | --- | --- | ---: |",
            )
        )
        lines.extend(
            (
                f"| `{_markdown(item.topic_alias)}` | {_markdown(item.status)} | "
                f"{_markdown(item.source)} | {item.age_seconds:.1f} |"
            )
            for item in broker.topics
        )
        for item in broker.topics:
            if isinstance(item, SupportTopicStateWithPayload):
                lines.extend(
                    (
                        "",
                        (
                            f"Payload for `{_markdown(item.topic_alias)}` "
                            f"({item.payload.encoding}, "
                            f"{item.payload.rendered_size} bytes):"
                        ),
                        "",
                        "```text",
                        item.payload.value.replace("```", "` ` `"),
                        "```",
                    )
                )
    else:
        lines.append("No current topics were available.")
    if broker.health is not None:
        lines.extend(_health_markdown(broker.health))
    if broker.limitations:
        lines.extend(("", "### Limitations", ""))
        lines.extend(f"- `{_markdown(item)}`" for item in broker.limitations)
    return lines


def _health_markdown(health: SupportHealth) -> list[str]:
    lines = [
        "",
        "### Health",
        "",
        f"- Aggregate status: `{_markdown(health.aggregate_status)}`",
        f"- Observation status: `{_markdown(health.observation_status)}`",
        f"- Evaluated: `{_datetime(health.evaluated_at)}`",
        (
            f"- Findings: `{health.returned_count}` returned, "
            f"`{health.omitted_count}` omitted"
        ),
        "",
    ]
    if not health.findings:
        lines.append("No health findings.")
        return lines
    lines.extend(
        (
            "| Finding | Target | Status | Code |",
            "| --- | --- | --- | --- |",
        )
    )
    lines.extend(
        (
            f"| `{_markdown(item.finding_alias)}` | `{_markdown(item.target)}` | "
            f"{_markdown(item.status)} | {_markdown(item.failure_code or '')} |"
        )
        for item in health.findings
    )
    for item in health.findings:
        if item.evidence_summary is not None:
            lines.extend(
                (
                    "",
                    f"- `{_markdown(item.finding_alias)}` evidence: "
                    f"{_markdown(item.evidence_summary)}",
                )
            )
    return lines


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _datetime(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
