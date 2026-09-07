# Control mode and expectation verification

TopicGate 1.4 adds profile creation, health expectation lifecycle tools, and bounded health waiting. The plugin remains read-only by default. `.mcp-control.json` is an example control entry; its presence does not select it in the plugin manifest.

Follow the existing host instructions for [Codex](CODEX.md), [Claude Code](CLAUDE_CODE.md), [Cursor](CURSOR.md), or [VS Code/Copilot](VSCODE_COPILOT.md). Codex and Claude guides show separate `topicgate-control` entries. Cursor uses `.cursor/mcp.json`; VS Code uses `.vscode/mcp.json` with `servers`. Set only the explicitly selected entry to `topicgate --mode control`, using the executable installed in that host's environment. Do not run multiple competing control processes against the same database. Do not edit cached plugin manifests as a durable setup method.

Restart the configured MCP server or host and open a new task after changing configuration or upgrading the package. Verify exposed tools: `activate_broker` means control tools are enabled; `list_health_expectations` should be available in both modes on 1.4+. Activation without the new creation/wait tools suggests an older executable or stale process. New listing without mutation tools indicates read-only mode. Package upgrades must use the interpreter that owns the host's `topicgate` executable. Never silently increase host capabilities.

Anonymous broker creation is supported. A username without stored credentials saves a profile with `status: needs_credentials`; configure that UUID in Desktop before continuing. No raw password or credential_ref is accepted. Existing credentials are keyed by profile UUID. Broker switching disconnects the current connection and leaves the selected broker active after the workflow. Publishing is a separate explicit action.

## Complete example

This example verifies the connection itself, not a publisher. Use additional topic/freshness conditions when those are part of the requested definition of health. The protocol regression runs these five calls against fake MQTT with isolated real storage:

```json
{"tool":"create_broker","arguments":{"request":{"name":"Lab","host":"localhost","port":1883,"username":"","use_tls":false}}}
{"tool":"activate_broker","arguments":{"broker_id":"<returned broker UUID>"}}
{"tool":"add_subscription","arguments":{"broker_id":"<returned broker UUID>","topic_filter":"devices/#"}}
{"tool":"create_health_expectation","arguments":{"broker":"<returned broker UUID>","request":{"name":"Connected","description":"The broker connection is established","target":{"kind":"broker"},"condition":{"kind":"equal","expected":{"encoding":"text","value":"connected"}}}}}
{"tool":"wait_for_broker_health","arguments":{"broker":"<returned broker UUID>"}}
```

The tested final result has `outcome: satisfied`, `scope: whole_broker`, `domain_status: healthy`, complete evidence, one enabled/evaluated expectation, one evaluation, no connection side effects, and `selected_broker_left_active: true`. Actual responses also include UUIDs, UTC timestamps, elapsed seconds, required revisions, bounded findings and omitted counts. This is an isolated test result, not evidence about your broker.

For an explicitly requested topic value, use target `{"kind":"topic","topic":"devices/status"}` and condition `{"kind":"equal","expected":{"encoding":"utf8","value":"online"}}` after a covering subscription exists. Do not publish to make it pass. Missing traffic times out with unknown/incomplete evidence; retained delivery does not establish publisher liveness. Use an explicitly requested freshness age when needed.

See the [plugin contract](../../topicgate-plugin/CONTRACT.md) for transaction semantics, retry/partial persistence, wait limits and synchronous storage cancellation limitations.

## Define checks in Desktop

Open **Health → Expectations** to configure a broker connection check. For a concrete topic, select it in the observer tree and open **Settings → Expectations**. Add a covering subscription before creating a topic expectation. The overview shows the evaluation result and evidence; history records failure episodes when the storage action is enabled.

![Sample broker expectation editor showing the connected condition and history actions.](../images/desktop-expectations.png)

*Sample configuration in Desktop; these screenshots are UI examples, not a live assessment of your broker.*

## Interpret a health wait

The default wait is 30 seconds, with a maximum of 60 seconds. It uses the existing active connection and leaves that broker active. Set `stable_for_seconds` when the requested checks must stay healthy for an interval, rather than pass at a single evaluation.

| Outcome | Meaning and next step |
| --- | --- |
| `satisfied` | The requested expectations passed with complete evidence. Check `scope`: a selected subset can pass while other broker checks fail. |
| `timed_out` | The criteria did not pass within the window. Inspect the timestamp and final report for failed, unknown, or missing evidence before deciding whether to wait again. |
| `disconnected` | The connection is unavailable. Diagnose it and explicitly reconnect before retrying. |
| `configuration_changed` | The broker, subscriptions, or expectations changed during verification. Inspect the current configuration before starting another wait. |

An empty or all-disabled expectation set cannot satisfy verification. Invalid bounds, missing IDs, lease conflicts, and storage errors are tool errors rather than health outcomes. Check the whole-broker report, evidence completeness, and omitted findings even when verifying a subset. A final report may be unavailable if no evaluation completed before the deadline.

![Sample health overview with failed and unknown checks and their evidence.](../images/desktop-health.png)

*Connection state and expectation health are separate. Limited observations can leave a check unknown even while the connection is established.*
