---
name: manage-mqtt-health
description: Configure health expectations, evaluate evidence, and wait once for an active TopicGate broker to satisfy requested expectations.
---

# Configure and verify MQTT health

Discover actual tools once. `list_health_expectations` and `query_failure_history` are passive in both modes. Creation, update, deletion, `get_health_report`, and `wait_for_broker_health` require control mode. Evaluation may persist transitions and failure episodes; it does not activate or connect. Route missing capabilities to setup-topicgate.

Reuse explicit workflow authorization. Ask only for missing health definitions; never invent expected values, numeric thresholds, freshness ages, or observation windows. Use manage-mqtt to create/reuse a profile, activate once, and add/reuse covering subscriptions before creating topic expectations. Retain UUIDs. Explain once that the selected broker remains active. Never publish a test message to manufacture health.

## Definition contract

`create_health_expectation(broker, request)` requires name, description, target and condition. Targets are `{kind:"broker"}` or `{kind:"topic",topic:"devices/status"}`; topics must be concrete names, not wildcards. Conditions:

- `{kind:"equal",expected:{encoding:"utf8",value:"online"}}` compares MQTT bytes. `base64` decodes strictly to bytes; `text` is only for broker status such as `connected`.
- `{kind:"in"|"outside",expected:[{encoding:"utf8",value:"online"}]}` supports membership/exclusion with matching payload types.
- `{kind:"numeric_range",minimum:"18.125",maximum:"25.75"}` uses finite decimal strings and inclusive bounds.
- `{kind:"exists"}`, `{kind:"absent"}`, and `{kind:"freshness",max_age_seconds:30}` require topic targets. Freshness is observation age.

Only severity `critical` and actions `log`/`store_failure` exist. Defaults enable the rule and store failures. No executable actions. Listing uses limit 1..100 and an offset cursor; restart pagination if definitions change. Returned payload definitions use base64 for bytes, preserving type and value.

`update_health_expectation(broker,expectation_id,changes)` applies explicit nonnull fields, including enabled. Behavior edits advance the revision and close the previous episode atomically. Metadata/enablement edits preserve revision and active history. `delete_health_expectation` closes the active episode, deletes the definition, and retains history. Never identify an update by a nonunique display name. After an uncertain write, list and compare the complete definition before retrying; reuse exact matches and report conflicts.

## Verification workflow

1. Resolve capabilities and inputs once.
2. Create/reuse broker; activate once; add/reuse subscriptions.
3. Create missing expectations or update explicitly identified IDs.
4. Call `wait_for_broker_health` once; do not add an agent-side polling loop or repeatedly call `observe_broker_snapshot`.
5. Report outcome, actual domain health, evidence limitations, and persisted IDs. Query bounded failure history only for useful diagnosis.

Wait arguments: broker; optional nonempty unique required_expectation_ids; timeout_seconds default 30, 0.1..60; poll_interval_seconds default 1, 0.1..5 and no larger than timeout; stable_for_seconds default 0 and no larger than timeout; positive finite stale_after_seconds; limit 1..200. Use user-supplied stability/freshness requirements. The broker must already be active and connected. Zero/all-disabled expectations require configuration.

Outcomes are `satisfied`, `timed_out`, `disconnected`, or `configuration_changed`, distinct from domain `healthy`, `problem`, and `unknown`. Satisfaction requires complete evidence, healthy observation status, and every required enabled rule healthy for the stability interval. All enabled rules are evaluated before presentation limits. Subset success is labeled `required_subset` and still includes whole-broker health. Timeout preserves the last complete report and its timestamp; no report means evaluation did not complete, never healthy. Lease conflicts require retry after the other operation completes or restart when configuration is stale. Cancellation releases the lease. No repeated reconnection; disconnected outcomes need an intended reconnect before retry.

Present failed/unknown findings first, counts, start/end/evaluation timestamps, omitted findings, and evidence completeness. Do not dump payloads/history by default. Never infer health from a connection, zero active failures, old healthy state, or skipped evaluations. Absence means “not observed within TopicGate's observation scope,” not that a topic does not exist. `received_at` is observation time; retained messages do not prove publisher liveness.

For one new broker, S subscriptions and E expectations, target S + E + 3 domain calls after discovery. Extra inspection is justified for ambiguity or uncertain retries. Report completed IDs and the failed step; configuration and MQTT effects are not an atomic provisioning transaction.
