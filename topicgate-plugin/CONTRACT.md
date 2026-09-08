# TopicGate plugin contract

- Supports TopicGate 1.4+ for provisioning/health workflow additions; preserves MCP contract `1.0` public names and arguments. Discover tool exposure when using older 1.x servers.
- Uses Agent Plugins 1.0 contracts validated by the repository tests for Codex, Claude Code, GitHub Copilot, and Cursor.
- `.mcp.json` and `mcp.json` are read-only. Use `.mcp-control.json` only for intentional connection, subscription, observation, or publish changes.
- TopicGate Desktop owns credentials, retention, cache deletion, and complex maintenance.

## Additive tools

| Tool | Mode | Contract |
| --- | --- | --- |
| get_topic_history | Both | Passive observed events; opaque query-scoped cursor, fixed committed snapshot, limit 1..500; recording is per-broker opt-in in Desktop |
| create_broker | Control | Typed nonsecret settings; exact normalized-name/config reuse, otherwise conflict; no connect |
| list_health_expectations | Both | Passive definitions with UUID, revision, enabled state, limit 1..100, offset cursor |
| create_health_expectation | Control | Typed target/condition, metadata, coverage validation, new UUID |
| update_health_expectation | Control | Broker-scoped UUID and explicit nonnull changed fields |
| delete_health_expectation | Control | Broker-scoped deletion with active episode closure and retained history |
| get_health_report | Control, existing | Fresh evaluation and possible local transition writes; no activation/wait |
| query_failure_history | Both, existing | Passive bounded historical evidence |
| wait_for_broker_health | Control | One bounded wait using an already active connected broker; no reconnect/publish |
| get_support_bundle | Both | Passive bounded JSON/Markdown with manifest; no files, selectors, credentials, or payloads |

Requests, encodings, result fields, and workflow rules are detailed in [manage-mqtt-health](skills/manage-mqtt-health/SKILL.md). Only `critical` severity and `log`/`store_failure` actions are supported. MQTT expected values are bytes encoded as utf8/base64; broker connection statuses are text. Numeric bounds accept decimal strings without float conversion. No credential references or raw password inputs are supported. Username profiles without an existing UUID-bound credential return `needs_credentials`, with Desktop configuration as the resumable next step.

Mutations use the existing process lease and generation checks. Definition edits/deletion and incident updates now share a transaction; no schema migration is needed. Disable/enable retains revision and active history. Behavior edits advance revision; metadata does not. No optimistic client revision token or persistent provisioning idempotency key is introduced. Read back and compare exact definitions after uncertain writes; display names are not unique identifiers.

Creation uses the runtime, including repository initialization. Local profile persistence precedes runtime initialization; an initialization failure may leave a saved profile. Inspect by name/UUID after a failure, restart to reinitialize repositories if needed, then resume. This API never stores credentials, so it cannot partially write a new password. Existing Desktop credential storage is a separate operation with its existing error behavior. Do not overwrite conflicting settings on retry.

Wait holds a renewable control lease, not a database transaction, across asynchronous sleeps. It detects definition/profile/subscription changes and lease loss. The same monotonic deadline bounds evaluation checkpoints and sleeps. Synchronous database operations cannot be preempted mid-statement; an in-flight storage call can delay cancellation/timeout delivery, but an overdue evaluation cannot return satisfied. No worker thread continues evaluation after cancellation. A timeout returns the last complete report with its timestamp, or null/unknown/incomplete when none completed.

Success is computed over full evaluator results before presentation limits. A subset can satisfy while the full broker is problem, but incomplete full-scope evidence still prevents satisfaction. Findings are ordered problem, unknown, healthy and include omitted counts. Repeated scheduled evaluations retain ongoing occurrence counts; message-driven occurrences keep existing behavior. No prior broker is silently restored, cumulative error counters are never reset.

Default manifests/configs remain read-only. See [control setup](../docs/install/CONTROL_AND_HEALTH.md) and [scenarios](SCENARIOS.md). Never silently rewrite host capabilities.
