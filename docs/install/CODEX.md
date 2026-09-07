# Connect TopicGate to Codex

First [install TopicGate](OS_INSTALL.md), run `topicgate-gui`, and configure a broker.

Install the plugin:

```console
codex plugin marketplace add Dumdart/TopicGate
codex plugin add topicgate@topicgate
```

Start a new task. The plugin loads TopicGate skills and runs the MCP server in read-only mode.

To use only MCP:

```console
codex mcp add topicgate -- topicgate --mode read-only
```

Trusted environments may opt into control tools:

```console
codex mcp add topicgate-control -- topicgate --mode control
```

Control mode can change connections, subscriptions, observations, and device state. Confirm publish details before use.

For profile provisioning and health expectations, see [Control mode and health verification](CONTROL_AND_HEALTH.md), including restart, tool-exposure checks, and credential limitations.
